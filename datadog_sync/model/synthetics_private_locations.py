# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import re

from typing import List, Dict, Optional

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class SyntheticsPrivateLocations(BaseResource):
    resource_type = "synthetics_private_locations"
    resource_config = ResourceConfig(
        base_path="/api/v1/synthetics/private-locations",
        excluded_attributes=["id", "modifiedAt", "createdAt", "createdBy", "metadata", "secrets", "config"],
    )
    # Additional SyntheticsPrivateLocations specific attributes
    base_locations_path = "/api/v1/synthetics/locations"
    pl_id_regex = re.compile("^pl:.*")

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.base_locations_path).json()

        return resp["locations"]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        if self.pl_id_regex.match(import_id):
            pl = source_client.get(self.resource_config.base_path + f"/{import_id}").json()

            self.resource_config.source_resources[import_id] = pl

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()["private_location"]

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        self.resource_config.destination_resources[_id].update(resp)

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
