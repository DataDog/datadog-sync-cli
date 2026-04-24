# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsTestSuites(BaseResource):
    resource_type = "synthetics_test_suites"
    resource_config = ResourceConfig(
        resource_connections={
            "synthetics_tests": ["attributes.tests.public_id"],
        },
        base_path="/api/v2/synthetics/suites",
        excluded_attributes=[
            "id",
            "attributes.public_id",
            "attributes.created_at",
            "attributes.modified_at",
            "attributes.created_by",
            "attributes.modified_by",
            "attributes.monitor_id",
            "attributes.org_id",
            "attributes.version",
            "attributes.version_uuid",
            "attributes.overall_state",
            "attributes.overall_state_modified",
            "attributes.options.slo_id",
        ],
        tagging_config=TaggingConfig(path="attributes.tags"),
        skip_resource_mapping=True,
    )
    search_path = "/api/v2/synthetics/suites/search"
    bulk_delete_path = "/api/v2/synthetics/suites/bulk-delete"

    _SEARCH_PAGE_SIZE = 100

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        suites: List[Dict] = []
        start = 0
        while True:
            resp = await client.get(
                self.search_path,
                params={"start": start, "count": self._SEARCH_PAGE_SIZE},
            )
            page = resp["data"]["attributes"]["suites"]
            suites.extend(page)
            if len(page) < self._SEARCH_PAGE_SIZE:
                break
            start += len(page)
        return suites

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        if _id:
            resp = await source_client.get(self.resource_config.base_path + f"/{_id}")
        else:
            resp = await source_client.get(self.resource_config.base_path + f"/{resource['public_id']}")

        return resp["data"]["attributes"]["public_id"], resp["data"]

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)
        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dest_public_id = self.config.state.destination[self.resource_type][_id]["attributes"]["public_id"]
        payload = {"data": resource}
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{dest_public_id}",
            payload,
        )
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        dest_public_id = self.config.state.destination[self.resource_type][_id]["attributes"]["public_id"]
        await destination_client.post(
            self.bulk_delete_path,
            {"data": {"type": "delete_suites_request", "attributes": {"public_ids": [dest_public_id]}}},
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        if resource_to_connect == "synthetics_tests":
            self.config.state.ensure_resource_type_loaded("synthetics_tests")
            resources = self.config.state.destination[resource_to_connect]
            failed_connections = []
            source_public_id = str(r_obj[key])
            # synthetics_tests state keys are "{public_id}#{monitor_id}"
            # Find the key that starts with the source public_id
            for state_key, dest_resource in resources.items():
                if state_key.startswith(source_public_id + "#"):
                    r_obj[key] = dest_resource["public_id"]
                    return failed_connections
            failed_connections.append(source_public_id)
            return failed_connections

        return super().connect_id(key, r_obj, resource_to_connect)
