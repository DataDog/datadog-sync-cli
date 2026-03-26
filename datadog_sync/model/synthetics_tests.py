# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

import aiohttp
import certifi
import json
import ssl
from copy import deepcopy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from yarl import URL

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.model.synthetics_mobile_applications_versions import SyntheticsMobileApplicationsVersions

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

_FILE_DOWNLOAD_PATH = "/api/v2/synthetics/tests/{}/files/download"


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
                "options.mobileApplication.applicationId",
            ],
            "synthetics_mobile_applications_versions": [
                "mobileApplicationsVersions",
                "options.mobileApplication.referenceId",
            ],
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
        deep_diff_config={
            "ignore_order": True,
            "exclude_regex_paths": [r".*\['bucketKey'\]"],
        },
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
    network_test_path: str = "/api/v2/synthetics/tests/network/{}"
    network_base_path: str = "/api/v2/synthetics/tests/network"
    network_delete_path: str = "/api/v2/synthetics/tests/bulk-delete"
    get_params = {"include_metadata": "true"}
    versions: List = []

    @staticmethod
    def _unwrap_network_response(resp: Dict) -> Dict:
        """Unwrap a v2 network test response into a flat resource dict."""
        resource = resp["data"]["attributes"]
        resource["public_id"] = resp["data"]["id"]
        resource["type"] = "network"
        return resource

    @staticmethod
    def _wrap_network_request(resource: Dict) -> Dict:
        """Wrap a flat resource dict into a v2 network test request body."""
        resource.pop("public_id", None)
        return {"data": {"attributes": resource, "type": "network"}}

    async def _get_test(self, client: CustomClient, test_type: str, public_id: str) -> Dict:
        """Fetch a single test, handling v2 envelope for network tests."""
        if test_type == "network":
            resp = await client.get(self.network_test_path.format(public_id), params=self.get_params)
            return self._unwrap_network_response(resp)
        path = self.resource_config.base_path + f"/{test_type}/{public_id}"
        return await client.get(path, params=self.get_params)

    async def _create_test(self, client: CustomClient, test_type: str, resource: Dict) -> Dict:
        """Create a test, handling v2 envelope for network tests."""
        if test_type == "network":
            body = self._wrap_network_request(resource)
            resp = await client.post(self.network_base_path, body)
            return self._unwrap_network_response(resp)
        return await client.post(self.resource_config.base_path + f"/{test_type}", resource)

    async def _update_test(self, client: CustomClient, public_id: str, resource: Dict) -> Dict:
        """Update a test, handling v2 envelope for network tests."""
        if resource.get("type") == "network":
            body = self._wrap_network_request(resource)
            resp = await client.put(self.network_base_path + f"/{public_id}", body)
            return self._unwrap_network_response(resp)
        return await client.put(self.resource_config.base_path + f"/{public_id}", resource)

    async def _delete_test(self, client: CustomClient, test_type: str, public_id: str) -> None:
        """Delete a test, handling v2 envelope for network tests."""
        if test_type == "network":
            body = {
                "data": {
                    "type": "delete_tests_request",
                    "attributes": {"public_ids": [public_id]},
                }
            }
            await client.post(self.network_delete_path, body)
        else:
            body = {"public_ids": [public_id]}
            await client.post(self.resource_config.base_path + "/delete", body)

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(
            self.resource_config.base_path,
            params=self.get_params,
        )
        versions = SyntheticsMobileApplicationsVersions(self.config)
        self.versions = await versions.get_resources(client)
        return resp["tests"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        if _id:
            try:
                resource = await source_client.get(
                    self.browser_test_path.format(_id),
                    params=self.get_params,
                )
            except Exception:
                try:
                    resource = await source_client.get(
                        self.api_test_path.format(_id),
                        params=self.get_params,
                    )
                except Exception:
                    try:
                        resource = await source_client.get(
                            self.mobile_test_path.format(_id),
                            params=self.get_params,
                        )
                    except Exception:
                        resp = await source_client.get(
                            self.network_test_path.format(_id),
                            params=self.get_params,
                        )
                        resource = self._unwrap_network_response(resp)

        resource = cast(dict, resource)
        _id = resource["public_id"]
        if resource.get("type") == "browser":
            resource = await source_client.get(
                self.browser_test_path.format(_id),
                params=self.get_params,
            )
        elif resource.get("type") == "api":
            resource = await source_client.get(
                self.api_test_path.format(_id),
                params=self.get_params,
            )
        elif resource.get("type") == "mobile":
            resource = await source_client.get(
                self.mobile_test_path.format(_id),
                params=self.get_params,
            )
            versions = [
                i["id"]
                for i in self.versions
                if i["application_id"] == resource["options"]["mobileApplication"]["applicationId"]
            ]
            resource["mobileApplicationsVersions"] = list(set(versions))
        elif resource.get("type") == "network":
            resp = await source_client.get(
                self.network_test_path.format(_id),
                params=self.get_params,
            )
            resource = self._unwrap_network_response(resp)

        resource = cast(dict, resource)
        return f"{resource['public_id']}#{resource['monitor_id']}", resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        # Inject metadata.disaster_recovery so diff/sync compares source status with
        # destination's metadata.disaster_recovery.source_status and triggers update when they differ.
        source = self.config.state.source[self.resource_type].get(_id, resource)
        source_public_id = source.get("public_id", "")
        source_status = source.get("status") or "live"
        resource.setdefault("metadata", {})["disaster_recovery"] = {
            "source_public_id": source_public_id,
            "source_status": source_status,
        }

    async def pre_apply_hook(self) -> None:
        pass

    @staticmethod
    def _replace_variable_public_id(resource: Dict, source_public_id: str, dest_public_id: str) -> bool:
        """Rewrite variable pattern/example to use the destination test's public_id.

        Variables can embed the test's public_id in their pattern and example fields
        (e.g. email variables use <public_id>.<random>@synthetics.dtdg.co for routing,
        and other variables may reference {{ public-id }}). When a test is synced, these
        fields still contain the source public_id and must be updated to match the
        destination test.

        Returns True if any replacements were made.
        """
        replaced = False
        for var in resource.get("config", {}).get("variables", []):
            if "pattern" in var and source_public_id in var["pattern"]:
                var["pattern"] = var["pattern"].replace(source_public_id, dest_public_id)
                replaced = True
            if "example" in var and source_public_id in var["example"]:
                var["example"] = var["example"].replace(source_public_id, dest_public_id)
                replaced = True
        return replaced

    @staticmethod
    def _get_file_lists_with_bucket_keys(resource: Dict) -> List[Tuple[List, str]]:
        """Return (files_list, bucket_key_prefix) for all file lists that contain files with a bucketKey.

        Covers:
        - API test request files: config.request.files[] (prefix: api-upload-file)
        - Multistep API test step files: config.steps[].request.files[] (prefix: api-upload-file)
        - Browser test step files: steps[].params.files[] (prefix: browser-upload-file-step)
        """
        result = []
        request_files = resource.get("config", {}).get("request", {}).get("files", [])
        if any("bucketKey" in f for f in request_files):
            result.append((request_files, "api-upload-file"))
        for step in resource.get("config", {}).get("steps", []):
            step_files = step.get("request", {}).get("files", [])
            if any("bucketKey" in f for f in step_files):
                result.append((step_files, "api-upload-file"))
        for step in resource.get("steps", []):
            step_files = step.get("params", {}).get("files", [])
            if any("bucketKey" in f for f in step_files):
                result.append((step_files, "browser-upload-file-step"))
        return result

    async def _download_file(self, source_public_id: str, bucket_key: str) -> bytes:
        """Download a file from the source org via the presigned URL endpoint."""
        source_client = self.config.source_client
        resp = await source_client.post(
            _FILE_DOWNLOAD_PATH.format(source_public_id),
            {"bucketKey": bucket_key},
        )
        if isinstance(resp, dict):
            presigned_url = resp["url"]
        else:
            presigned_url = resp.strip('"')

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.get(URL(presigned_url, encoded=True)) as response:
                return await response.read()

    async def _replicate_files(self, source_public_id: str, resource: Dict) -> None:
        """Download files from source and inject content inline.

        The create/update API stores the file in R2 and generates a new
        bucketKey for the destination.  Files whose download fails are
        removed from the list so the API never receives an invalid entry.
        """
        for files_list, _prefix in self._get_file_lists_with_bucket_keys(resource):
            to_remove = []
            for file_dict in files_list:
                bucket_key = file_dict.get("bucketKey", "")
                if not bucket_key:
                    continue
                try:
                    raw = await self._download_file(source_public_id, bucket_key)
                    content = json.loads(raw)
                    file_dict.pop("bucketKey")
                    file_dict["content"] = content
                    file_dict["size"] = len(content)
                except Exception as e:
                    self.config.logger.error(
                        f"Failed to download file {bucket_key} from source test {source_public_id}; "
                        f"removing file from payload. Error: {e}"
                    )
                    to_remove.append(file_dict)
            for f in to_remove:
                files_list.remove(f)

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        test_type = resource["type"]
        resource.pop("mobileApplicationsVersions", None)

        # Force status to "paused" for new tests to prevent immediate execution
        # on destination during failover scenarios. Status can be manually changed after creation.
        resource["status"] = "paused"

        source_public_id = _id.split("#")[0]

        await self._replicate_files(source_public_id, resource)

        resp = await self._create_test(destination_client, test_type, resource)

        # Fix variables that embed the source public_id.
        dest_public_id = resp["public_id"]
        needs_update = False
        if source_public_id != dest_public_id:
            needs_update = self._replace_variable_public_id(resource, source_public_id, dest_public_id)

        if needs_update:
            resp = await self._update_test(destination_client, dest_public_id, resource)

        # Persist metadata in state so destination JSON has it and diffs compare correctly.
        if resource.get("metadata"):
            resp.setdefault("metadata", {}).update(deepcopy(resource["metadata"]))
        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource.pop("mobileApplicationsVersions", None)

        source_public_id = _id.split("#")[0]
        dest_public_id = self.config.state.destination[self.resource_type][_id]["public_id"]
        self._replace_variable_public_id(resource, source_public_id, dest_public_id)

        await self._replicate_files(source_public_id, resource)

        resp = await self._update_test(destination_client, dest_public_id, resource)
        # Persist metadata in state so destination JSON has it and diffs compare correctly.
        if resource.get("metadata"):
            resp.setdefault("metadata", {}).update(deepcopy(resource["metadata"]))
        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        dest_resource = self.config.state.destination[self.resource_type][_id]
        await self._delete_test(destination_client, dest_resource.get("type"), dest_resource["public_id"])

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
        elif resource_to_connect == "synthetics_mobile_applications_versions" and key == "referenceId":
            # When referenceType is "latest", referenceId contains the application ID, not a version ID.
            # Connect it against synthetics_mobile_applications instead.
            if r_obj.get("referenceType") == "latest":
                return super(SyntheticsTests, self).connect_id(key, r_obj, "synthetics_mobile_applications")
            return super(SyntheticsTests, self).connect_id(key, r_obj, resource_to_connect)
        else:
            return super(SyntheticsTests, self).connect_id(key, r_obj, resource_to_connect)
