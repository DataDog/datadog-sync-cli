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
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        logs_custom_pipelines = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing logs_custom_pipelines %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource_import, logs_custom_pipeline, logs_custom_pipelines)
                    for logs_custom_pipeline in resp
                ]
            )

        # Write resources to file
        self.write_resources_file("source", logs_custom_pipelines)

    def process_resource_import(self, logs_custom_pipeline, logs_custom_pipelines):
        if not logs_custom_pipeline["is_read_only"]:
            logs_custom_pipelines[logs_custom_pipeline["id"]] = logs_custom_pipeline

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        for _id, logs_custom_pipeline in source_resources.items():
            self.prepare_resource_and_apply(
                _id,
                logs_custom_pipeline,
                local_destination_resources,
                connection_resource_obj,
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, logs_custom_pipeline, local_destination_resources, connection_resource_obj=None
    ):

        if self.resource_connections:
            self.connect_resources(logs_custom_pipeline, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, logs_custom_pipeline, local_destination_resources)
        else:
            self.create_resource(_id, logs_custom_pipeline, local_destination_resources)

    def create_resource(self, _id, logs_custom_pipeline, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_excluded_attr(logs_custom_pipeline)

        try:
            resp = destination_client.post(self.base_path, logs_custom_pipeline).json()
        except HTTPError as e:
            self.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
            return
        local_destination_resources[_id] = resp

    def update_resource(self, _id, logs_custom_pipeline, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_excluded_attr(logs_custom_pipeline)

        diff = self.check_diff(logs_custom_pipeline, local_destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", logs_custom_pipeline
                ).json()
            except HTTPError as e:
                self.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
