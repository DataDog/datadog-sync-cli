# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Roles(BaseResource):
    resource_type = "roles"
    resource_config = ResourceConfig(
        base_path="/api/v2/roles",
        excluded_attributes=["id", "attributes.created_at", "attributes.modified_at", "attributes.user_count"],
    )
    # Additional Roles specific attributes
    source_permissions: Dict = {}
    destination_permissions: Dict = {}
    destination_roles_mapping: Optional[Dict] = None
    permissions_base_path: str = "/api/v2/permissions"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(self.resource_config.base_path)

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client

        if not self.source_permissions:
            # Retrieve source permissions in the import step and cache it.
            # Ideally, this would be in the pre_apply_hook, but for the purposes of import/sync seperation
            # we are doing it here.
            try:
                source_permissions = (await source_client.get(self.permissions_base_path))["data"]
                # source_permissions = resp["data"]
                permissions = {}
                for permission in source_permissions:
                    permissions[permission["id"]] = permission["attributes"]["name"]
                self.source_permissions = permissions
            except CustomClientHTTPError as e:
                self.config.logger.warning("error retrieving permissions: %s", e)

        if _id:
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)
        if self.source_permissions and "permissions" in resource["relationships"]:
            for permission in resource["relationships"]["permissions"]["data"]:
                if permission["id"] in self.source_permissions:
                    permission["id"] = self.source_permissions[permission["id"]]

        return resource["id"], resource

    async def pre_apply_hook(self) -> None:
        self.destination_roles_mapping = await self.get_destination_roles_mapping()

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        await self.remap_permissions(resource)

    async def create_resource(self, _id, resource) -> Tuple[str, Dict]:
        if resource["attributes"]["name"] in self.destination_roles_mapping:
            role_copy = copy.deepcopy(resource)
            role_copy.update(self.destination_roles_mapping[resource["attributes"]["name"]])

            if check_diff(self.resource_config, resource, role_copy):
                self.config.state.destination[self.resource_type][_id] = role_copy
                return await self.update_resource(_id, resource)
            else:
                return _id, role_copy

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    async def remap_permissions(self, resource):
        if not self.destination_permissions:
            try:
                destination_permissions = (await self.config.destination_client.get(self.permissions_base_path))["data"]
                for permission in destination_permissions:
                    self.destination_permissions[permission["attributes"]["name"]] = permission["id"]
            except CustomClientHTTPError as e:
                self.config.logger.warning("error retrieving permissions: %s", e)
                return

        if "permissions" in resource["relationships"]:
            for permission in resource["relationships"]["permissions"]["data"]:
                if permission["id"] in self.destination_permissions:
                    permission["id"] = self.destination_permissions[permission["id"]]

    async def get_destination_roles_mapping(self):
        destination_client = self.config.destination_client
        destination_roles_mapping = {}

        # Destination roles mapping
        try:
            destination_roles_resp = await destination_client.paginated_request(destination_client.get)(
                self.resource_config.base_path
            )
        except CustomClientHTTPError as e:
            self.config.logger.error("error retrieving roles: %s", e)
            return destination_roles_mapping

        for role in destination_roles_resp:
            destination_roles_mapping[role["attributes"]["name"]] = role

        return destination_roles_mapping
