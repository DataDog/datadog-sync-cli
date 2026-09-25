# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMTeamsOwnershipMappings(BaseResource):
    """RUM teams-ownership mappings.

    Mappings have no PATCH endpoint, so update is implemented as
    delete-then-recreate. ``attributes.application_id`` is a RUM application
    uuid remapped via ``resource_connections``. ``attributes.team_handle`` is a
    stable handle (teams are mapped by name:handle and the handle is preserved
    across orgs), so it is NOT remapped -- the dependency on teams is soft
    (documented) rather than a resource_connection (which would mis-remap a
    handle as a uuid).
    """

    resource_type = "rum_teams_ownership_mappings"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/config/teams-ownership/mappings",
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.created_by",
            "attributes.org_id",
        ],
        resource_connections={
            "rum_applications": ["attributes.application_id"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMTeamsOwnershipMappings specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        # create data has no id (server-assigned)
        resource.pop("id", None)
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)
        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # No PATCH endpoint -- implement update as delete-then-recreate.
        await self.delete_resource(_id)
        return await self.create_resource(_id, resource)

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
