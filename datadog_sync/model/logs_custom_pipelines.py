from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class LogsCustomPipelines(BaseResource):
    resource_type = "logs_custom_pipelines"
    resource_connections = None
    base_path = "/api/v1/logs/config/pipelines"
    excluded_attributes = [
        "root['id']",
        "root['type']",
        "root['is_read_only']",
    ]
    match_on = "name"

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing logs_custom_pipelines %s", e)
            return

        if self.config.import_existing:
            self.get_destination_existing_resources()

        self.import_resources_concurrently(resp)

    def process_resource_import(self, logs_custom_pipeline):
        if logs_custom_pipeline["is_read_only"]:
            return
        if not self.filter(logs_custom_pipeline):
            return

        self.source_resources[logs_custom_pipeline["id"]] = logs_custom_pipeline

        # Map existing resources
        if self.config.import_existing:
            if logs_custom_pipeline[self.match_on] in self.destination_existing_resources:
                existing_pipeline = self.destination_existing_resources[logs_custom_pipeline[self.match_on]]
                self.destination_resources[str(logs_custom_pipeline["id"])] = existing_pipeline

    def get_destination_existing_resources(self):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing logs_custom_pipelines %s", e)
            return

        for logs_custom_pipeline in resp:
            self.destination_existing_resources[logs_custom_pipeline[self.match_on]] = logs_custom_pipeline

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_sequentially(connection_resource_obj)

    def prepare_resource_and_apply(self, _id, logs_custom_pipeline, connection_resource_obj):
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
