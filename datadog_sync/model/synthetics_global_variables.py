from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class SyntheticsGlobalVariables(BaseResource):
    resource_type = "synthetics_global_variables"
    resource_connections = {"synthetics_tests": ["parse_test_public_id"]}
    base_path = "/api/v1/synthetics/variables"
    non_nullable_attr = ["parse_test_public_id", "parse_test_options"]
    excluded_attributes = [
        "root['id']",
        "root['modified_at']",
        "root['created_at']",
        "root['parse_test_extracted_at']",
        "root['created_by']",
        "root['is_totp']",
        "root['parse_test_name']",
    ]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_global_variables: %s", e)
            return

        self.import_resources_concurrently(resp["variables"])

    def process_resource_import(self, synthetics_global_variable):
        if not self.config.filter.is_applicable(self.resource_type, synthetics_global_variable):
            return

        self.source_resources[synthetics_global_variable["id"]] = synthetics_global_variable

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        destination_global_variables = self.get_destination_global_variables()

        self.apply_resources_concurrently(
            connection_resource_obj,
            destination_global_variables=destination_global_variables,
        )

    def prepare_resource_and_apply(
        self,
        _id,
        synthetics_global_variable,
        connection_resource_obj,
        **kwargs,
    ):
        destination_global_variables = kwargs.get("destination_global_variables")

        self.connect_resources(synthetics_global_variable, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, synthetics_global_variable)
        elif synthetics_global_variable["name"] in destination_global_variables:
            self.update_existing_resource(_id, synthetics_global_variable, destination_global_variables)
        else:
            self.create_resource(_id, synthetics_global_variable)

    def create_resource(self, _id, synthetics_global_variable):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(synthetics_global_variable)
        self.remove_non_nullable_attributes(synthetics_global_variable)

        try:
            resp = destination_client.post(self.base_path, synthetics_global_variable).json()
        except HTTPError as e:
            self.logger.error("error creating synthetics_global_variable: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, synthetics_global_variable):
        destination_client = self.config.destination_client

        diff = self.check_diff(synthetics_global_variable, self.destination_resources[_id])
        if diff:
            self.remove_excluded_attr(synthetics_global_variable)
            self.remove_non_nullable_attributes(synthetics_global_variable)
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", synthetics_global_variable
                ).json()
            except HTTPError as e:
                self.logger.error("error updating synthetics_global_variable: %s", e.response.text)
                return
            self.destination_resources[_id].update(resp)

    def update_existing_resource(self, _id, synthetics_global_variable, destination_global_variables):
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
            self.destination_resources[_id] = resp
        else:
            self.destination_resources[_id] = destination_global_variables[synthetics_global_variable["name"]]

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
