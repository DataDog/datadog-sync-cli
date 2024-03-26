# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class DashboardLists(BaseResource):
    resource_type = "dashboard_lists"
    resource_config = ResourceConfig(
        resource_connections={"dashboards": ["dashboards.id"]},
        base_path="/api/v1/dashboard/lists/manual",
        excluded_attributes=["id", "type", "author", "created", "modified", "is_favorite", "dashboard_count"],
    )
    # Additional Dashboards specific attributes
    dash_list_items_path: str = "/api/v2/dashboard/lists/manual/{}/dashboards"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["dashboard_lists"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client

        if _id:
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        _id = str(resource["id"])
        resp = None
        try:
            resp = await source_client.get(self.dash_list_items_path.format(_id))
        except CustomClientHTTPError as e:
            self.config.logger.error("error retrieving dashboard_lists items %s", e)

        resource["dashboards"] = []
        if resp:
            for dash in resp.get("dashboards", []):
                dash_list_item = {"id": dash["id"], "type": dash["type"]}
                resource["dashboards"].append(dash_list_item)

        return _id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        resource.pop("dashboards")
        resp = await destination_client.post(self.resource_config.base_path, resource)
        await self.update_dash_list_items(resp["id"], dashboards, resp)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        dash_list_diff = check_diff(
            self.resource_config,
            self.resource_config.destination_resources[_id]["dashboards"],
            dashboards,
        )
        resource.pop("dashboards")

        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        )

        resp.pop("dashboards")
        self.resource_config.destination_resources[_id].update(resp)

        if dash_list_diff:
            await self.update_dash_list_items(
                self.resource_config.destination_resources[_id]["id"],
                dashboards,
                self.resource_config.destination_resources[_id],
            )

        return _id, self.resource_config.destination_resources[_id]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(DashboardLists, self).connect_id(key, r_obj, resource_to_connect)

    async def update_dash_list_items(self, _id: str, dashboards: Dict, dashboard_list: dict):
        payload = {"dashboards": dashboards}
        destination_client = self.config.destination_client
        try:
            dashboards = await destination_client.put(self.dash_list_items_path.format(_id), payload)
        except CustomClientHTTPError as e:
            self.config.logger.error("error updating dashboard list items: %s", e)
            return
        dashboard_list.update(dashboards)
