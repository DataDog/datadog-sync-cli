# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class HostTags(BaseResource):
    resource_type = "host_tags"
    resource_config = ResourceConfig(
        resource_connections={},
        base_path="/api/v1/tags/hosts",
    )
    # Additional HostTags specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return list(resp["tags"].items())

    def import_resource(self, resource: Dict) -> None:
        tag = resource[0]
        hosts = resource[1]
        for host in hosts:
            if host not in self.resource_config.source_resources:
                self.resource_config.source_resources[host] = []
            self.resource_config.source_resources[host].append(tag)

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        self.update_resource(_id, resource)

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        body = {"tags": resource}
        resp = destination_client.put(self.resource_config.base_path + f"/{_id}", body).json()

        self.resource_config.destination_resources[_id] = resp["tags"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(self.resource_config.base_path + f"/{_id}")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(HostTags, self).connect_id(key, r_obj, resource_to_connect)
