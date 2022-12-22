# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict
from datetime import datetime

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class SLOCorrections(BaseResource):
    resource_type = "slo_corrections"
    resource_config = ResourceConfig(
        resource_connections={"service_level_objectives": ["attributes.slo_id"]},
        base_path="/api/v1/slo/correction",
        excluded_attributes=[
            "id",
            "attributes.creator",
            "attributes.created_at",
            "attributes.modified_at",
        ],
        non_nullable_attr=["attributes.duration", "attributes.rrule"],
    )
    # Additional SLOCorrections specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["data"]

    def import_resource(self, resource: Dict) -> None:
        if resource["attributes"].get("end", False):
            if (
                round(datetime.now().timestamp()) - int(resource["attributes"]["end"])
            ) / 86400 > 90:
                return
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path, payload).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.patch(
            self.resource_config.base_path
            + f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path
            + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(SLOCorrections, self).connect_id(key, r_obj, resource_to_connect)
