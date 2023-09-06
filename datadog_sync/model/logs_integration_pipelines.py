# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsIntegrationPipelines(BaseResource):
    resource_type = "logs_integration_pipelines"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipelines",
        excluded_attributes=["id", "is_read_only", "type", "processors", "filter"],
    )
    # Additional LogsIntegrationPipelines specific attributes
    destination_integration_pipelines: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()

        resource = cast(dict, resource)
        if not resource["is_read_only"]:
            return

        # Normalize name for the integration pipeline
        resource["name"] = resource["name"].lower()

        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        self.destination_integration_pipelines = self.get_destination_integration_pipelines()

    def create_resource(self, _id: str, resource: Dict) -> None:
        if resource["name"] not in self.destination_integration_pipelines:
            raise Exception(
                "resource cannot be created only updated." +
                f"Skipping sync. Enable integration pipeline {resource['name']}",
            )

        self.resource_config.destination_resources[_id] = self.destination_integration_pipelines[resource["name"]]
        self.update_resource(_id, resource)

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resource["filter"] = {}
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        # Normalize name for the integration pipeline
        resp["name"] = resp["name"].lower()

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        raise Exception("resource cannot be deleted.")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    def get_destination_integration_pipelines(self):
        destination_integration_pipelines_obj = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for pipeline in resp:
            if pipeline["is_read_only"]:
                # Normalize name for the integration pipeline
                pipeline["name"] = pipeline["name"].lower()

                destination_integration_pipelines_obj[pipeline["name"]] = pipeline

        return destination_integration_pipelines_obj
