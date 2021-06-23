from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "service_level_objectives"
EXCLUDED_ATTRIBUTES = [
    "root['creator']",
    "root['id']",
    "root['monitor_ids']",
    "root['created_at']",
    "root['modified_at']",
]
BASE_PATH = "/api/v1/slo"
RESOURCES_TO_CONNECT = {"monitors": ["monitor_ids"], "synthetics_tests": ["monitor_ids"]}


class ServiceLevelObjectives(BaseResource):
    resource_type = "service_level_objectives"

    source_resources = {}
    destination_resources = {}

    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCES_TO_CONNECT,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
        )

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing slo %s", e)
            return

        self.import_resources_concurrently(resp["data"])

    def process_resource_import(self, slo):
        self.source_resources[slo["id"]] = slo

    def apply_resources(self):
        self.open_resources()

        self.logger.info("Processing service_level_objectives")

        connection_resource_obj = self.get_connection_resources()

        self.apply_resources_concurrently(
            self.source_resources,
            connection_resource_obj,
        )

    def prepare_resource_and_apply(self, _id, slo, connection_resource_obj):
        self.connect_resources(slo, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, slo)
        else:
            self.create_resource(_id, slo)

    def create_resource(self, _id, slo):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, slo).json()
        except HTTPError as e:
            self.logger.error("error creating slo: %s", e.response.text)
            return

        self.destination_resources[_id] = resp["data"][0]

    def update_resource(self, _id, slo):
        destination_client = self.config.destination_client

        diff = self.check_diff(slo, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(self.base_path + f"/{self.destination_resources[_id]['id']}", slo).json()
            except HTTPError as e:
                self.logger.error("error creating slo: %s", e.response.text)
                return
            self.destination_resources[_id] = resp["data"][0]
