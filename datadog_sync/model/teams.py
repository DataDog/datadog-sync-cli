# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Teams(BaseResource):
    resource_type = "teams"
    resource_config = ResourceConfig(
        base_path="/api/v2/team",
        excluded_attributes=[
            "id",
            "relationships",
            "attributes.created_at",
            "attributes.modified_at",
            "attributes.user_count",
            "attributes.link_count",
            # Ignored fields until further discussion with API owners
            "attributes.avatar",
            "attributes.banner",
            "attributes.visible_modules",
            "attributes.hidden_modules",
            "attributes.summary",
        ],
    )
    # Additional Teams specific attributes
    pagination_config = PaginationConfig(remaining_func=lambda *args: 1)
    destination_teams: Dict[str, Dict] = {}

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config,
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        client = self.config.destination_client
        resp = await self.get_resources(client)
        for r in resp:
            self.destination_teams[f"{r['attributes']['name']}:{r['attributes']['handle']}"] = r

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        k = f"{resource['attributes']['name']}:{resource['attributes']['handle']}"
        if k in self.destination_teams:
            self.resource_config.destination_resources[_id] = self.destination_teams[k]
            return await self.update_resource(_id, resource)

        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Teams, self).connect_id(key, r_obj, resource_to_connect)
