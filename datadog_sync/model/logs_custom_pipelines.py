# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class LogsCustomPipelines(BaseResource):
    resource_type = "logs_custom_pipelines"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipelines",
        excluded_attributes=["id", "type", "is_read_only"],
    )
    # Additional LogsCustomPipelines specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp

    def import_resource(self, resource: Dict) -> None:
        if resource["is_read_only"]:
            return
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
