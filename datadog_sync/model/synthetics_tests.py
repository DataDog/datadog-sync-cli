import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "synthetics_tests"
EXCLUDED_ATTRIBUTES = [
    "root['deleted_at']",
    "root['org_id']",
    "root['public_id']",
    "root['monitor_id']",
    "root['modified_at']",
    "root['created_at']",
]
EXCLUDED_ATTRIBUTES_RE = ["updatedAt"]
BASE_PATH = "/api/v1/synthetics/tests"


class SyntheticsTests(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        synthetics_tests = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_PATH).json()
        except HTTPError as e:
            log.error("error importing synthetics_tests: %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource, synthetics_test, synthetics_tests)
                    for synthetics_test in resp["tests"]
                ]
            )

        # Write resources to file
        self.write_resources_file("source", synthetics_tests)

    def process_resource(self, synthetics_test, synthetics_tests):
        synthetics_tests[synthetics_test["public_id"]] = synthetics_test

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        synthetics_test,
                        local_destination_resources,
                        connection_resource_obj,
                    )
                    for _id, synthetics_test in source_resources.items()
                ]
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, synthetics_test, local_destination_resources, connection_resource_obj=None
    ):
        destination_client = self.ctx.obj.get("destination_client")
        if self.resource_connections:
            self.connect_resources(synthetics_test, connection_resource_obj)

        self.remove_excluded_attr(synthetics_test)

        if _id in local_destination_resources:
            diff = DeepDiff(
                synthetics_test,
                local_destination_resources[_id],
                ignore_order=True,
                exclude_regex_paths=EXCLUDED_ATTRIBUTES_RE,
                exclude_paths=self.excluded_attributes,
            )
            if diff:
                try:
                    resp = destination_client.put(
                        self.base_path + f"/{local_destination_resources[_id]['public_id']}", synthetics_test
                    ).json()
                except HTTPError as e:
                    log.error("error creating synthetics_test: %s", e.response.text)
                    return
                local_destination_resources[_id] = resp
        else:
            try:
                resp = destination_client.post(self.base_path, synthetics_test).json()
            except HTTPError as e:
                log.error("error creating synthetics_test: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
