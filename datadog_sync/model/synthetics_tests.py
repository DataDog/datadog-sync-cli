from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class SyntheticsTests(BaseResource):
    resource_type = "synthetics_tests"
    resource_connections = {"synthetics_private_locations": ["locations"]}
    base_path = "/api/v1/synthetics/tests"
    excluded_attributes = [
        "root['deleted_at']",
        "root['org_id']",
        "root['public_id']",
        "root['monitor_id']",
        "root['modified_at']",
        "root['created_at']",
    ]
    excluded_attributes_re = ["updatedat", "notify_audit", "locked", "include_tags", "new_host_delay", "notify_no_data"]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_tests: %s", e)
            return

        self.import_resources_concurrently(resp["tests"])

    def process_resource_import(self, synthetics_test):
        self.source_resources[f"{synthetics_test['public_id']}#{synthetics_test['monitor_id']}"] = synthetics_test

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(self.source_resources, connection_resource_obj)

    def prepare_resource_and_apply(self, _id, synthetics_test, connection_resource_obj):
        if self.resource_connections:
            self.connect_resources(synthetics_test, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, synthetics_test)
        else:
            self.create_resource(_id, synthetics_test)

    def create_resource(self, _id, synthetics_test):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(synthetics_test)

        try:
            resp = destination_client.post(self.base_path, synthetics_test).json()
        except HTTPError as e:
            self.logger.error("error creating synthetics_test: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, synthetics_test):
        destination_client = self.config.destination_client

        diff = self.check_diff(synthetics_test, self.destination_resources[_id])
        if diff:
            self.remove_excluded_attr(synthetics_test)
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['public_id']}", synthetics_test
                ).json()
            except HTTPError as e:
                self.logger.error("error creating synthetics_test: %s", e.response.text)
                return
            self.destination_resources[_id] = resp
