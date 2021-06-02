import re
import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "synthetics_global_variables"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['modified_at']",
    "root['created_at']",
    "root['parse_test_extracted_at']",
    "root['created_by']",
    "root['is_totp']",
    "root['parse_test_name']",
]
BASE_PATH = "/api/v1/synthetics/variables"
RESOURCE_CONNECTIONS = {"synthetics_tests": ["parse_test_public_id"]}


class SyntheticsGlobalVariables(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCE_CONNECTIONS,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
        )

    def import_resources(self):
        synthetics_global_variables = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_PATH).json()
        except HTTPError as e:
            log.error("error importing synthetics_global_variables: %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource, synthetics_global_variable, synthetics_global_variables)
                    for synthetics_global_variable in resp["variables"]
                ]
            )

        # Write resources to file
        self.write_resources_file("source", synthetics_global_variables)

    def process_resource(self, synthetics_global_variable, synthetics_global_variables):
        synthetics_global_variables[synthetics_global_variable["id"]] = synthetics_global_variable

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        synthetics_global_variable,
                        local_destination_resources,
                        connection_resource_obj,
                    )
                    for _id, synthetics_global_variable in source_resources.items()
                ]
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, synthetics_global_variable, local_destination_resources, connection_resource_obj=None
    ):
        destination_client = self.ctx.obj.get("destination_client")

        if self.resource_connections:
            self.connect_resources(synthetics_global_variable, connection_resource_obj)

        self.remove_excluded_attr(synthetics_global_variable)

        if synthetics_global_variable["parse_test_public_id"] is None:
            synthetics_global_variable.pop("parse_test_public_id", None)
            synthetics_global_variable.pop("parse_test_options", None)

        if _id in local_destination_resources:
            diff = DeepDiff(
                synthetics_global_variable,
                local_destination_resources[_id],
                ignore_order=True,
                exclude_paths=self.excluded_attributes,
            )
            if diff:
                try:
                    resp = destination_client.put(
                        self.base_path + f"/{local_destination_resources[_id]['id']}", synthetics_global_variable
                    ).json()
                except HTTPError as e:
                    log.error("error updating synthetics_global_variable: %s", e.response.text)
                    return
                local_destination_resources[_id].update(resp)
        else:
            try:
                resp = destination_client.post(self.base_path, synthetics_global_variable).json()
            except HTTPError as e:
                log.error("error creating synthetics_global_variable: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
