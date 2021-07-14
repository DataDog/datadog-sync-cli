from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class ServiceLevelObjectives(BaseResource):
    resource_type = "service_level_objectives"
    resource_connections = {"monitors": ["monitor_ids"], "synthetics_tests": []}
    base_path = "/api/v1/slo"
    excluded_attributes = [
        "root['creator']",
        "root['id']",
        "root['monitor_ids']",
        "root['created_at']",
        "root['modified_at']",
    ]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing slo %s", e)
            return

        self.import_resources_concurrently(resp["data"])

    def process_resource_import(self, slo):
        if not self.filter(slo):
            return

        self.source_resources[slo["id"]] = slo

    def apply_resources(self):
        self.logger.info("Processing service_level_objectives")

        connection_resource_obj = self.get_connection_resources()

        self.apply_resources_concurrently(
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

    def connect_id(self, key, r_obj, resource_to_connect):
        monitors = self.config.resources["monitors"].destination_resources
        synthetics_tests = self.config.resources["synthetics_tests"].destination_resources

        for i in range(len(r_obj[key])):
            _id = str(r_obj[key][i])
            # Check if resource exists in monitors
            if _id in monitors:
                r_obj[key][i] = monitors[_id]["id"]
                continue
            # Fall back on Synthetics and check
            found = False
            for k, v in synthetics_tests.items():
                if k.endswith(_id):
                    r_obj[key][i] = v["monitor_id"]
                    found = True
                    break
            if not found:
                raise
