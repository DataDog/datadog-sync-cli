from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


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
NON_NULLABLE_ATTRIBUTE = ["parse_test_public_id", "parse_test_options"]
RESOURCE_CONNECTIONS = {"synthetics_tests": ["parse_test_public_id"]}


class SyntheticsGlobalVariables(BaseResource):
    def __init__(self, config):
        super().__init__(
            config,
            RESOURCE_TYPE,
            BASE_PATH,
            resource_connections=RESOURCE_CONNECTIONS,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            non_nullable_attr=NON_NULLABLE_ATTRIBUTE,
        )

    def import_resources(self):
        synthetics_global_variables = {}
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_global_variables: %s", e)
            return

        self.import_resources_concurrently(synthetics_global_variables, resp["variables"])

        # Write resources to file
        self.write_resources_file("source", synthetics_global_variables)

    def process_resource_import(self, synthetics_global_variable, synthetics_global_variables):
        synthetics_global_variables[synthetics_global_variable["id"]] = synthetics_global_variable

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        destination_global_variables = self.get_destination_global_variables()

        self.apply_resources_concurrently(
            source_resources,
            local_destination_resources,
            connection_resource_obj,
            destination_global_variables=destination_global_variables,
        )
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self,
        _id,
        synthetics_global_variable,
        local_destination_resources,
        connection_resource_obj,
        **kwargs,
    ):
        destination_global_variables = kwargs.get("destination_global_variables")

        self.connect_resources(synthetics_global_variable, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, synthetics_global_variable, local_destination_resources)
        elif synthetics_global_variable["name"] in destination_global_variables:
            self.update_existing_resource(
                _id, synthetics_global_variable, local_destination_resources, destination_global_variables
            )
        else:
            self.create_resource(_id, synthetics_global_variable, local_destination_resources)

    def create_resource(self, _id, synthetics_global_variable, local_destination_resources):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(synthetics_global_variable)
        self.remove_non_nullable_attributes(synthetics_global_variable)

        try:
            resp = destination_client.post(self.base_path, synthetics_global_variable).json()
        except HTTPError as e:
            self.logger.error("error creating synthetics_global_variable: %s", e.response.text)
            return
        local_destination_resources[_id] = resp

    def update_resource(self, _id, synthetics_global_variable, local_destination_resources):
        destination_client = self.config.destination_client

        diff = self.check_diff(synthetics_global_variable, local_destination_resources[_id])
        if diff:
            self.remove_excluded_attr(synthetics_global_variable)
            self.remove_non_nullable_attributes(synthetics_global_variable)
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", synthetics_global_variable
                ).json()
            except HTTPError as e:
                self.logger.error("error updating synthetics_global_variable: %s", e.response.text)
                return
            local_destination_resources[_id].update(resp)

    def update_existing_resource(
        self, _id, synthetics_global_variable, local_destination_resources, destination_global_variables
    ):
        destination_client = self.config.destination_client

        diff = self.check_diff(
            synthetics_global_variable, destination_global_variables[synthetics_global_variable["name"]]
        )
        if diff:
            self.remove_excluded_attr(synthetics_global_variable)
            self.remove_non_nullable_attributes(synthetics_global_variable)
            try:
                resp = destination_client.put(
                    self.base_path + f"/{destination_global_variables[synthetics_global_variable['name']]['id']}",
                    synthetics_global_variable,
                ).json()
            except HTTPError as e:
                self.logger.error("error updating synthetics_global_variable: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
        else:
            local_destination_resources[_id] = destination_global_variables[synthetics_global_variable["name"]]

    def get_destination_global_variables(self):
        destination_global_variable_obj = {}
        destination_client = self.config.destination_client

        try:
            resp = destination_client.get(self.base_path).json()["variables"]
        except HTTPError as e:
            self.logger.error("error retrieving remote users: %s", e)
            return

        for variable in resp:
            destination_global_variable_obj[variable["name"]] = variable

        return destination_global_variable_obj
