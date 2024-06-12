# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import INVALID_INTEGRATION_LOGS_PIPELINES, LogsPipelinesOrderIdsComparator

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
        # Ensure the destination integration pipelines are populated
        # We use this data to delete invalid integration pipelines from the order
        logs_pipelines = self.config.resources["logs_pipelines"]
        if not logs_pipelines.destination_integration_pipelines:
            self.config.logger.info("Destination integration pipelines not populated. Fetching now.")
            logs_pipelines.destination_integration_pipelines = (
                await logs_pipelines.get_destination_integration_pipelines()
            )

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if not self.destination_pipeline_order:
            raise Exception("Failed to retrieve destination orgs logs pipeline order")

        self.resource_config.destination_resources[_id] = self.destination_pipeline_order
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_resources = self.destination_pipeline_order or self.resource_config.destination_resources[_id]
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
        logs_pipelines = self.config.resources["logs_pipelines"]
        source_pipelines = logs_pipelines.resource_config.source_resources
        destination_pipelines = logs_pipelines.resource_config.destination_resources
        failed_connections = []
        ids_to_delete = []
        for i, v in enumerate(r_obj[key]):
            if v in destination_pipelines:
                r_obj[key][i] = destination_pipelines[v]["id"]
            elif (
                v in source_pipelines
                and source_pipelines[v]["name"] in INVALID_INTEGRATION_LOGS_PIPELINES
                and source_pipelines[v]["is_read_only"]
            ):
                # We need to determine if the source pipeline is a valid integration pipeline
                # and wether it already exists in the destination org. If it does not,
                # we need to remove it from the pipeline order.
                if source_pipelines[v]["name"] in logs_pipelines.destination_integration_pipelines:
                    failed_connections.append(v)
                else:
                    ids_to_delete.append(v)
            else:
                failed_connections.append(v)

        for _id in ids_to_delete:
            try:
                r_obj[key].remove(_id)
            except ValueError:
                pass

        return failed_connections

    async def get_destination_pipeline_order(self):
        destination_client = self.config.destination_client
        resp = await self.get_resources(destination_client)

        return resp[0]
