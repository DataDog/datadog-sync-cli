# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import copy
from typing import Optional, List, Dict

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import check_diff


class DashboardLists(BaseResource):
    resource_type = "dashboard_lists"
    resource_config = ResourceConfig(
        resource_connections={"dashboards": ["dashboards.id"]},
        base_path="/api/v1/dashboard/lists/manual",
        excluded_attributes=[
            "id",
            "type",
            "author",
            "created",
            "modified",
            "is_favorite",
            "dashboard_count",
        ],
    )
    # Additional Dashboards specific attributes
    dash_list_items_path = "/api/v2/dashboard/lists/manual/{}/dashboards"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["dashboard_lists"]

    def import_resource(self, resource: Dict) -> None:
        source_client = self.config.source_client
        _id = str(resource["id"])
        resp = None
        try:
            resp = source_client.get(self.dash_list_items_path.format(_id)).json()
        except HTTPError as e:
            self.config.logger.error("error retrieving dashboard_lists items %s", e)

        resource["dashboards"] = []
        if resp:
            for dash in resp.get("dashboards"):
                dash_list_item = {"id": dash["id"], "type": dash["type"]}
                resource["dashboards"].append(dash_list_item)

        self.resource_config.source_resources[_id] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        resource.pop("dashboards")
        resp = destination_client.post(self.resource_config.base_path, resource).json()

        self.resource_config.destination_resources[_id] = resp
        self.update_dash_list_items(resp["id"], dashboards, resp)

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        dash_list_diff = check_diff(
            self.resource_config,
            self.resource_config.destination_resources[_id]["dashboards"],
            dashboards,
        )
        resource.pop("dashboards")

        resp = destination_client.put(
            self.resource_config.base_path
            + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        resp.pop("dashboards")
        self.resource_config.destination_resources[_id].update(resp)

        if dash_list_diff:
            self.update_dash_list_items(
                self.resource_config.destination_resources[_id]["id"],
                dashboards,
                self.resource_config.destination_resources[_id],
            )

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path
            + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(DashboardLists, self).connect_id(key, r_obj, resource_to_connect)

    def update_dash_list_items(self, _id: str, dashboards: Dict, dashboard_list: dict):
        payload = {"dashboards": dashboards}
        destination_client = self.config.destination_client
        try:
            dashboards = destination_client.put(
                self.dash_list_items_path.format(_id), payload
            ).json()
        except HTTPError as e:
            self.config.logger.error("error updating dashboard list items: %s", e)
            return
        dashboard_list.update(dashboards)
