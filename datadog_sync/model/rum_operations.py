# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMOperations(BaseResource):
    """RUM operations.

    A RUM operation is a static definition (name, display_name, category, query,
    journey rules) tied to a RUM application via ``attributes.application_id``
    (a real body field, remapped to the destination app id via
    ``resource_connections``). The ``query`` fields in the journey are RUM query
    filters (e.g. ``@type:view``), not session ids, so no session-id stripping is
    required. List is via the ``/rum/operations/search`` GET endpoint; create is
    POST, update is PUT, delete is DELETE.
    """

    resource_type = "rum_operations"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/operations",
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.created_by",
            "attributes.updated_at",
            "attributes.updated_by",
            "attributes.org_id",
        ],
        resource_connections={
            "rum_applications": ["attributes.application_id"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMOperations specific attributes
    _search_path = "/api/v2/rum/operations/search"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self._search_path)

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
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]
        resource["id"] = destination_id
        payload = {"data": resource}
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{destination_id}",
            payload,
        )
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
