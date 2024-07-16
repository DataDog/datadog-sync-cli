# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class MetricTagConfigurations(BaseResource):
    resource_type = "metric_tag_configurations"
    resource_config = ResourceConfig(
        base_path="/api/v2/metrics",
        excluded_attributes=["attributes.created_at", "attributes.modified_at"],
    )
    # Additional MetricTagConfigurations specific attributes
    destination_metric_tag_configurations: Dict[str, Dict] = dict()

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path, params={"filter[configured]": "true"})

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}/tags"))["data"]

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_metric_tag_configurations = await self.get_destination_metric_tag_configuration()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if _id in self.destination_metric_tag_configurations:
            self.config.storage.data[self.resource_type].destination[_id] = self.destination_metric_tag_configurations[
                _id
            ]
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(
            self.resource_config.base_path + f"/{self.config.storage.data[self.resource_type].source[_id]['id']}/tags",
            payload,
        )

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        if "attributes" in resource:
            resource["attributes"].pop("metric_type", None)
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path
            + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}/tags",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path
            + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}/tags"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    async def get_destination_metric_tag_configuration(self) -> Dict[str, Dict]:
        destination_metric_tag_configurations = {}
        destination_client = self.config.destination_client

        resp = await self.get_resources(destination_client)
        for metric_tag_config in resp:
            destination_metric_tag_configurations[metric_tag_config["id"]] = metric_tag_config

        return destination_metric_tag_configurations
