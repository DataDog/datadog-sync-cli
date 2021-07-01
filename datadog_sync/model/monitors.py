from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class Monitors(BaseResource):
    resource_type = "monitors"
    resource_connections = {"monitors": ["query"], "roles": ["restricted_roles"]}
    base_path = "/api/v1/monitor"
    excluded_attributes = [
        "root['id']",
        "root['matching_downtimes']",
        "root['creator']",
        "root['created']",
        "root['deleted']",
        "root['org_id']",
        "root['created_at']",
        "root['modified']",
        "root['overall_state']",
        "root['overall_state_modified']",
    ]

    def import_resources(self):
        source_client = self.config.source_client
        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing monitors %s", e)
            return

        self.import_resources_concurrently(resp)

    def process_resource_import(self, monitor):
        if not self.filter(monitor) and monitor["type"] != "synthetics alert":
            return

        self.source_resources[monitor["id"]] = monitor

    def apply_resources(self):
        simple_monitors = {}
        composite_monitors = {}

        for _id, monitor in self.source_resources.items():
            if monitor["type"] == "synthetics alert":
                continue
            if monitor["type"] == "composite":
                composite_monitors[_id] = monitor
            else:
                simple_monitors[_id] = monitor

        self.logger.info("Processing Simple Monitors")
        self.apply_resources_concurrently({}, resources=simple_monitors)

        self.logger.info("Processing Composite Monitors")
        connection_resource_obj = self.get_connection_resources()

        self.apply_resources_concurrently(connection_resource_obj, resources=composite_monitors)

    def prepare_resource_and_apply(self, _id, monitor, connection_resource_obj, **kwargs):
        self.connect_resources(monitor, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, monitor)
        else:
            self.create_resource(_id, monitor)

    def create_resource(self, _id, monitor):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, monitor).json()
        except HTTPError as e:
            self.logger.error("error creating monitor: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, monitor):
        destination_client = self.config.destination_client

        diff = self.check_diff(monitor, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", monitor
                ).json()
            except HTTPError as e:
                self.logger.error("error creating monitor: %s", e.response.text)
                return
            self.destination_resources[_id] = resp
