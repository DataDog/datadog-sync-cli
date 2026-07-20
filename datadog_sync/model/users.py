# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Any, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class UserRoleAssignmentError(RuntimeError):
    """One or more role assignments failed while reconciling a user."""

    def __init__(self, user: Dict, failed_role_ids: List[str]) -> None:
        self.user = user
        self.failed_role_ids = tuple(failed_role_ids)
        count = len(failed_role_ids)
        assignment = "role assignment" if count == 1 else "role assignments"
        super().__init__(f"{count} {assignment} failed while reconciling user")


class Users(BaseResource):
    resource_type = "users"
    resource_config = ResourceConfig(
        resource_connections={"roles": ["relationships.roles.data.id"]},
        base_path="/api/v2/users",
        non_nullable_attr=["attributes.name"],
        null_values={
            "name": [""],
        },
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.title",
            "attributes.status",
            "attributes.verified",
            "attributes.service_account",
            # NOTE: attributes.handle is deliberately NOT excluded here. It is the
            # user mapping key (resource_mapping_key below) and the payload for the
            # v1 user creation, so it must survive prep_resource. It is popped
            # manually before the v2 POST/PATCH (v2 treats handle as read-only) and
            # kept out of update diffs via deep_diff_config.exclude_regex_paths below.
            "attributes.icon",
            "attributes.modified_at",
            "attributes.mfa_enabled",
            "attributes.allowed_login_methods",
            "attributes.last_login_time",
            "attributes.uuid",
            "relationships.org",
            "relationships.team_roles",
        ],
        resource_mapping_key="attributes.handle",
        # Handle is read-only in v2 and is popped from the create/update payload;
        # exclude it from update diffs so a source-vs-destination handle difference
        # never drives a spurious PATCH of a field v2 will not accept.
        deep_diff_config={"ignore_order": True, "exclude_regex_paths": [r".*\['handle'\]"]},
    )
    # Additional Users specific attributes
    pagination_config = PaginationConfig(
        page_size=500,
    )
    roles_path: str = "/api/v2/roles/{}/users"
    user_lookup_retry_delays: Tuple[float, ...] = (1.0, 2.0)

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(
        self, _id: Optional[str] = None, resource: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)
        if resource["attributes"]["disabled"]:
            raise SkipResource(resource["id"], self.resource_type, "User is disabled.")

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        key = self.get_resource_mapping_key(resource)
        if key and key in self._existing_resources_map:
            self.config.state.destination[self.resource_type][_id] = self._existing_resources_map[key]
            return await self.update_resource(_id, resource)

        attributes = resource["attributes"]
        if self.config.use_v1_user_api:
            # v2 create cannot set a handle (it derives one from the email), which
            # collapses distinct-handle users that share an email onto one handle.
            # v1 accepts an explicit handle, so each user keeps its own.
            try:
                user = await self._create_via_v1(
                    attributes.get("handle"),
                    attributes.get("name"),
                    attributes.get("email"),
                    resource.get("relationships", {}).get("roles", {}).get("data", []),
                )
            except UserRoleAssignmentError as e:
                # Preserve the reconciled UUID and successful memberships so
                # downstream resources can still resolve this user. Re-raising
                # makes the apply handler count and emit the partial failure.
                self.config.state.destination[self.resource_type][_id] = e.user
                raise
            return _id, user

        destination_client = self.config.destination_client
        # handle is read-only in v2 (derived from email) and must not be sent.
        attributes.pop("disabled", None)
        attributes.pop("handle", None)
        resp = await destination_client.post(self.resource_config.base_path, {"data": resource})
        return _id, resp["data"]

    async def _create_via_v1(
        self,
        handle: Optional[str],
        name: Optional[str],
        email: Optional[str],
        desired_roles: Optional[List[Dict]] = None,
    ) -> Dict:
        """Create the user via the v1 API, which accepts an explicit handle.

        v2 ``POST /api/v2/users`` cannot set a handle (it is derived from the
        email), so users that share an email collapse onto one handle and later
        creates 409 — and the v2 "winner" is created with the wrong handle. v1
        ``POST /api/v1/user`` accepts a distinct handle. The v1 response is the
        legacy user shape, so re-resolve the v2 UUID by handle and return that
        record — state and downstream references (roles, team_memberships) key
        on the v2 UUID.
        """
        if not handle:
            raise ValueError("v1 user creation requires a handle")

        destination_client = self.config.destination_client
        await destination_client.post("/api/v1/user", {"handle": handle, "name": name, "email": email})

        user = await self._get_destination_user_by_handle(handle)
        if user is None:
            raise ValueError("v1-created user not found by handle after create")
        await self._assign_missing_roles(user, desired_roles or [])
        return user

    async def _assign_missing_roles(self, user: Dict, desired_roles: List[Dict]) -> None:
        """Assign missing roles and keep the reconciled user state accurate."""
        existing_roles = user.setdefault("relationships", {}).setdefault("roles", {}).setdefault("data", [])
        existing_role_ids = {
            role["id"] for role in existing_roles if isinstance(role, dict) and role.get("id") is not None
        }
        failed_role_ids = []

        for role in desired_roles:
            if not isinstance(role, dict) or role.get("id") is None:
                continue
            role_id = role["id"]
            if role_id in existing_role_ids:
                continue
            if await self.add_user_to_role(user["id"], role_id):
                existing_roles.append(dict(role))
                existing_role_ids.add(role_id)
            else:
                failed_role_ids.append(str(role_id))

        if failed_role_ids:
            raise UserRoleAssignmentError(user, failed_role_ids)

    @staticmethod
    def _merge_role_state(updated_user: Dict, role_state: Dict) -> Dict:
        """Preserve known role memberships when a user PATCH omits them."""
        updated_roles = updated_user.setdefault("relationships", {}).setdefault("roles", {}).setdefault("data", [])
        updated_role_ids = {
            role["id"] for role in updated_roles if isinstance(role, dict) and role.get("id") is not None
        }
        for role in role_state.get("relationships", {}).get("roles", {}).get("data", []):
            role_id = role.get("id") if isinstance(role, dict) else None
            if role_id is not None and role_id not in updated_role_ids:
                updated_roles.append(dict(role))
                updated_role_ids.add(role_id)
        return updated_user

    async def _get_destination_user_by_handle(self, handle: str) -> Optional[Dict]:
        """Return the destination user whose handle matches exactly, or None.

        Transient HTTP errors are already retried by the client's
        ``request_with_retry``; this adds a bounded re-query loop with delays to
        absorb read-after-write visibility lag after a v1 create.
        """
        destination_client = self.config.destination_client
        for attempt in range(len(self.user_lookup_retry_delays) + 1):
            resp = await destination_client.paginated_request(destination_client.get)(
                self.resource_config.base_path,
                pagination_config=self.pagination_config,
                params={"filter": handle},
            )
            for user in resp:
                if user.get("attributes", {}).get("handle") == handle:
                    return user
            if attempt < len(self.user_lookup_retry_delays):
                await asyncio.sleep(self.user_lookup_retry_delays[attempt])
        return None

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        destination_user = self.config.state.destination[self.resource_type][_id]
        diff = check_diff(self.resource_config, destination_user, resource)
        if diff:
            role_error = None
            try:
                await self._assign_missing_roles(
                    destination_user,
                    resource.get("relationships", {}).get("roles", {}).get("data", []),
                )
            except UserRoleAssignmentError as e:
                # Continue with unrelated attribute updates, then report the
                # partial role failure so the apply handler does not emit success.
                role_error = e

            resource["id"] = destination_user["id"]
            resource.pop("relationships", None)
            resource["attributes"].pop("handle", None)
            resp = await destination_client.patch(
                self.resource_config.base_path + f"/{destination_user['id']}",
                {"data": resource},
            )
            updated_user = self._merge_role_state(resp["data"], destination_user)

            if role_error is not None:
                self.config.state.destination[self.resource_type][_id] = updated_user
                role_error.user = updated_user
                raise role_error
            return _id, updated_user
        return _id, destination_user

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Users, self).connect_id(key, r_obj, resource_to_connect)

    async def add_user_to_role(self, user_id, role_id) -> bool:
        destination_client = self.config.destination_client
        payload = {"data": {"id": user_id, "type": "users"}}
        try:
            await destination_client.post(self.roles_path.format(role_id), payload)
            return True
        except CustomClientHTTPError as e:
            self.config.logger.error("error adding user: %s to role %s: %s", user_id, role_id, e)
            return False

    async def remove_user_from_role(self, user_id, role_id):
        destination_client = self.config.destination_client
        payload = {"data": {"id": user_id, "type": "users"}}
        try:
            await destination_client.delete(self.roles_path.format(role_id), payload)
        except CustomClientHTTPError as e:
            self.config.logger.error("error removing user: %s from role %s: %s", user_id, role_id, e)
