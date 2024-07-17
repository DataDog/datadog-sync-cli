# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class AuthNMappings(BaseResource):
    resource_type = "authn_mappings"
    resource_config = ResourceConfig(
        base_path="/api/v2/authn_mappings",
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.modified_at",
            "attributes.saml_assertion_attribute_id",
            "relationships.saml_assertion_attribute",
        ],
        resource_connections={"roles": ["relationships.role.data.id"], "teams": ["relationships.team.data.id"]},
    )
    # Additional AuthNMappings specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        role_resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, params={"resource_type": "role"}
        )
        team_resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, params={"resource_type": "team"}
        )

        return role_resp + team_resp

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
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)
        self.remove_null_relationships(resp)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        d_id = self.config.state.destination[self.resource_type][_id]["id"]
        resource["id"] = d_id
        payload = {"data": resource}
        resp = await destination_client.patch(self.resource_config.base_path + f"/{d_id}", payload)
        self.remove_null_relationships(resp)

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(AuthNMappings, self).connect_id(key, r_obj, resource_to_connect)

    @staticmethod
    def remove_null_relationships(resp: Dict) -> None:
        if resp["data"]["relationships"].get("role", {}).get("data") is None:
            resp["data"]["relationships"].pop("role", None)
        if resp["data"]["relationships"].get("team", {}).get("data") is None:
            resp["data"]["relationships"].pop("team", None)

        return resp
