# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsViews(BaseResource):
    resource_type = "logs_views"
    resource_config = ResourceConfig(
        resource_connections={"logs_indexes": ["index"]},
        base_path="/api/v1/logs/views",
        excluded_attributes=[
            "modified_at",
            "author",
            "id",
            "integration_id",
            "integration_short_name",
            "is_favorite"
        ]
    )
    # Additional LogsViews specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["logs_views"]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()["logs_view"]

        resource = cast(dict, resource)
        # skip integrations saved views
        if resource["integration_id"]:
            return
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = resource
        payload["_authentication_token"] = destination_client.csrf_token
        resp = destination_client.post(
            self.resource_config.base_path,
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = resource
        payload["_authentication_token"] = destination_client.csrf_token
        resp = destination_client.put(
            self.resource_config.base_path +
            f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        payload = {}
        payload["_authentication_token"] = destination_client.csrf_token
        destination_client.delete(
            self.resource_config.base_path +
            f"/{self.resource_config.destination_resources[_id]['id']}?type=logs",
            payload,
        ).json()

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
