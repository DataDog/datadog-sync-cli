# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient
    from datadog_sync.model.synthetics_private_locations import SyntheticsPrivateLocations

class SyntheticsTests(BaseResource):
    resource_type = "synthetics_tests"
    resource_config = ResourceConfig(
        resource_connections={
            "synthetics_tests": ["steps.params.subtestPublicId"],
            "synthetics_private_locations": ["locations"],
            # "synthetics_global_variables": ["config.configVariables.id"],
        },
        base_path="/api/v1/synthetics/tests",
        excluded_attributes=["deleted_at", "org_id", "public_id", "monitor_id", "modified_at", "created_at", "creator"],
        excluded_attributes_re=[
            "updatedAt",
            "notify_audit",
            "locked",
            "include_tags",
            "new_host_delay",
            "notify_no_data",
        ],
    )
    # Additional SyntheticsTests specific attributes
    browser_test_path: str = "/api/v1/synthetics/tests/browser/{}"
    api_test_path: str = "/api/v1/synthetics/tests/api/{}"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["tests"]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        source_client = self.config.source_client
        if _id:
            try:
                resource = source_client.get(self.browser_test_path.format(_id)).json()
            except Exception:
                resource = source_client.get(self.api_test_path.format(_id)).json()

        resource = cast(dict, resource)
        _id = resource["public_id"]
        if resource.get("type") == "browser":
            resource = source_client.get(self.browser_test_path.format(_id)).json()
        elif resource.get("type") == "api":
            resource = source_client.get(self.api_test_path.format(_id)).json()

        resource = cast(dict, resource)
        self.remove_global_variables_from_config(resource)
        self.resource_config.source_resources[f"{resource['public_id']}#{resource['monitor_id']}"] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()
        self.remove_global_variables_from_config(resp)

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['public_id']}",
            resource,
        ).json()
        self.remove_global_variables_from_config(resp)

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        body = {"public_ids": [self.resource_config.destination_resources[_id]["public_id"]]}
        destination_client.post(self.resource_config.base_path + "/delete", body)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        failed_connections: List[str] = []
        if resource_to_connect == "synthetics_private_locations":
            pl = self.config.resources["synthetics_private_locations"]
            resources = self.config.resources[resource_to_connect].resource_config.destination_resources
            failed_connections = []

            for i, _id in enumerate(r_obj[key]):
                if pl.pl_id_regex.match(_id):
                    if _id in resources:
                        r_obj[key][i] = resources[_id]["id"]
                    else:
                        failed_connections.append(_id)
            return failed_connections
        elif resource_to_connect == "synthetics_tests":
            resources = self.config.resources[resource_to_connect].resource_config.destination_resources
            found = False
            for k, v in resources.items():
                if k.startswith(r_obj[key]):
                    r_obj[key] = v["public_id"]
                    found = True
                    break
            if not found:
                failed_connections.append(_id)
            return failed_connections
        else:
            return super(SyntheticsTests, self).connect_id(key, r_obj, resource_to_connect)

    @staticmethod
    def remove_global_variables_from_config(resource: Dict[str, Any]) -> Dict[str, Any]:
        if "config" in resource and "configVariables" in resource["config"]:
            for variables in resource["config"]["configVariables"]:
                if variables["type"] == "global":
                    resource["config"]["configVariables"].remove(variables)
        return resource
