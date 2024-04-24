# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from asyncio import sleep

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import DEFAULT_TAGS, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsPipelines(BaseResource):
    resource_type = "logs_pipelines"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipelines",
        excluded_attributes=["id", "type"],
    )
    # Additional LogsPipelines specific attributes
    destination_integration_pipelines: Dict[str, Dict] = dict()
    logs_intake_subdomain = "http-intake.logs"
    logs_intake_path = "/api/v2/logs"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        if resource["is_read_only"]:
            resource["name"] = resource["name"].lower()
            resource["processors"] = []

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_integration_pipelines = await self.get_destination_integration_pipelines()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        if resource["is_read_only"]:
            if resource["name"] not in self.destination_integration_pipelines:
                # Extract the source from the query
                source = resource["filter"]["query"].split("source:")[-1]
                payload = {
                    "ddsource": source,
                    "ddtags": ",".join(DEFAULT_TAGS),
                    "message": f"[datadog-sync-cli] Triggering creation of '{resource['name']}' integration pipeline",
                }

                # Submit a log to the logs intake API to trigger the creation of the integration pipeline
                await destination_client.post(self.logs_intake_path, payload, subdomain=self.logs_intake_subdomain)
                created = False
                for _ in range(3):
                    updated_pipelines = await self.get_destination_integration_pipelines()
                    if resource["name"] in updated_pipelines:
                        self.destination_integration_pipelines = updated_pipelines
                        created = True
                        break
                    else:
                        await sleep(5)

                if not created:
                    raise Exception(f"Max retry reached. Integration pipeline '{resource['name']}' pending creation")

            self.resource_config.destination_resources[_id] = self.destination_integration_pipelines[resource["name"]]
            diff = check_diff(self.resource_config, self.destination_integration_pipelines[resource["name"]], resource)
            if diff:
                return await self.update_resource(_id, resource)

            return _id, self.resource_config.destination_resources[_id]
        else:
            resp = await destination_client.post(self.resource_config.base_path, resource)

            return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        )

        if resp["is_read_only"]:
            resource.update(resp)
            resource["name"] = resource["name"].lower()

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        if self.resource_config.destination_resources[_id]["is_read_only"]:
            self.config.logger.warning("Integration pipelines cannot deleted. Removing resource from config only.")
        else:
            destination_client = self.config.destination_client
            await destination_client.delete(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
            )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    async def get_destination_integration_pipelines(self):
        destination_integration_pipelines_obj = {}
        destination_client = self.config.destination_client

        resp = await self.get_resources(destination_client)
        for pipeline in resp:
            if pipeline["is_read_only"]:
                # Normalize name for the integration pipeline
                pipeline["name"] = pipeline["name"].lower()
                pipeline["processors"] = []

                destination_integration_pipelines_obj[pipeline["name"]] = pipeline

        return destination_integration_pipelines_obj
