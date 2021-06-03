import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource

RESOURCE_TYPE = "dashboards"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['author_handle']",
    "root['author_name']",
    "root['url']",
    "root['created_at']",
    "root['modified_at']",
]
RESOURCE_CONNECTIONS = {"monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"]}
BASE_PATH = "/api/v1/dashboard"


log = logging.getLogger("__name__")


class Dashboards(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
        )

    def import_resources(self):
        dashboards = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_PATH).json()
        except HTTPError as e:
            log.error("error importing dashboards %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource_import, dash["id"], dashboards) for dash in resp["dashboards"]])

        # Write the resource to a file
        self.write_resources_file("source", dashboards)

    def process_resource_import(self, dash_id, dashboards):
        source_client = self.ctx.obj.get("source_client")
        try:
            dashboard = source_client.get(BASE_PATH + f"/{dash_id}").json()
        except HTTPError as e:
            log.error("error retrieving dashboard: %s", e)
        dashboards[dash_id] = dashboard

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(source_resources, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, dashboard, local_destination_resources, connection_resource_obj=None):
        if self.resource_connections:
            self.connect_resources(dashboard, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, dashboard, local_destination_resources)
        else:
            self.create_resource(_id, dashboard, local_destination_resources)

    def create_resource(self, _id, dashboard, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        try:
            resp = destination_client.post(self.base_path, dashboard).json()
        except HTTPError as e:
            log.error("error updating dashboard: %s", e)
            return
        local_destination_resources[_id] = resp

    def update_resource(self, _id, dashboard, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        diff = DeepDiff(
            dashboard, local_destination_resources[_id], ignore_order=True, exclude_paths=self.excluded_attributes
        )
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", dashboard
                ).json()
            except HTTPError as e:
                log.error("error creating dashboard: %s", e)
                return
            local_destination_resources[_id] = resp
