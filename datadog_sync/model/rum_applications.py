# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMApplications(BaseResource):
    resource_type = "rum_applications"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/applications",
        excluded_attributes=[
            "id",
            "attributes.api_key_id",
            "attributes.application_id",
            "attributes.client_token",
            "attributes.created_at",
            "attributes.created_by_handle",
            "attributes.hash",
            "attributes.is_active",
            "attributes.ootb_metrics_installed",
            "attributes.org_id",
            "attributes.updated_at",
            "attributes.updated_by_handle",
            "attributes.product_analytics_preview_disabled",
            "attributes.product_scales.product_analytics_retention_scale.last_modified_at",
            "attributes.product_scales.product_analytics_retention_scale.state",
            "attributes.product_scales.rum_event_processing_scale.last_modified_at",
            "attributes.remote_config_id",
            "attributes.short_name",
        ],
    )
    # Additional RUM Applications specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        # the list endpoint doesn't return the whole resource, so pull them individually
        resources = []
        for partial_resource in resp["data"]:
            partial_resource_id = partial_resource["id"]
            whole_resource = (await client.get(self.resource_config.base_path + f"/{partial_resource_id}"))["data"]
            resources.append(whole_resource)

        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["type"] = "rum_application_create"
        payload = {"data": resource}
        post_resp = await destination_client.post(self.resource_config.base_path, payload)
        return _id, post_resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]

        # if the resource doesn't exist at the destination then create it
        existing_resources = await self.get_resources(destination_client)
        existing_resource_ids = [r["id"] for r in existing_resources]
        if destination_id not in existing_resource_ids:
            self.config.logger.debug(f"{destination_id} not found, creating it")
            return await self.create_resource(_id, resource)

        # resource exists so we can update it
        resource["type"] = "rum_application_update"
        resource["id"] = destination_id
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + "/" + destination_id,
            payload,
        )
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
