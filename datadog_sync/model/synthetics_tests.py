import re

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class SyntheticsTests(BaseResource):
    resource_type = "synthetics_tests"
    resource_connections = {"synthetics_private_locations": ["locations"], "monitors": []}
    base_path = "/api/v1/synthetics/tests"
    excluded_attributes = [
        "root['deleted_at']",
        "root['org_id']",
        "root['public_id']",
        "root['monitor_id']",
        "root['modified_at']",
        "root['created_at']",
    ]
    excluded_attributes_re = ["updatedAt", "notify_audit", "locked", "include_tags", "new_host_delay", "notify_no_data"]
    validate_id_re = re.compile("[a-zA-Z0-9-]+#[0-9]+")

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_tests: %s", e)
            return

        self.import_resources_concurrently(resp["tests"])

    def process_resource_import(self, synthetics_test):
        if not self.filter(synthetics_test):
            return

        self.source_resources[synthetics_test["public_id"]] = synthetics_test

    def apply_resources(self):
        self.apply_resources_concurrently()
        # Creating synthetics tests also creates monitors. After creation dump the monitors resources.
        monitors_resource = self.config.resources["monitors"]
        monitors_resource.write_resources_file("destination", monitors_resource.destination_resources)

    def prepare_resource_and_apply(self, _id, synthetics_test):
        self.connect_resources(synthetics_test)

        if _id in self.destination_resources:
            self.update_resource(_id, synthetics_test)
        else:
            self.create_resource(_id, synthetics_test)

    def create_resource(self, _id, synthetics_test):
        destination_client = self.config.destination_client
        monitor_id = str(synthetics_test["monitor_id"])
        self.remove_excluded_attr(synthetics_test)

        try:
            resp = destination_client.post(self.base_path, synthetics_test).json()
        except HTTPError as e:
            self.logger.error("error creating synthetics_test: %s", e.response.text)
            return
        self.destination_resources[_id] = resp
        self.config.resources["monitors"].destination_resources[monitor_id] = {
            "id": resp["monitor_id"],
            "type": "synthetics alert",
        }

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
