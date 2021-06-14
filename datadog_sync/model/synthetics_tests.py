import logging
from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger(__name__)


RESOURCE_TYPE = "synthetics_tests"
EXCLUDED_ATTRIBUTES = [
    "root['deleted_at']",
    "root['org_id']",
    "root['public_id']",
    "root['monitor_id']",
    "root['modified_at']",
    "root['created_at']",
]
EXCLUDED_ATTRIBUTES_RE = ["updatedAt", "notify_audit", "locked", "include_tags", "new_host_delay", "notify_no_data"]
BASE_PATH = "/api/v1/synthetics/tests"
RESOURCE_CONNECTIONS = {"synthetics_private_locations": ["locations"]}


class SyntheticsTests(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCE_CONNECTIONS,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            excluded_attributes_re=EXCLUDED_ATTRIBUTES_RE,
        )

    def import_resources(self):
        synthetics_tests = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_tests: %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource_import, synthetics_test, synthetics_tests)
                    for synthetics_test in resp["tests"]
                ]
            )

        # Write resources to file
        self.write_resources_file("source", synthetics_tests)

    def process_resource_import(self, synthetics_test, synthetics_tests):
        synthetics_tests[f"{synthetics_test['public_id']}#{synthetics_test['monitor_id']}"] = synthetics_test

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(source_resources, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, synthetics_test, local_destination_resources, connection_resource_obj=None
    ):

        if self.resource_connections:
            self.connect_resources(synthetics_test, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, synthetics_test, local_destination_resources)
        else:
            self.create_resource(_id, synthetics_test, local_destination_resources)

    def create_resource(self, _id, synthetics_test, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_excluded_attr(synthetics_test)

        try:
            resp = destination_client.post(self.base_path, synthetics_test).json()
        except HTTPError as e:
            self.logger.error("error creating synthetics_test: %s", e.response.text)
            return
        local_destination_resources[_id] = resp

    def update_resource(self, _id, synthetics_test, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")

        diff = self.check_diff(synthetics_test, local_destination_resources[_id])
        if diff:
            self.remove_excluded_attr(synthetics_test)
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['public_id']}", synthetics_test
                ).json()
            except HTTPError as e:
                self.logger.error("error creating synthetics_test: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
