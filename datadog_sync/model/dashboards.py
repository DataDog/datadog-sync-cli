# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Dashboards(BaseResource):
    resource_type = "dashboards"
    resource_config = ResourceConfig(
        resource_connections={
            "monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"],
            "service_level_objectives": ["widgets.definition.slo_id", "widgets.definition.widgets.definition.slo_id"],
            "roles": ["restricted_roles"],
        },
        base_path="/api/v1/dashboard",
        excluded_attributes=["id", "author_handle", "author_name", "url", "created_at", "modified_at"],
    )
    # Additional Dashboards specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["dashboards"]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        resource = source_client.get(self.resource_config.base_path + f"/{import_id}").json()
        resource = cast(dict, resource)
        
        return import_id, resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()

        return _id, resp

    def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        return _id, resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Dashboards, self).connect_id(key, r_obj, resource_to_connect)
