# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SensitiveDataScannerRules(BaseResource):
    resource_type = "sensitive_data_scanner_rules"
    resource_config = ResourceConfig(
        base_path="/api/v2/sensitive-data-scanner/config",
        excluded_attributes=[
            "id",
        ],
        resource_connections={"sensitive_data_scanner_groups": ["relationships.group.data.id"]},
        concurrent=False,
    )
    # Additional SensitiveDataScannerRules specific attributes
    standard_pattern_path = "/api/v2/sensitive-data-scanner/standard-patterns"
    source_standard_pattern_mapping: Dict = {}  # pattern_id -> pattern_name
    destination_standard_pattern_mapping: Dict = {}  # pattern_name -> pattern_id

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return [r for r in resp.get("included", []) if r["type"] == "sensitive_data_scanner_rule"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        if not self.source_standard_pattern_mapping:
            # Populate the standard pattern mapping
            try:
                std_patterns = (await source_client.get(self.resource_config.base_path + "/standard-patterns"))["data"]
                for pattern in std_patterns:
                    self.source_standard_pattern_mapping[pattern["id"]] = pattern["attributes"]["name"]
            except Exception as e:
                self.config.logger.warning("error retrieving standard patterns: %s", e)

        if _id:
            resource = await source_client.get(self.resource_config.base_path + f"/rules/{_id}")

        if _std_id := resource.get("relationships", {}).get("standard_pattern", {}).get("data", {}).get("id"):
            resource["relationships"]["standard_pattern"]["data"]["id"] = self.source_standard_pattern_mapping.get(
                _std_id, _std_id
            )

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if name := resource.get("relationships", {}).get("standard_pattern", {}).get("data", {}).get("id"):
            resource["relationships"]["standard_pattern"]["data"]["id"] = self.destination_standard_pattern_mapping.get(
                name, name
            )

    async def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        if not self.destination_standard_pattern_mapping:
            mapping = {}
            # Populate the standard pattern mapping
            try:
                std_patterns = (await destination_client.get(self.resource_config.base_path + "/standard-patterns"))[
                    "data"
                ]
                for pattern in std_patterns:
                    mapping[pattern["attributes"]["name"]] = pattern["id"]
                self.destination_standard_pattern_mapping = mapping
            except Exception as e:
                self.config.logger.warning("error retrieving standard patterns: %s", e)

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        payload = {"data": resource, "meta": {}}
        resp = await destination_client.post(self.resource_config.base_path + "/rules", payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        payload = {"data": resource, "meta": {}}
        await destination_client.patch(
            self.resource_config.base_path + f"/rules/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        payload = {"meta": {}}
        await destination_client.delete(
            self.resource_config.base_path + f"/rules/{self.config.state.destination[self.resource_type][_id]['id']}",
            body=payload,
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(SensitiveDataScannerRules, self).connect_id(key, r_obj, resource_to_connect)
