# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.model.synthetics_mobile_applications_versions import SyntheticsMobileApplicationsVersions

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsTests(BaseResource):
    resource_type = "synthetics_tests"
    resource_config = ResourceConfig(
        resource_connections={
            "synthetics_tests": ["steps.params.subtestPublicId"],
            "synthetics_private_locations": ["locations"],
            "synthetics_global_variables": [
                "config.configVariables.id",
                "config.variables.id",
            ],
            "roles": ["options.restricted_roles"],
            "rum_applications": ["options.rumSettings.applicationId"],
            "synthetics_mobile_applications": [
                "options.mobileApplication.referenceId",
                "options.mobileApplication.applicationId",
            ],
            "synthetics_mobile_applications_versions": ["mobileApplicationsVersions"],
        },
        base_path="/api/v1/synthetics/tests",
        excluded_attributes=[
            "created_at",
            "creator",
            "created_by",
            "deleted_at",
            "mobileApplicationsVersions",
            "modified_at",
            "modified_by",
            "monitor_id",
            "org_id",
            "public_id",
            "overall_state",
            "overall_state_modified",
            "status",  # Exclude status to prevent overwriting manual changes during sync
            "stepCount",
            "steps.public_id",
        ],
        non_nullable_attr=[
            "options.monitor_options.on_missing_data",
            "options.monitor_options.notify_audit",
            "options.monitor_options.new_host_delay",
            "options.monitor_options.include_tags",
            "steps",
        ],
        null_values={
            "on_missing_data": ["show_no_data"],
            "notify_audit": [False],
            "new_host_delay": [300],
            "include_tags": [True],
            "steps": [[]],
        },
        tagging_config=TaggingConfig(path="tags"),
    )
    # Additional SyntheticsTests specific attributes
    browser_test_path: str = "/api/v1/synthetics/tests/browser/{}"
    api_test_path: str = "/api/v1/synthetics/tests/api/{}"
    mobile_test_path: str = "/api/v1/synthetics/tests/mobile/{}"
    versions: List = []

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)
        versions = SyntheticsMobileApplicationsVersions(self.config)
        self.versions = await versions.get_resources(client)
        return resp["tests"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        if _id:
            try:
                resource = await source_client.get(self.browser_test_path.format(_id))
            except Exception:
                try:
                    resource = await source_client.get(self.api_test_path.format(_id))
                except Exception:
                    resource = await source_client.get(self.mobile_test_path.format(_id))

        resource = cast(dict, resource)
        _id = resource["public_id"]
        if resource.get("type") == "browser":
            resource = await source_client.get(self.browser_test_path.format(_id))
        elif resource.get("type") == "api":
            resource = await source_client.get(self.api_test_path.format(_id))
        elif resource.get("type") == "mobile":
            resource = await source_client.get(self.mobile_test_path.format(_id))
            versions = [
                i["id"]
                for i in self.versions
                if i["application_id"] == resource["options"]["mobileApplication"]["applicationId"]
            ]
            resource["mobileApplicationsVersions"] = list(set(versions))

        resource = cast(dict, resource)
        return f"{resource['public_id']}#{resource['monitor_id']}", resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        test_type = resource["type"]
        resource.pop("mobileApplicationsVersions", None)

        # Force status to "paused" for new tests to prevent immediate execution
        # on destination during failover scenarios. Status can be manually changed after creation.
        resource["status"] = "paused"

        resp = await destination_client.post(self.resource_config.base_path + f"/{test_type}", resource)
        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource.pop("mobileApplicationsVersions", None)
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['public_id']}",
            resource,
        )
        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        body = {"public_ids": [self.config.state.destination[self.resource_type][_id]["public_id"]]}
        await destination_client.post(self.resource_config.base_path + "/delete", body)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        failed_connections: List[str] = []
        if resource_to_connect == "synthetics_private_locations":
            pl = self.config.resources["synthetics_private_locations"]
            resources = self.config.state.destination[resource_to_connect]
            failed_connections = []

            for i, _id in enumerate(r_obj[key]):
                if pl.pl_id_regex.match(_id):
                    if _id in resources:
                        r_obj[key][i] = resources[_id]["id"]
                    else:
                        failed_connections.append(_id)
            return failed_connections
        elif resource_to_connect == "synthetics_tests":
            resources = self.config.state.destination[resource_to_connect]
            found = False
            for k, v in resources.items():
                if k.startswith(r_obj[key]):
                    r_obj[key] = v["public_id"]
                    found = True
                    break
            if not found:
                failed_connections.append(r_obj[key])
            return failed_connections
        else:
            return super(SyntheticsTests, self).connect_id(key, r_obj, resource_to_connect)
