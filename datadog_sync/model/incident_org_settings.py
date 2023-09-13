# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class IncidentOrgSettings(BaseResource):
    resource_type = "incident_org_settings"
    resource_config = ResourceConfig(
        base_path="/api/v2/incidents/config/org/settings",
        excluded_attributes=[
            "id",
            "attributes.modified",
        ]
    )
    # Additional Incidents specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()["data"]
        return [ resp ]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            # there is only one settings, ignoring id
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path).json()["data"]

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        # the settings is always there, just update
        self.update_resource(_id, resource)

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.patch(
            self.resource_config.base_path,
            payload,
        ).json()["data"]

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        raise Exception("deleting incident_org_settings is not supported")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
