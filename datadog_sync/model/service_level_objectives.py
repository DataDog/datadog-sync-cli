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
    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCES_TO_CONNECT,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
        )

    def import_resources(self):
        slos = {}
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing slo %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource_import, slo, slos) for slo in resp["data"]])

        # Write resources to file
        self.write_resources_file("source", slos)

    def process_resource_import(self, slo, slos):
        slos[slo["id"]] = slo

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()

        self.logger.info("Processing service_level_objectives")

        connection_resource_obj = self.get_connection_resources()

        self.apply_resources_concurrently(
            source_resources,
            local_destination_resources,
            connection_resource_obj,
        )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, slo, local_destination_resources, connection_resource_obj):
        self.connect_resources(slo, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, slo, local_destination_resources)
        else:
            self.create_resource(_id, slo, local_destination_resources)

    def create_resource(self, _id, slo, local_destination_resources):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, slo).json()
        except HTTPError as e:
            self.logger.error("error creating slo: %s", e.response.text)
            return

        # local_destination_resources[f"{_id}:{resp['monitor_id']}"] = resp
        local_destination_resources[_id] = resp["data"][0]

    def update_resource(self, _id, slo, local_destination_resources):
        destination_client = self.config.destination_client

        diff = self.check_diff(slo, local_destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(self.base_path + f"/{local_destination_resources[_id]['id']}", slo).json()
            except HTTPError as e:
                self.logger.error("error creating slo: %s", e.response.text)
                return
            local_destination_resources[_id] = resp["data"][0]
