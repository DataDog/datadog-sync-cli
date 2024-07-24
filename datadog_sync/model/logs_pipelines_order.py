# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import LogsPipelinesOrderIdsComparator

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsPipelinesOrder(BaseResource):
    resource_type = "logs_pipelines_order"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipeline-order",
        resource_connections={
            "logs_pipelines": ["pipeline_ids"],
        },
        deep_diff_config={
            "ignore_order": False,
            "custom_operators": [LogsPipelinesOrderIdsComparator()],
        },
    )
    # Additional LogsPipelinesOrder specific attributes
    destination_pipeline_order: Dict[str, Dict] = dict()
    default_id: str = "logs-pipeline-order"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return [resp]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path)

        return self.default_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_pipeline_order = await self.get_destination_pipeline_order()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if not self.destination_pipeline_order:
            raise Exception("Failed to retrieve destination orgs logs pipeline order")

        self.config.state.destination[self.resource_type][_id] = self.destination_pipeline_order
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_resources = (
            self.destination_pipeline_order or self.config.state.destination[self.resource_type][_id]
        )
        ids_to_omit = set(resource["pipeline_ids"]) - set(destination_resources["pipeline_ids"])

        extra_ids_to_include = [
            _id for _id in destination_resources["pipeline_ids"] if _id not in resource["pipeline_ids"]
        ]

        resource["pipeline_ids"] = [_id for _id in resource["pipeline_ids"] if _id not in ids_to_omit]
        resource["pipeline_ids"] = resource["pipeline_ids"] + extra_ids_to_include

        destination_client = self.config.destination_client
        resp = await destination_client.put(self.resource_config.base_path, resource)

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning("logs_pipeline_order cannot deleted. Removing resource from config only.")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        logs_pipelines = self.config.state.destination["logs_pipelines"]
        failed_connections = []
        ids_to_omit = []
        for i, _id in enumerate(r_obj[key]):
            if _id in logs_pipelines:
                if logs_pipelines[_id].get("__datadog_sync_invalid"):
                    # Invalid logs integration pipelines which cannot be created.
                    # we remove it from the final logs pipeline order payload.
                    ids_to_omit.append(_id)
                else:
                    r_obj[key][i] = logs_pipelines[_id]["id"]
            else:
                failed_connections.append(_id)

        if ids_to_omit:
            r_obj[key] = [_id for _id in r_obj[key] if _id not in ids_to_omit]

        return failed_connections

    async def get_destination_pipeline_order(self):
        destination_client = self.config.destination_client
        resp = await self.get_resources(destination_client)

        return resp[0]
