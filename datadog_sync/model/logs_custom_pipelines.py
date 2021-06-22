from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "logs_custom_pipelines"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['type']",
    "root['is_read_only']",
]
BASE_PATH = "/api/v1/logs/config/pipelines"


class LogsCustomPipelines(BaseResource):
    resource_type = "logs_custom_pipelines"

    source_resources = {}
    destination_resources = {}

    def __init__(self, config):
        super().__init__(config, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        logs_custom_pipelines = {}
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing logs_custom_pipelines %s", e)
            return

        self.import_resources_concurrently(logs_custom_pipelines, resp)

        # Write resources to file

    def process_resource_import(self, logs_custom_pipeline):
        if not logs_custom_pipeline["is_read_only"]:
            self.source_resources[logs_custom_pipeline["id"]] = logs_custom_pipeline

    def apply_resources(self):
        self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_sequentially(source_resources, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

        log.info(f"connection_resource_obj: {connection_resource_obj}")

        for _id, logs_custom_pipeline in self.source_resources.items():
            self.prepare_resource_and_apply(
                _id,
                logs_custom_pipeline,
                connection_resource_obj,
            )

    def prepare_resource_and_apply(self, _id, logs_custom_pipeline, connection_resource_obj, **kwargs):
        if self.resource_connections:
            self.connect_resources(logs_custom_pipeline, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, logs_custom_pipeline)
        else:
            self.create_resource(_id, logs_custom_pipeline)

    def create_resource(self, _id, logs_custom_pipeline):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(logs_custom_pipeline)

        try:
            resp = destination_client.post(self.base_path, logs_custom_pipeline).json()
        except HTTPError as e:
            self.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, logs_custom_pipeline):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(logs_custom_pipeline)

        diff = self.check_diff(logs_custom_pipeline, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", logs_custom_pipeline
                ).json()
            except HTTPError as e:
                self.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
                return
            self.destination_resources[_id] = resp
