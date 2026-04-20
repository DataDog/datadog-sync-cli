# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsGlobalVariables(BaseResource):
    resource_type = "synthetics_global_variables"
    resource_config = ResourceConfig(
        resource_connections={"synthetics_tests": ["parse_test_public_id"]},
        base_path="/api/v1/synthetics/variables",
        non_nullable_attr=["parse_test_public_id", "parse_test_options", "is_fido", "is_totp"],
        excluded_attributes=[
            "id",
            "creator",
            "last_error",
            "modified_at",
            "created_at",
            "parse_test_extracted_at",
            "created_by",
            "is_totp",
            "parse_test_name",
            "attributes",
            "editor",
        ],
        tagging_config=TaggingConfig(path="tags"),
        resource_mapping_key="name",
    )
    # Additional SyntheticsGlobalVariables specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)
        return resp["variables"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _inject_secret_value(self, _id: str, resource: Dict) -> None:
        """Fetch the clear (unobfuscated) secret value from the source and inject it
        into the resource dict. The value is only held in memory and never persisted
        to state files."""
        if "value" not in resource.get("value", {}):
            try:
                clear = await self.config.source_client.get(self.resource_config.base_path + f"/{_id}/clear")
                resource.setdefault("value", {})["value"] = clear["value"]["value"]
            except (CustomClientHTTPError, KeyError):
                self.config.logger.warning(f"Failed to inject secret value for global variable {_id}")
                resource.setdefault("value", {})["value"] = "SECRET"

    @staticmethod
    def _strip_secret_value(resp: Dict) -> None:
        """Remove the secret value from the response so it is not saved to state files."""
        if resp.get("value", {}).get("secure"):
            resp["value"].pop("value", None)

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        key = self.get_resource_mapping_key(resource)
        if key and key in self._existing_resources_map:
            self.config.state.destination[self.resource_type][_id] = self._existing_resources_map[key]
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client

        await self._inject_secret_value(_id, resource)

        if "is_fido" in resource and resource["is_fido"]:
            resource.pop("value")

        resp = await destination_client.post(self.resource_config.base_path, resource)
        self._strip_secret_value(resp)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        await self._inject_secret_value(_id, resource)

        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )
        self._strip_secret_value(resp)

        r = self.config.state.destination[self.resource_type][_id]
        r.update(resp)

        return _id, r

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        resources = self.config.state.destination[resource_to_connect]
        failed_connections = []
        found = False
        for k, v in resources.items():
            if k.startswith(r_obj[key]):
                r_obj[key] = v["public_id"]
                found = True
                break
        if not found:
            failed_connections.append(r_obj[key])
        return failed_connections
