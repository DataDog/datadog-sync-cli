from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN


class Dashboards(BaseResource):
    resource_type = "dashboards"
    resource_connections = {
        "monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"],
        "service_level_objectives": ["widgets.definition.slo_id", "widgets.definition.widgets.definition.slo_id"],
        "roles": ["restricted_roles"],
    }
    base_path = "/api/v1/dashboard"
    excluded_attributes = [
        "root['id']",
        "root['author_handle']",
        "root['author_name']",
        "root['url']",
        "root['created_at']",
        "root['modified_at']",
    ]
    match_on = "title"

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing dashboards %s", e)
            return

        if self.config.import_existing:
            self.get_destination_existing_resources()

        self.import_resources_concurrently(resp["dashboards"])

    def process_resource_import(self, dash):
        self.source_resources[dash["id"]] = self.get_dashboard(dash['id'], SOURCE_ORIGIN)

        # Map existing resources
        if self.config.import_existing:
            if dash[self.match_on] in self.destination_existing_resources:
                existing_dash = self.destination_existing_resources[dash[self.match_on]]
                self.destination_resources[str(dash["id"])] = self.get_dashboard(existing_dash["id"], DESTINATION_ORIGIN)

    def get_destination_existing_resources(self):
        destination_client = self.config.destination_client

        try:
            dest_resp = destination_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing dashboards %s", e)
            return

        for dashboard in dest_resp["dashboards"]:
            self.destination_existing_resources[dashboard[self.match_on]] = dashboard

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(connection_resource_obj)

    def prepare_resource_and_apply(self, _id, dashboard, connection_resource_obj):
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

    def get_dashboard(self, _id, origin):
        if origin == SOURCE_ORIGIN:
            client = self.config.source_client
        else:
            client = self.config.destination_client

        try:
            dashboard = client.get(self.base_path + f"/{_id}").json()
        except HTTPError as e:
            self.logger.error("error retrieving dashboard: %s", e)
            return

        return dashboard
