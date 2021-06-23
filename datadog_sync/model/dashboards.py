from concurrent.futures import ThreadPoolExecutor, wait

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


class Dashboards(BaseResource):
    resource_type = "dashboards"

    source_resources = {}
    destination_resources = {}

    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
        )

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing dashboards %s", e)
            return

        self.import_resources_concurrently(resp["dashboards"])

    def process_resource_import(self, dash):
        source_client = self.config.source_client
        try:
            dashboard = source_client.get(self.base_path + f"/{dash['id']}").json()
        except HTTPError as e:
            self.logger.error("error retrieving dashboard: %s", e)
            return
        self.source_resources[dash["id"]] = dashboard

    def apply_resources(self):
        self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(self.source_resources, connection_resource_obj)

    def prepare_resource_and_apply(self, _id, dashboard, connection_resource_obj=None):
        if self.resource_connections:
            self.connect_resources(dashboard, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, dashboard)
        else:
            self.create_resource(_id, dashboard)

    def create_resource(self, _id, dashboard):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, dashboard).json()
        except HTTPError as e:
            self.logger.error("error updating dashboard: %s", e)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, dashboard):
        destination_client = self.config.destination_client

        diff = self.check_diff(dashboard, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", dashboard
                ).json()
            except HTTPError as e:
                self.logger.error("error creating dashboard: %s", e)
                return
            self.destination_resources[_id] = resp
