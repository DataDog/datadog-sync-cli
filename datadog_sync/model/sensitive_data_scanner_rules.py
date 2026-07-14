# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.constants import Command, Metrics
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource

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
        non_nullable_attr=["attributes.included_keywords"],
        concurrent=False,
        skip_resource_mapping=True,
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

        if _std_id := (resource.get("relationships", {}).get("standard_pattern", {}).get("data") or {}).get("id"):
            resource["relationships"]["standard_pattern"]["data"]["id"] = self.source_standard_pattern_mapping.get(
                _std_id, _std_id
            )

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if name := (resource.get("relationships", {}).get("standard_pattern", {}).get("data") or {}).get("id"):
            dest_id = self.destination_standard_pattern_mapping.get(name)
            if dest_id is None:
                raise SkipResource(
                    _id,
                    self.resource_type,
                    f"Deprecated resource configuration: Standard pattern '{name}' "
                    "does not exist in the destination org. Provision the standard scanner pattern before syncing.",
                )
            resource["relationships"]["standard_pattern"]["data"]["id"] = dest_id

    async def _align_name_with_standard_pattern(self, _id: str, resource: Dict) -> None:
        # Destination API rejects a standard-pattern-linked rule whose
        # attributes.name does not match the linked pattern's canonical name.
        # Overwrite the name on write and emit a metric so operators can
        # audit drift. Applied only on create/update (not diffs/import) so
        # source state is not silently mutated. By the time this runs,
        # pre_resource_action_hook has already replaced data.id with the
        # destination pattern uuid, so resolve the canonical name via the
        # destination mapping (name -> id) rather than trusting the id
        # field to still hold a name string.
        pattern_id = ((resource.get("relationships", {}).get("standard_pattern", {}).get("data") or {}).get("id"))
        if not pattern_id:
            return
        pattern_name = next(
            (n for n, i in self.destination_standard_pattern_mapping.items() if i == pattern_id),
            None,
        )
        if not pattern_name:
            return
        attrs = resource.setdefault("attributes", {})
        source_name = attrs.get("name")
        if not source_name or source_name == pattern_name:
            return
        attrs["name"] = pattern_name
        self.config.logger.debug(
            "%s %s: aligned attributes.name '%s' -> '%s' to match linked standard pattern",
            self.resource_type,
            _id,
            source_name,
            pattern_name,
        )
        try:
            await self.config.destination_client.send_metric(
                Metrics.ACTION.value,
                [
                    f"id:{_id}",
                    f"resource_type:{self.resource_type}",
                    f"action_type:{Command.SYNC.value}",
                    "action_sub_type:standard_pattern_name_rewrite",
                    "status:success",
                    "client_type:destination",
                    f"pattern:{pattern_name}",
                ],
            )
        except Exception as e:
            self.config.logger.debug(
                "Failed to send standard_pattern_name_rewrite metric for %s %s: %s",
                self.resource_type,
                _id,
                e,
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

        await self._align_name_with_standard_pattern(_id, resource)
        payload = {"data": resource, "meta": {}}
        resp = await destination_client.post(self.resource_config.base_path + "/rules", payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        await self._align_name_with_standard_pattern(_id, resource)
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
