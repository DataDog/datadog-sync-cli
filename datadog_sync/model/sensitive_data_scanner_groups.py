# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SensitiveDataScannerGroups(BaseResource):
    resource_type = "sensitive_data_scanner_groups"
    resource_config = ResourceConfig(
        non_nullable_attr=[],
        base_path="/api/v2/sensitive-data-scanner/config",
        excluded_attributes=[
            "id",
            "relationships",
        ],
        concurrent=False,
    )
    # Additional SensitiveDataScannerGroups specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return [r for r in resp.get("included", []) if r["type"] == "sensitive_data_scanner_group"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            groups = await self.get_resources(source_client)
            found = False
            for group in groups:
                if group["id"] == _id:
                    resource = group
                    found = True
                    break
            if not found:
                raise Exception(f"Group with id {_id} not found")

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource, "meta": {}}
        resp = await destination_client.post(self.resource_config.base_path + "/groups", payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        payload = {"data": resource, "meta": {}}
        await destination_client.patch(
            self.resource_config.base_path + f"/groups/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        payload = {"meta": {}}
        await destination_client.delete(
            self.resource_config.base_path + f"/groups/{self.config.state.destination[self.resource_type][_id]['id']}",
            body=payload,
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
