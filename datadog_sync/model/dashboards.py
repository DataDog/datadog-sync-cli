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
            log.error("error importing dashboards: %", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, dash["id"], dashboards) for dash in resp["dashboards"]])

        # Write the resource to a file
        self.write_resources_file("source", dashboards)

    def process_resource(self, dash_id, dashboards):
        source_client = self.ctx.obj.get("source_client")
        try:
            dashboard = source_client.get(BASE_PATH + f"/{dash_id}").json()
        except HTTPError as e:
            log.error("error retrieving dashboard: %e", e)
        dashboards[dash_id] = dashboard

    def apply_resources(self):
        source_resources, destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        dashboard,
                        destination_resources,
                        connection_resource_obj,
                    )
                    for _id, dashboard in source_resources.items()
                ]
            )

        self.write_resources_file("destination", destination_resources)

    def prepare_resource_and_apply(self, _id, resource, local_resources, connection_resource_obj=None):
        destination_client = self.ctx.obj.get("destination_client")
        if self.resource_connections:
            self.connect_resources(resource, connection_resource_obj)

        if _id in local_resources:
            diff = DeepDiff(resource, local_resources[_id], ignore_order=True, exclude_paths=self.excluded_attributes)
            if diff:
                try:
                    resp = destination_client.put(self.base_path + f"/{local_resources[_id]['id']}", resource).json()
                except HTTPError as e:
                    log.error("error creating dashboard: %e", e)
                    return
                local_resources[_id] = resp
        else:
            try:
                resp = destination_client.post(self.base_path, resource).json()
            except HTTPError as e:
                log.error("error updating dashboard: %e", e)
                return
            local_resources[_id] = resp
