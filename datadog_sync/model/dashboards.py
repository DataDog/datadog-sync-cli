# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Dashboards(BaseResource):
    resource_type = "dashboards"
    resource_config = ResourceConfig(
        resource_connections={
            "monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"],
            "powerpacks": ["widgets.definition.powerpack_id", "widgets.definition.widgets.definition.powerpack_id"],
            "service_level_objectives": ["widgets.definition.slo_id", "widgets.definition.widgets.definition.slo_id"],
            "roles": ["restricted_roles"],
        },
        base_path="/api/v1/dashboard",
        excluded_attributes=[
            "id",
            "author_handle",
            "author_name",
            "url",
            "created_at",
            "modified_at",
            "is_read_only",
            "notify_list",
        ],
    )
    # Additional Dashboards specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["dashboards"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        try:
            resource = await source_client.get(self.resource_config.base_path + f"/{import_id}")
        except CustomClientHTTPError as err:
            if err.status_code == 403:
                raise SkipResource(import_id, self.resource_type, "No access to restricted dashboard")
            raise err

        resource = cast(dict, resource)
        return import_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Dashboards, self).connect_id(key, r_obj, resource_to_connect)
