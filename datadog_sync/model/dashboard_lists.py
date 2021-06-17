import copy
import logging

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger(__name__)


RESOURCE_TYPE = "dashboard_lists"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['type']",
    "root['author']",
    "root['created']",
    "root['modified']",
    "root['is_favorite']",
    "root['dashboard_count']",
]
RESOURCE_CONNECTIONS = {"dashboards": ["dashboards.id"]}
BASE_PATH = "/api/v1/dashboard/lists/manual"
DASH_LIST_ITEMS_PATH = "/api/v2/dashboard/lists/manual/{}/dashboards"


class DashboardLists(BaseResource):
    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
        )

    def import_resources(self):
        dashboard_lists = {}
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            log.error("error importing dashboard_lists %s", e)
            return

        self.import_resources_concurrently(dashboard_lists, resp["dashboard_lists"])

        # Write resources to file
        self.write_resources_file("source", dashboard_lists)

    def process_resource_import(self, dashboard_list, dashboard_lists):
        source_client = self.config.source_client
        resp = None
        try:
            resp = source_client.get(DASH_LIST_ITEMS_PATH.format(dashboard_list["id"])).json()
        except HTTPError as e:
            log.error("error retrieving dashboard_lists items %s", e)

        dashboard_list["dashboards"] = []
        if resp:
            for dash in resp.get("dashboards"):
                dash_list_item = {"id": dash["id"], "type": dash["type"]}
                dashboard_list["dashboards"].append(dash_list_item)

        dashboard_lists[dashboard_list["id"]] = dashboard_list

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(source_resources, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, dashboard_list, local_destination_resources, connection_resource_obj):
        self.connect_resources(dashboard_list, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, dashboard_list, local_destination_resources)
        else:
            self.create_resource(_id, dashboard_list, local_destination_resources)

    def create_resource(self, _id, dashboard_list, local_destination_resources):
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(dashboard_list["dashboards"])
        dashboard_list.pop("dashboards")
        self.remove_excluded_attr(dashboard_list)

        try:
            resp = destination_client.post(self.base_path, dashboard_list).json()
        except HTTPError as e:
            log.error("error creating dashboard_list: %s", e.response.text)
            return
        local_destination_resources[_id] = resp
        self.update_dash_list_items(resp["id"], dashboards, resp)

    def update_resource(self, _id, dashboard_list, local_destination_resources):
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(dashboard_list["dashboards"])
        dashboard_list.pop("dashboards")
        self.remove_excluded_attr(dashboard_list)
        dash_list_diff = self.check_diff(local_destination_resources[_id]["dashboards"], dashboards)

        diff = self.check_diff(dashboard_list, local_destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", dashboard_list
                ).json()
            except HTTPError as e:
                log.error("error creating dashboard_list: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
        if dash_list_diff:
            self.update_dash_list_items(
                local_destination_resources[_id]["id"], dashboards, local_destination_resources[_id]
            )

    def update_dash_list_items(self, _id, dashboards, dashboard_list):
        payload = {"dashboards": dashboards}
        destination_client = self.config.destination_client
        try:
            dashboards = destination_client.put(DASH_LIST_ITEMS_PATH.format(_id), payload).json()
        except HTTPError as e:
            log.error("error updating dashboard list items: %s", e)
            return
        dashboard_list.update(dashboards)
