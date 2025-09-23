# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsMobileApplications(BaseResource):
    resource_type = "synthetics_mobile_applications"
    resource_config = ResourceConfig(
        base_path="/api/unstable/synthetics/mobile/applications",
        excluded_attributes=[
            "id",
            "created_at",
            "versions",
        ],
        non_nullable_attr=[
            "framework",
        ],
        null_values={
            "framework": [""],
        },
    )
    # Additional Synthetics Mobile Applications specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)
        return resp["applications"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource.pop("versions", None)
        resp = await destination_client.post(self.resource_config.base_path, resource)
        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]

        # resource exists so we can update it
        resource["id"] = destination_id
        payload = {"data": resource}
        resp = await destination_client.put(
            self.resource_config.base_path + "/" + destination_id,
            payload,
        )
        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
