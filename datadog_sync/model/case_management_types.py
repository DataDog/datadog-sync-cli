# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class CaseManagementTypes(BaseResource):
    """Case management case types (``/api/v2/cases/types``).

    API shape (verified against the published API schema):

    - ``GET /api/v2/cases/types`` is a single, unpaginated list response.
      There is **no per-id GET** endpoint, so targeted import by id is
      list-and-filter: the id lookup below lists the endpoint once (cached,
      lock-guarded) and selects the requested entry. ``ResourcesHandler.
      _force_missing_dep_import_cb`` relies on this path when
      ``--force-missing-dependencies`` resolves a missing dependency
      referenced by ``case_management_attributes``.
    - ``attributes.deleted_at`` is a tombstone marker: a null/absent value
      means the case type is active. Tombstoned types are skipped at import
      and excluded from destination mapping, so a deleted destination type
      never shadows a same-named active source type (the source type is
      recreated instead).

    Identity/adoption is ``attributes.name``, ambiguity-refused
    (implementation contract ``docs/case-management-support.md`` §3.1,
    PR #732): a name match against the destination hydrates
    ``state.destination`` and delegates to update; a miss creates a new case
    type. When two ACTIVE destination types share a name, adoption is
    refused (the name is excluded from the mapping with a warning — never a
    silent pick). Case types are not a mapping resource for ``--id-file``
    — the allowlists in ``configuration.py`` intentionally exclude this
    type.
    """

    resource_type = "case_management_types"
    resource_config = ResourceConfig(
        base_path="/api/v2/cases/types",
        excluded_attributes=[
            "id",
            "attributes.deleted_at",
        ],
        resource_mapping_key="attributes.name",
    )

    # Cache for the list-backed id lookup (see import_resource). Populated
    # lazily on the first --force-missing-dependencies id import and reused
    # for the rest of the run. Guarded by a lock because the force-missing
    # import callback runs inside the worker pool.
    _types_cache: Optional[Dict[str, Dict]]
    _types_cache_lock: Optional[asyncio.Lock]

    def __init__(self, config) -> None:
        super().__init__(config)
        self._types_cache = None
        self._types_cache_lock = None

    async def init_async(self) -> None:
        """Create the cache lock under the loop that will use it.

        asyncio primitives bind to a running loop at (re)construction time on
        some versions, so the lock is (re)created here rather than in
        __init__, matching ResourceConfig.init_async's convention.
        """
        await super().init_async()
        self._types_cache_lock = asyncio.Lock()

    @staticmethod
    def _is_tombstoned(resource: Dict) -> bool:
        """A case type is tombstoned when attributes.deleted_at is set."""
        return bool((resource.get("attributes") or {}).get("deleted_at"))

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def _types_by_id(self) -> Dict[str, Dict]:
        """List source case types once and index them by id.

        Lazily cached behind a lock so a worker-pool fan-out of
        force-missing-dependency imports performs one GET for the whole run
        rather than one per requested id.
        """
        if self._types_cache_lock is None:
            # Defensive: init_async() should have created the lock; construct
            # it here (already inside a running loop) when it has not.
            self._types_cache_lock = asyncio.Lock()
        async with self._types_cache_lock:
            if self._types_cache is None:
                resp = await self.config.source_client.get(self.resource_config.base_path)
                self._types_cache = {t["id"]: t for t in resp["data"]}
        return self._types_cache

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if resource is not None:
            if self._is_tombstoned(resource):
                raise SkipResource(resource.get("id", _id), self.resource_type, "Case type is deleted (tombstoned).")
            return resource["id"], resource

        # Targeted import by id: no per-id GET exists, so list-and-filter.
        types_by_id = await self._types_by_id()
        matched = types_by_id.get(_id)
        if matched is None:
            raise SkipResource(_id, self.resource_type, "Case type not found in source list.")
        if self._is_tombstoned(matched):
            raise SkipResource(_id, self.resource_type, "Case type is deleted (tombstoned).")
        return _id, matched

    async def map_existing_resources(self) -> None:
        """Build the destination mapping from ACTIVE types only, refusing
        ambiguous identities.

        - A tombstoned destination type must not shadow a same-named active
          source type: if it did, the source type would be PUT against a
          deleted destination type instead of being (re)created.
        - Ambiguity-refused identity (implementation contract §3.1): when two
          ACTIVE destination types share a name, adoption is refused — the
          name is excluded from the mapping with a warning, never resolved by
          silently picking one candidate. The source type then takes the
          create path, where the API's own duplicate-name handling surfaces
          as a per-resource error (disaster-recovery: continue past it).
        """
        dest_resources = await self.get_resources(self.config.destination_client)
        candidates: Dict[str, Dict] = {}
        ambiguous: set = set()
        for resource in dest_resources:
            if self._is_tombstoned(resource):
                self.config.logger.debug(
                    "excluding tombstoned destination case type from mapping: %s",
                    self.get_resource_mapping_key(resource),
                    resource_type=self.resource_type,
                )
                continue
            key = self.get_resource_mapping_key(resource)
            if key is None:
                continue
            if key in candidates:
                # Two active destination types claim the same name: refuse
                # the ambiguous adoption entirely.
                ambiguous.add(key)
                continue
            candidates[key] = resource
        for key in sorted(ambiguous):
            self.config.logger.warning(
                "refusing ambiguous case type name adoption: name %r is claimed by multiple "
                "active destination case types; treating as no-match",
                key,
                resource_type=self.resource_type,
            )
            candidates.pop(key, None)
        self._existing_resources_map = candidates

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        key = self.get_resource_mapping_key(resource)
        if key and key in self._existing_resources_map:
            # Name match at the destination: hydrate state.destination so the
            # framework's diff accounting sees the adopted resource, then
            # update in place (teams pattern).
            self.config.state.destination[self.resource_type][_id] = self._existing_resources_map[key]
            return await self.update_resource(_id, resource)

        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            {"data": resource},
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
