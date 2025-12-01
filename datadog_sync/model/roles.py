# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
import json
import re
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Roles(BaseResource):
    resource_type = "roles"
    resource_config = ResourceConfig(
        base_path="/api/v2/roles",
        excluded_attributes=[
            "attributes.created_at",
            "attributes.created_by_handle",
            "attributes.managed",
            "attributes.modified_at",
            "attributes.modified_by_handle",
            "attributes.user_count",
            "id",
        ],
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
        self.destination_roles_mapping = await self.get_destination_roles_mapping()
        await self.remap_permissions(resource)

    async def create_resource(self, _id, resource) -> Tuple[str, Dict]:
        # this method uses role name from matching
        role_name = resource["attributes"]["name"]

        # remove the 'managed' attribute since it can not be passed into creation
        resource["attributes"].pop("managed", None)

        # role does not exist at the destination, so create it
        if role_name not in self.destination_roles_mapping:
            destination_client = self.config.destination_client

            # Retry loop to handle multiple invalid permissions
            max_retries = 1 + len(self.config.allow_partial_permissions_roles)
            retry_count = 0

            while retry_count < max_retries:
                payload = {"data": resource}
                try:
                    resp = await destination_client.post(self.resource_config.base_path, payload)
                    return _id, resp["data"]
                except CustomClientHTTPError as e:
                    if e.status_code == 400 and self.config.allow_partial_permissions_roles:
                        # Try to parse the invalid permission from the error
                        invalid_permission = self._parse_invalid_permission_from_error(str(e))

                        if invalid_permission and invalid_permission in self.config.allow_partial_permissions_roles:
                            # Check if we removed the permission successfully
                            if self._remove_permission_from_resource(resource, invalid_permission):
                                self.config.logger.warning(
                                    f"Trying again without '{invalid_permission}' permission for role '{role_name}'"
                                )

                                # Check if the modified resource now matches an existing destination role
                                # This can happen if we already synced the role without this permission before
                                if role_name in self.destination_roles_mapping:
                                    matching_destination_role = self.destination_roles_mapping[role_name]
                                    role_copy = copy.deepcopy(resource)
                                    role_copy.update(matching_destination_role)

                                    # If there's no diff, the role already exists without this permission
                                    if not check_diff(self.resource_config, resource, role_copy):
                                        raise SkipResource(
                                            _id,
                                            self.resource_type,
                                            f"Role '{role_name}' already exists at destination "
                                            "without '{invalid_permission}' permission",
                                        )

                                retry_count += 1
                                continue  # Retry with the updated resource

                    # If we couldn't handle the error, re-raise it
                    raise

            # If we exhausted retries, raise an error
            raise Exception(f"Exceeded maximum retries ({max_retries}) while creating role '{role_name}'")

        # role already exists at the destination
        matching_destination_role = self.destination_roles_mapping[role_name]
        role_copy = copy.deepcopy(resource)
        role_copy.update(matching_destination_role)

        # role is managed at the destination, do nothing
        if "managed" in matching_destination_role["attributes"] and matching_destination_role["attributes"]["managed"]:
            self.config.logger.warning(f"{role_name} is a managed resource at the destination, can not update it")
            return _id, role_copy

        # role is not managed at destination and it differs
        if check_diff(self.resource_config, resource, role_copy):
            self.config.state.destination[self.resource_type][_id] = role_copy
            return await self.update_resource(_id, resource)

        # role is not managed at destination and does not differ
        return _id, role_copy

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        role_name = resource["attributes"]["name"]
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]

        # Retry loop to handle multiple invalid permissions
        max_retries = 1 + len(self.config.allow_partial_permissions_roles)
        retry_count = 0

        while retry_count < max_retries:
            payload = {"data": resource}
            try:
                resp = await destination_client.patch(
                    self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
                    payload,
                )
                return _id, resp["data"]
            except CustomClientHTTPError as e:
                if e.status_code == 400 and self.config.allow_partial_permissions_roles:
                    # Try to parse the invalid permission from the error
                    invalid_permission = self._parse_invalid_permission_from_error(str(e))

                    if invalid_permission and invalid_permission in self.config.allow_partial_permissions_roles:
                        # Check if we removed the permission successfully
                        if self._remove_permission_from_resource(resource, invalid_permission):
                            self.config.logger.warning(
                                f"Trying again without '{invalid_permission}' permission for role '{role_name}'"
                            )

                            # Check if the modified resource now matches the existing destination state
                            # This can happen if we already synced the role without this permission before
                            if _id in self.config.state.destination[self.resource_type]:
                                destination_resource = self.config.state.destination[self.resource_type][_id]

                                # If there's no diff, the role already exists without this permission
                                if not check_diff(self.resource_config, resource, destination_resource):
                                    raise SkipResource(
                                        _id,
                                        self.resource_type,
                                        f"Role '{role_name}' already exists at destination "
                                        "without '{invalid_permission}' permission",
                                    )

                            retry_count += 1
                            continue  # Retry with the updated resource

                # If we couldn't handle the error, re-raise it
                raise

        # If we exhausted retries, raise an error
        raise Exception(f"Exceeded maximum retries ({max_retries}) while updating role '{role_name}'")

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def _parse_invalid_permission_from_error(self, error_message: str) -> Optional[str]:
        """Parse the invalid permission name from a 400 error message.

        Example error: '400 Bad Request - {"title":"Generic Error","detail":"invalid UUID [assistant_access]"}'
        Returns: "assistant_access"
        """
        try:
            # Try to extract JSON from error message
            match = re.search(r"\{.*\}", error_message)
            if not match:
                return None

            error_json = json.loads(match.group(0))

            # Look for "invalid UUID [permission_name]" pattern
            detail = error_json.get("detail", "")
            uuid_match = re.search(r"invalid UUID \[([^\]]+)\]", detail)
            if uuid_match:
                return uuid_match.group(1)

            # Also check in errors array if present
            errors = error_json.get("errors", [])
            for error in errors:
                if isinstance(error, str):
                    uuid_match = re.search(r"invalid UUID \[([^\]]+)\]", error)
                    if uuid_match:
                        return uuid_match.group(1)
                elif isinstance(error, dict):
                    detail = error.get("detail", "")
                    uuid_match = re.search(r"invalid UUID \[([^\]]+)\]", detail)
                    if uuid_match:
                        return uuid_match.group(1)
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

        return None

    def _remove_permission_from_resource(self, resource: Dict, permission_id: str) -> bool:
        """Remove a permission from the resource's relationships.

        Returns True if permission was found and removed, False otherwise.
        """
        if "relationships" not in resource or "permissions" not in resource["relationships"]:
            return False

        permissions_data = resource["relationships"]["permissions"].get("data", [])
        original_length = len(permissions_data)

        # Filter out the permission
        resource["relationships"]["permissions"]["data"] = [p for p in permissions_data if p.get("id") != permission_id]

        return len(resource["relationships"]["permissions"]["data"]) < original_length

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
