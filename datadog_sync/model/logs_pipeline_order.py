# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import LogsPipelineOrderIdsComparator

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsPipelineOrder(BaseResource):
    resource_type = "logs_pipeline_order"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipeline-order",
        resource_connections={
            "logs_custom_pipelines": ["pipeline_ids"],
            "logs_integration_pipelines": ["pipeline_ids"],
        },
        deep_diff_config={
            "ignore_order": False,
            "custom_operators": [LogsPipelineOrderIdsComparator()],
        },
    )
    # Additional LogsPipelineOrder specific attributes
    destination_pipeline_order: Dict[str, Dict] = dict()
    default_id: str = "logs-pipeline-order"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return [resp]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path).json()

        self.resource_config.source_resources[self.default_id] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        self.destination_pipeline_order = self.get_destination_pipeline_order()

    def create_resource(self, _id: str, resource: Dict) -> None:
        if not self.destination_pipeline_order:
            raise Exception("Failed to retrieve destination orgs logs pipeline order")

        self.resource_config.destination_resources[_id] = self.destination_pipeline_order
        self.update_resource(_id, resource)

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_resources = self.destination_pipeline_order or self.resource_config.destination_resources[_id]
        ids_to_omit = set(resource["pipeline_ids"]) - set(destination_resources["pipeline_ids"])

        extra_ids_to_include = [
            _id for _id in destination_resources["pipeline_ids"] if _id not in resource["pipeline_ids"]
        ]

        resource["pipeline_ids"] = [_id for _id in resource["pipeline_ids"] if _id not in ids_to_omit]
        resource["pipeline_ids"] = resource["pipeline_ids"] + extra_ids_to_include

        destination_client = self.config.destination_client
        resp = destination_client.put(self.resource_config.base_path, resource).json()
        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        raise Exception("resource cannot be deleted.")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(LogsPipelineOrder, self).connect_id(key, r_obj, resource_to_connect)

    def get_destination_pipeline_order(self):
        destination_client = self.config.destination_client
        resp = self.get_resources(destination_client)

        return resp[0]
