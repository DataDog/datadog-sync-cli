# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResourceModel, ResourceConfig
from datadog_sync.utils.resource_utils import ResourceConnectionError


class SyntheticsGlobalVariables(BaseResourceModel):
    resource_type = "synthetics_global_variables"
    resource_config = ResourceConfig(
        resource_connections={"synthetics_tests": ["parse_test_public_id"]},
        base_path="/api/v1/synthetics/variables",
        non_nullable_attr=["parse_test_public_id", "parse_test_options"],
        excluded_attributes=[
            "root['id']",
            "root['modified_at']",
            "root['created_at']",
            "root['parse_test_extracted_at']",
            "root['created_by']",
            "root['is_totp']",
            "root['parse_test_name']",
        ],
    )
    # Additional SyntheticsGlobalVariables specific attributes
    destination_global_variables = None

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing synthetics_global_variables: %s", e)
            return []

        return resp["variables"]

    def import_resource(self, resource) -> None:
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, resource) -> None:
        pass

    def pre_apply_hook(self, resources) -> Optional[list]:
        self.destination_global_variables = self.get_destination_global_variables()
        return

    def create_resource(self, _id, resource) -> None:
        if resource["name"] in self.destination_global_variables:
            self.resource_config.destination_resources[_id] = self.destination_global_variables[resource["name"]]
            self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client
        try:
            resp = destination_client.post(self.resource_config.base_path, resource).json()
        except HTTPError as e:
            self.config.logger.error("error creating synthetics_global_variable: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.put(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", resource
            ).json()
        except HTTPError as e:
            self.config.logger.error("error updating synthetics_global_variable: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id].update(resp)

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        found = False
        for k, v in resources.items():
            if k.startswith(r_obj[key]):
                r_obj[key] = v["public_id"]
                found = True
                break
        if not found:
            raise ResourceConnectionError(resource_to_connect, _id=r_obj[key])

    def get_destination_global_variables(self):
        destination_global_variable_obj = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for variable in resp:
            destination_global_variable_obj[variable["name"]] = variable

        return destination_global_variable_obj
