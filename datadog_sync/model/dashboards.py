import os
import copy
import json
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.constants import RESOURCE_FILE_PATH

RESOURCE_TYPE = "dashboards"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['author_handle']",
    "root['author_name']",
    "root['url']",
    "root['created_at']",
    "root['modified_at']",
]
EXCLUDED_REGEX_ATTRIBUTES = [
    "root\\['widgets'\\]\\[\\d+\\]\\['definition'\\]\\['alert_id'\\]",
    "root\\['widgets'\\]\\[\\d+\\]\\['definition'\\]\\['widgets'\\]\\[\\d+\\][\\definition\\]\\[alert_id\\]",
]
RESOURCE_CONNECTIONS = {"monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"]}
BASE_PATH = "/api/v1/dashboard"


class Dashboards(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, resource_connections=RESOURCE_CONNECTIONS)

    def import_resources(self):
        dashboards = {}

        source_client = self.ctx.obj.get("source_client")
        res = source_client.get(BASE_PATH).json()

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, dash["id"], dashboards) for dash in res["dashboards"]])

        # Write the resource to a file
        self.write_resources_file("source", dashboards)

    def process_resource(self, dash_id, dashboards):
        source_client = self.ctx.obj.get("source_client")
        dashboard = source_client.get(BASE_PATH + f"/{dash_id}").json()
        dashboards[dash_id] = dashboard

    def apply_resources(self):
        source_dashboards, destination_dashboards = self.open_resources()
        connection_resources = {}

        for k in self.resource_connections.keys():
            path = RESOURCE_FILE_PATH.format("destination", k)
            if os.path.exists(path):
                with open(RESOURCE_FILE_PATH.format("destination", k), "r") as f:
                    connection_resources[k] = json.load(f)

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        dashboard,
                        destination_dashboards,
                        connection_resources=connection_resources,
                    )
                    for _id, dashboard in source_dashboards.items()
                ]
            )

        self.write_resources_file("destination", destination_dashboards)

    def prepare_resource_and_apply(self, _id, dashboard, destination_dashboards, connection_resources=None):
        destination_client = self.ctx.obj.get("destination_client")
        self.connect_resources(dashboard, connection_resources)

        if _id in destination_dashboards:
            dest_monitor_copy = copy.deepcopy(destination_dashboards[_id])
            diff = DeepDiff(
                dashboard,
                dest_monitor_copy,
                ignore_order=True,
                exclude_regex_paths=EXCLUDED_REGEX_ATTRIBUTES,
                exclude_paths=EXCLUDED_ATTRIBUTES,
            )
            if diff:
                res = destination_client.put(BASE_PATH + f"/{destination_dashboards[_id]['id']}", dashboard).json()
                destination_dashboards[_id] = res
        else:
            res = destination_client.post(BASE_PATH, dashboard).json()
            destination_dashboards[_id] = res
