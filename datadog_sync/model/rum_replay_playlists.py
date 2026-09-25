# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMReplayPlaylists(BaseResource):
    """RUM replay playlists (shell only).

    A RUM replay playlist is a static shell definition (name, description).
    The playlist body itself contains no session-id references; the
    playlist->session associations live on the ``.../sessions`` sub-resources,
    which are intake-tied and out of scope. So this model syncs the playlist
    shell only (full CRUD) and does not call the ``.../sessions`` endpoints.
    """

    resource_type = "rum_replay_playlists"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/replay/playlists",
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.updated_at",
            "attributes.created_by",
            "attributes.session_count",
        ],
        skip_resource_mapping=True,
    )
    # Additional RUMReplayPlaylists specific attributes

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
