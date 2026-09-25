# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMPermanentRetentionFilters(BaseResource):
    """Permanent RUM retention filters (configure-only).

    Permanent retention filters are system-defined with fixed ids
    (``rum_apm_flat_sampling``, ``synthetics_sessions``, ``forced_replay_sessions``)
    identical across orgs, so the filter id needs no remapping — only the parent
    application id does. The endpoint set is PATCH-only (no POST/DELETE), so
    ``create_resource`` delegates to ``update_resource`` and ``delete_resource``
    is a no-op, mirroring ``logs_archives_order``.

    Because the filter ids are fixed and repeat under every application, the
    state key is a **composite** ``"{application_id}:{filter_id}"`` to avoid
    collisions when multiple applications are synced. The payload ``data.id``
    remains the server filter id.

    Like ``rum_retention_filters``, the application id is not part of the filter
    body, so a synthetic ``_application_id`` is injected during enumeration and
    remapped via ``resource_connections``. It is kept out of ``excluded_attributes``
    (must survive ``prep_resource``) and excluded from diffs via
    ``deep_diff_config.exclude_regex_paths``.
    """

    resource_type = "rum_permanent_retention_filters"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/applications",
        excluded_attributes=[
            "attributes.name",
            "attributes.description",
            "attributes.editability",
        ],
        resource_connections={
            "rum_applications": ["_application_id"],
        },
        deep_diff_config={
            "ignore_order": True,
            "exclude_regex_paths": [r".*\['_application_id'\]"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMPermanentRetentionFilters specific attributes
    _applications_path = "/api/v2/rum/applications"

    @staticmethod
    def _composite_key(application_id: str, filter_id: str) -> str:
        return f"{application_id}:{filter_id}"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        apps = (await client.get(self._applications_path))["data"]
        resources: List[Dict] = []
        for app in apps:
            app_id = app["id"]
            resp = await client.get(f"{self._applications_path}/{app_id}/retention_filters/permanent")
            for f in resp["data"]:
                f["_application_id"] = app_id
                resources.append(f)
        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            # The {permanent_rf_id} GET is parent-scoped; search apps for it.
            # Only used by --id-file (not allowlisted for this type).
            # _id may be a composite key "{app_id}:{filter_id}" or just a
            # filter id (legacy). When it's a composite key, look up directly.
            source_client = self.config.source_client
            if ":" in _id:
                app_id, filter_id = _id.split(":", 1)
                resp = await source_client.get(
                    f"{self._applications_path}/{app_id}/retention_filters/permanent/{filter_id}"
                )
                resource = resp["data"]
                resource["_application_id"] = app_id
            else:
                apps = (await source_client.get(self._applications_path))["data"]
                resource = None
                for app in apps:
                    app_id = app["id"]
                    resp = await source_client.get(f"{self._applications_path}/{app_id}/retention_filters/permanent")
                    for f in resp["data"]:
                        if f["id"] == _id:
                            f["_application_id"] = app_id
                            resource = f
                            break
                    if resource:
                        break
                if resource is None:
                    raise Exception(f"rum_permanent_retention_filter {_id} not found in any application")

        resource = resource  # type: ignore[assignment]
        return self._composite_key(resource["_application_id"], resource["id"]), resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # No POST endpoint — permanent filters are system-provisioned with fixed
        # ids. Delegate to update (configure the cross_product_sampling).
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        app_id = resource.pop("_application_id", None)
        # permanent filter ids are fixed across orgs; the filter id in the
        # resource IS the destination filter id. The app_id was remapped by
        # connect_resources to the destination app id.
        filter_id = resource["id"]
        payload = {"data": resource}
        resp = await destination_client.patch(
            f"{self._applications_path}/{app_id}/retention_filters/permanent/{filter_id}",
            payload,
        )
        data = resp["data"]
        data["_application_id"] = app_id
        return _id, data

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning(
            "rum_permanent_retention_filters cannot be deleted. Removing resource from state only."
        )
