# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.utils.resource_utils import check_diff, find_attr, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class ServiceLevelObjectives(BaseResource):
    resource_type = "service_level_objectives"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["monitor_ids"], "synthetics_tests": []},
        base_path="/api/v1/slo",
        excluded_attributes=["creator", "id", "created_at", "modified_at"],
        tagging_config=TaggingConfig(path="tags"),
    )
    # Additional ServiceLevelObjectives specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]
        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _clean_resource_connections(self, _id: str, resource: Dict) -> Dict:
        # SLO failed connections to monitors may need to be removed from the resource
        cleaned_resource = deepcopy(resource)
        if self.config.skip_failed_resource_connections:
            if "monitor_ids" in cleaned_resource.keys():
                failed_connections = self.connect_id("monitor_ids", cleaned_resource, "monitors")
                for monitor_id in failed_connections:
                    if "monitor_ids" in cleaned_resource.keys() and int(monitor_id) in cleaned_resource["monitor_ids"]:
                        cleaned_resource["monitor_ids"].remove(int(monitor_id))
            if _id in self.config.state.destination[self.resource_type]:
                diff = check_diff(self.resource_config, self.config.state.destination[self.resource_type][_id], cleaned_resource)
                if not diff:
                    raise SkipResource(_id, self.resource_type, "No differences after failed connections removed")
        return cleaned_resource

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        cleaned_resource = await self._clean_resource_connections(_id, self.config.state.source[self.resource_type][_id])
        if resource.get("monitor_ids", None) and not cleaned_resource.get("monitor_ids", None):
            raise SkipResource(_id, self.resource_type, "None of the monitor_ids exist at destination")

        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, cleaned_resource)
        return _id, resp["data"][0]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        cleaned_resource = await self._clean_resource_connections(_id, self.config.state.source[self.resource_type][_id])
        if resource.get("monitor_ids", None) and not cleaned_resource.get("monitor_ids", None):
            raise SkipResource(_id, self.resource_type, "None of the monitor_ids exist at destination")
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            cleaned_resource,
        )

        return _id, resp["data"][0]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            params={"force": "true"},
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        monitors = self.config.state.destination["monitors"]
        synthetics_tests = self.config.state.destination["synthetics_tests"]

        failed_connections = []
        for i, obj in enumerate(r_obj[key]):
            _id = str(obj)
            # Check if resource exists in monitors
            if _id in monitors:
                r_obj[key][i] = monitors[_id]["id"]
                continue
            # Fall back on Synthetics and check
            found = False
            for k, v in synthetics_tests.items():
                if k.endswith(_id):
                    r_obj[key][i] = v["monitor_id"]
                    found = True
                    break
            if not found:
                failed_connections.append(_id)
        return failed_connections
