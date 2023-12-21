# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsGlobalVariables(BaseResource):
    resource_type = "synthetics_global_variables"
    resource_config = ResourceConfig(
        resource_connections={"synthetics_tests": ["parse_test_public_id"]},
        base_path="/api/v1/synthetics/variables",
        non_nullable_attr=["parse_test_public_id", "parse_test_options", "is_fido", "is_totp"],
        excluded_attributes=[
            "id",
            "creator",
            "last_error",
            "modified_at",
            "created_at",
            "parse_test_extracted_at",
            "created_by",
            "is_totp",
            "parse_test_name",
            "attributes",
            "editor",
        ],
    )
    # Additional SyntheticsGlobalVariables specific attributes
    destination_global_variables: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()
        return resp["variables"]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        self.destination_global_variables = self.get_destination_global_variables()

    def create_resource(self, _id: str, resource: Dict) -> None:
        if resource["name"] in self.destination_global_variables:
            self.resource_config.destination_resources[_id] = self.destination_global_variables[resource["name"]]
            self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client

        if "value" not in resource["value"]:
            resource["value"]["value"] = "SECRET"

        resp = destination_client.post(self.resource_config.base_path, resource).json()

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        self.resource_config.destination_resources[_id].update(resp)

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        failed_connections = []
        found = False
        for k, v in resources.items():
            if k.startswith(r_obj[key]):
                r_obj[key] = v["public_id"]
                found = True
                break
        if not found:
            failed_connections.append(r_obj[key])
        return failed_connections

    def get_destination_global_variables(self) -> Dict[str, Dict]:
        destination_global_variable_obj = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for variable in resp:
            destination_global_variable_obj[variable["name"]] = variable

        return destination_global_variable_obj
