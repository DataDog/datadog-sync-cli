from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "monitors"
EXCLUDED_ATTRIBUTES = [
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
RESOURCE_CONNECTIONS = {"monitors": ["query"]}
BASE_PATH = "/api/v1/monitor"


class Monitors(BaseResource):
    resource_type = "monitors"

    source_resources = {}
    destination_resources = {}

    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCE_CONNECTIONS,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            source_resources={},
            destination_resources={},
        )

    def import_resources(self):
        source_client = self.config.obj.get("source_client")
        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing monitors %s", e)
            return

        self.import_resources_concurrently(monitors, resp)

        # Write resources to file
        self.write_resources_file("source")

    def process_resource_import(self, monitor, monitors):
        monitors[monitor["id"]] = monitor

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        simple_monitors = {}
        composite_monitors = {}

        for _id, monitor in source_resources.items():
            if monitor["type"] == "synthetics alert":
                continue
            if monitor["type"] == "composite":
                composite_monitors[_id] = monitor
            else:
                simple_monitors[_id] = monitor

        self.logger.info("Processing Simple Monitors")
        self.apply_resources_concurrently(simple_monitors, local_destination_resources, {})
        self.write_resources_file("destination", local_destination_resources)

        self.logger.info("Processing Composite Monitors")
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(composite_monitors, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, monitor, local_destination_resources, connection_resource_obj):
        self.connect_resources(monitor, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, monitor)
        else:
            self.create_resource(_id, monitor)

    def create_resource(self, _id, monitor):
        destination_client = self.config.obj.get("destination_client")

        try:
            resp = destination_client.post(self.base_path, monitor).json()
        except HTTPError as e:
            self.logger.error("error creating monitor: %s", e.response.text)
            return
        self.destination_resources[_id] = resp


    def update_resource(self, _id, monitor):
        destination_client = self.config.obj.get("destination_client")

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
