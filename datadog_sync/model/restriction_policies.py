# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
import json
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


# Supported id prefixes for bulk-source entries. Must match the target types
# enumerated in get_resources and remapped in connect_id; an entry outside this
# set would import cleanly but never get its source id remapped to the destination
# id at apply time, producing a silent miscompare.
_SUPPORTED_BULK_ID_PREFIXES = frozenset({"dashboard", "notebook", "slo"})


class RestrictionPolicies(BaseResource):
    resource_type = "restriction_policies"
    resource_config = ResourceConfig(
        resource_connections={
            # Primary ID connections
            "dashboards": ["id"],
            "service_level_objectives": ["id"],
            "notebooks": ["id"],
            # # TODO: Commented out until security rules are supported
            # "security_rules": ["id"],
            # Bindings connections
            "users": ["attributes.bindings.principals"],
            "roles": ["attributes.bindings.principals"],
            "teams": ["attributes.bindings.principals"],
        },
        base_path="/api/v2/restriction_policy",
        excluded_attributes=[],
        skip_resource_mapping=True,
    )
    # Additional RestrictionPolicies specific attributes
    current_user_path: str = "/api/v2/current_user"
    org_principal: Optional[str] = None

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # Bulk-source short-circuit: when --restriction-policies-bulk-source is set,
        # read prefetched bodies from disk and skip enumeration of dashboards/notebooks/SLOs.
        # Each body is a full per-ID GET response shape ({"id","type","attributes"}),
        # detected downstream in import_resource by attributes.bindings being a list.
        # File I/O is dispatched to a worker thread so the asyncio loop is not blocked
        # while the producer-supplied JSON is parsed (size is bounded only by the producer).
        bulk_source = self.config.restriction_policies_bulk_source
        if bulk_source:
            return await asyncio.to_thread(self._load_bulk_source, bulk_source)

        policies = []

        dashboards = await self.config.resources["dashboards"].get_resources(client)
        notebooks = await self.config.resources["notebooks"].get_resources(client)
        slos = await self.config.resources["service_level_objectives"].get_resources(client)
        # # TODO: Commented out until security rules are supported
        # security_rules = self.config.resources["security_rules"].get_resources(client)

        if dashboards and len(dashboards) > 0:
            for dashboard in dashboards:
                policies.append(
                    {
                        "id": f"dashboard:{dashboard['id']}",
                    }
                )
        if notebooks and len(notebooks) > 0:
            for notebook in notebooks:
                policies.append(
                    {
                        "id": f"notebook:{notebook['id']}",
                    }
                )
        if slos and len(slos) > 0:
            for slo in slos:
                policies.append(
                    {
                        "id": f"slo:{slo['id']}",
                    }
                )
        # # TODO: Commented out until security rules are supported
        # if security_rules and len(security_rules) > 0:
        #     for rule in security_rules:
        #         policies.append({
        #             "id": f"security-rule:{rule['id']}",
        #         })

        return policies

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        # Bulk-source short-circuit: if get_resources already supplied a full body
        # (detected by attributes.bindings being a list — guaranteed by _validate_bulk_body
        # for bulk entries and absent from the legacy LIST stub which is just {"id": "..."}),
        # return it without issuing a per-ID GET. The stronger discriminator (bindings is a
        # list, not just attributes is a dict) is intentional: it pins the short-circuit to
        # the exact shape the validator produces and prevents accidental engagement if a
        # future change ever populates `attributes` on the legacy LIST stub.
        # SkipResource on empty-bindings is applied identically to the GET path so the
        # prefetched and live paths produce the same observable behavior.
        if (
            resource is not None
            and isinstance(resource.get("attributes"), dict)
            and isinstance(resource["attributes"].get("bindings"), list)
        ):
            import_id = resource["id"]
            if not resource["attributes"]["bindings"]:
                raise SkipResource(import_id, self.resource_type, "Resource does not have any bindings.")
            return import_id, resource

        source_client = self.config.source_client
        import_id = _id or resource["id"]

        try:
            resource = await source_client.get(self.resource_config.base_path + f"/{import_id}")
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                raise SkipResource(import_id, self.resource_type, "Resource does not exist.")
            else:
                raise e

        if not resource["data"]["attributes"]["bindings"]:
            raise SkipResource(import_id, self.resource_type, "Resource does not have any bindings.")

        return import_id, resource["data"]

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if self.org_principal:
            for binding in resource["attributes"]["bindings"]:
                for i, key in enumerate(binding["principals"]):
                    if key.startswith("org:"):
                        binding["principals"][i] = self.org_principal
                        break

    async def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        try:
            resp = await destination_client.get(self.current_user_path)
            org_id = resp["data"]["relationships"]["org"]["data"]["id"]
            self.org_principal = f"org:{org_id}"
        except Exception as e:
            self.config.logger.error(f"Failed to get org details: {e}")
            raise

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}

        # Add query parameter if allow_self_lockout is enabled
        params = {}
        if self.config.allow_self_lockout:
            params["allow_self_lockout"] = "true"

        resp = await destination_client.post(
            self.resource_config.base_path + f"/{resource_id}", payload, params=params if params else None
        )

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}

        # Add query parameter if allow_self_lockout is enabled
        params = {}
        if self.config.allow_self_lockout:
            params["allow_self_lockout"] = "true"

        resp = await destination_client.post(
            self.resource_config.base_path + f"/{resource_id}", payload, params=params if params else None
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        dashboards = self.config.state.destination["dashboards"]
        slos = self.config.state.destination["service_level_objectives"]
        notebooks = self.config.state.destination["notebooks"]
        users = self.config.state.destination["users"]
        roles = self.config.state.destination["roles"]
        teams = self.config.state.destination["teams"]

        failed_connections = []
        if key == "id":
            _type, _id = r_obj[key].split(":", 1)
            if resource_to_connect == "dashboards" and _type == "dashboard":
                if _id in dashboards:
                    r_obj[key] = f"dashboard:{dashboards[_id]['id']}"
                else:
                    failed_connections.append(_id)
            elif resource_to_connect == "service_level_objectives" and _type == "slo":
                if _id in slos:
                    r_obj[key] = f"slo:{slos[_id]['id']}"
                else:
                    failed_connections.append(_id)
            elif resource_to_connect == "notebooks" and _type == "notebook":
                if _id in notebooks:
                    r_obj[key] = f"notebook:{notebooks[_id]['id']}"
                else:
                    failed_connections.append(_id)

        if key == "principals":
            for i, policy_id in enumerate(r_obj[key]):
                _type, _id = policy_id.split(":", 1)

                if resource_to_connect == "users" and _type == "user":
                    if _id in users:
                        r_obj[key][i] = f"user:{users[_id]['id']}"
                    else:
                        failed_connections.append(_id)
                elif resource_to_connect == "roles" and _type == "role":
                    if _id in roles:
                        r_obj[key][i] = f"role:{roles[_id]['id']}"
                    else:
                        failed_connections.append(_id)
                elif resource_to_connect == "teams" and _type == "team":
                    if _id in teams:
                        r_obj[key][i] = f"team:{teams[_id]['id']}"
                    else:
                        failed_connections.append(_id)

        return failed_connections

    def _load_bulk_source(self, path: str) -> List[Dict]:
        """Load prefetched restriction_policy bodies from a JSON file.

        Returns the list as-is for downstream consumption by import_resource.
        Raises RuntimeError on missing file, invalid JSON, wrong top-level shape, or
        per-entry shape violations. The file is producer-supplied and the per-entry
        shape must match the per-ID GET response body — any deviation indicates a
        producer bug and is rejected at load time so a malformed body cannot reach
        import_resource and surface as a non-actionable error downstream.

        Validated shape per entry:
          id:         non-empty str of the form "<type>:<resource-id>", with
                       type one of {"dashboard","notebook","slo"} (the set
                       enumerated by get_resources and remapped by connect_id)
          type:       literal "restriction_policy"
          attributes: dict
          attributes.bindings: list (empty list is valid — SkipResource is applied
                       in import_resource, matching the live per-ID GET path)
        """
        try:
            with open(path, "r") as f:
                bodies = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"--restriction-policies-bulk-source: failed to load {path!r}: {e}"
            ) from e
        if not isinstance(bodies, list):
            raise RuntimeError(
                f"--restriction-policies-bulk-source: expected JSON array at {path!r}, "
                f"got {type(bodies).__name__}"
            )
        seen: Dict[str, int] = {}
        for i, body in enumerate(bodies):
            self._validate_bulk_body(body, i, path)
            body_id = body["id"]
            if body_id in seen:
                raise RuntimeError(
                    f"--restriction-policies-bulk-source: duplicate \"id\" {body_id!r} at "
                    f"entries {seen[body_id]} and {i} in {path!r} — each policy id must "
                    f"appear at most once (state writes are last-wins and would silently "
                    f"drop earlier entries)"
                )
            seen[body_id] = i
        return bodies

    @staticmethod
    def _validate_bulk_body(body, index: int, path: str) -> None:
        prefix = f"--restriction-policies-bulk-source: entry {index} at {path!r}"
        if not isinstance(body, dict):
            raise RuntimeError(f"{prefix} must be a JSON object, got {type(body).__name__}")
        body_id = body.get("id")
        if not isinstance(body_id, str) or not body_id:
            raise RuntimeError(f'{prefix} must have a non-empty string "id"')
        type_prefix, sep, resource_id_part = body_id.partition(":")
        if not sep or not type_prefix or not resource_id_part:
            raise RuntimeError(
                f'{prefix} has malformed "id" {body_id!r}: '
                'expected "<type>:<resource-id>" with both sides non-empty '
                '(e.g. "dashboard:abc-123")'
            )
        if type_prefix not in _SUPPORTED_BULK_ID_PREFIXES:
            raise RuntimeError(
                f'{prefix} has unsupported "id" prefix {type_prefix!r}: '
                f"supported prefixes are {sorted(_SUPPORTED_BULK_ID_PREFIXES)}"
            )
        body_type = body.get("type")
        if body_type != "restriction_policy":
            raise RuntimeError(
                f'{prefix} must have "type" == "restriction_policy", got {body_type!r}'
            )
        attributes = body.get("attributes")
        if not isinstance(attributes, dict):
            raise RuntimeError(
                f'{prefix} must have "attributes" as an object, '
                f"got {type(attributes).__name__}"
            )
        bindings = attributes.get("bindings")
        if not isinstance(bindings, list):
            raise RuntimeError(
                f'{prefix} must have "attributes.bindings" as an array '
                "(empty array is valid), "
                f"got {type(bindings).__name__}"
            )
