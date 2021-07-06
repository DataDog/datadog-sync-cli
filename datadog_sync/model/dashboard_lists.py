import copy

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN


class DashboardLists(BaseResource):
    resource_type = "dashboard_lists"
    resource_connections = {"dashboards": ["dashboards.id"]}
    base_path = "/api/v1/dashboard/lists/manual"
    dash_list_items_path = "/api/v2/dashboard/lists/manual/{}/dashboards"
    excluded_attributes = [
        "root['id']",
        "root['type']",
        "root['author']",
        "root['created']",
        "root['modified']",
        "root['is_favorite']",
        "root['dashboard_count']",
    ]
    match_on = "name"

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing dashboard_lists %s", e)
            return

        if self.config.import_existing:
            self.populate_destination_existing_resources()

        self.import_resources_concurrently(resp["dashboard_lists"])

    def process_resource_import(self, dashboard_list):
        if not self.filter(dashboard_list):
            return

        dashboard_list["dashboards"] = self.get_dashboards(dashboard_list["id"], SOURCE_ORIGIN)
        self.source_resources[dashboard_list["id"]] = dashboard_list

        # Map existing resources
        if self.config.import_existing:
            if dashboard_list[self.match_on] in self.destination_existing_resources:
                existing_dash_list = self.destination_existing_resources[dashboard_list[self.match_on]]
                existing_dash_list["dashboards"] = self.get_dashboards(existing_dash_list["id"], DESTINATION_ORIGIN)
                self.destination_resources[str(dashboard_list["id"])] = existing_dash_list

    def populate_destination_existing_resources(self):
        destination_client = self.config.destination_client

        try:
            dest_resp = destination_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing dashboard_lists %s", e)
            return

        for dash_list in dest_resp["dashboard_lists"]:
            self.destination_existing_resources[dash_list[self.match_on]] = dash_list

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(connection_resource_obj)

    def prepare_resource_and_apply(self, _id, dashboard_list, connection_resource_obj):
        self.connect_resources(dashboard_list, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, dashboard_list)
        else:
            self.create_resource(_id, dashboard_list)

    def create_resource(self, _id, dashboard_list):
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(dashboard_list["dashboards"])
        dashboard_list.pop("dashboards")
        self.remove_excluded_attr(dashboard_list)

        try:
            resp = destination_client.post(self.base_path, dashboard_list).json()
        except HTTPError as e:
            self.logger.error("error creating dashboard_list: %s", e.response.text)
            return
        self.destination_resources[_id] = resp
        self.update_dash_list_items(resp["id"], dashboards, resp)

    def update_resource(self, _id, dashboard_list):
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(dashboard_list["dashboards"])

        self.remove_excluded_attr(dashboard_list)
        dash_list_diff = self.check_diff(self.destination_resources[_id]["dashboards"], dashboards)
        diff = self.check_diff(dashboard_list, self.destination_resources[_id])
        dashboard_list.pop("dashboards")

        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", dashboard_list
                ).json()
            except HTTPError as e:
                self.logger.error("error creating dashboard_list: %s", e.response.text)
                return

            resp.pop("dashboards")
            self.destination_resources[_id].update(resp)

        if dash_list_diff:
            self.update_dash_list_items(
                self.destination_resources[_id]["id"], dashboards, self.destination_resources[_id]
            )

    def update_dash_list_items(self, _id, dashboards, dashboard_list):
        payload = {"dashboards": dashboards}
        destination_client = self.config.destination_client
        try:
            dashboards = destination_client.put(self.dash_list_items_path.format(_id), payload).json()
        except HTTPError as e:
            self.logger.error("error updating dashboard list items: %s", e)
            return
        dashboard_list.update(dashboards)

    def get_dashboards(self, _id, origin):
        if origin == SOURCE_ORIGIN:
            client = self.config.source_client
        else:
            client = self.config.destination_client

        resp = None
        try:
            resp = client.get(self.dash_list_items_path.format(_id)).json()
        except HTTPError as e:
            self.logger.error("error retrieving dashboard_lists items %s", e)

        dashboards_list = []
        if resp:
            for dash in resp.get("dashboards"):
                dash_list_item = {"id": dash["id"], "type": dash["type"]}
                dashboards_list.append(dash_list_item)
        return dashboards_list
