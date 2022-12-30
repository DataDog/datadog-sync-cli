# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class MetricTagConfigurations(BaseResource):
    resource_type = "metric_tag_configurations"
    resource_config = ResourceConfig(
        resource_connections={},
        base_path="/api/v2/metrics",
        excluded_attributes=["attributes.created_at", "attributes.modified_at"],
    )
    # Additional MetricTagConfigurations specific attributes
    destination_metric_tag_configurations: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path, params={"filter[configured]": "true"}).json()

        return resp["data"]

    def import_resource(self, resource: Dict) -> None:
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        self.destination_metric_tag_configurations = self.get_destination_metric_tag_configuration()
        return None

    def create_resource(self, _id: str, resource: Dict) -> None:
        if _id in self.destination_metric_tag_configurations:
            self.resource_config.destination_resources[_id] = self.destination_metric_tag_configurations[_id]
            self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.post(
            self.resource_config.base_path + f"/{self.resource_config.source_resources[_id]['id']}/tags",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        if "attributes" in resource:
            resource["attributes"].pop("metric_type", None)
        payload = {"data": resource}
        resp = destination_client.patch(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}/tags",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}/tags"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(MetricTagConfigurations, self).connect_id(key, r_obj, resource_to_connect)

    def get_destination_metric_tag_configuration(self) -> Dict[str, Dict]:
        destination_metric_tag_configurations = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for metric_tag_config in resp:
            destination_metric_tag_configurations[metric_tag_config["id"]] = metric_tag_config

        return destination_metric_tag_configurations
