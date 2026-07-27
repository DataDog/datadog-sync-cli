# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
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
            # NOTE: attributes.service_account and attributes.handle are deliberately
            # NOT excluded here. Both must survive prep_resource so create_resource can
            # read them: handle is the mapping key / v1 payload, and service_account
            # routes the create to POST /api/v2/service_accounts (and is required in
            # that payload). Both are popped from the plain v2 users POST/PATCH and are
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
        # handle is read-only in v2 and popped from the create/update payload;
        # service_account is create-time routing metadata that is read-only after
        # creation. Exclude both from update diffs so a source-vs-destination
        # difference never drives a spurious PATCH of a field v2 will not accept.
        deep_diff_config={
            "ignore_order": True,
            "exclude_regex_paths": [r".*\['handle'\]", r".*\['service_account'\]"],
        },
    )
    # Additional Users specific attributes
    pagination_config = PaginationConfig(
        page_size=500,
    )
    roles_path: str = "/api/v2/roles/{}/users"
    service_accounts_path: str = "/api/v2/service_accounts"

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
        destination = self.config.state.destination[self.resource_type].get(_id)
        if destination is not None:
            self._warn_on_account_type_mismatch(_id, resource, destination)

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        key = self.get_resource_mapping_key(resource)
        if key and key in self._existing_resources_map:
            destination = self._existing_resources_map[key]
            self._warn_on_account_type_mismatch(_id, resource, destination)
            self.config.state.destination[self.resource_type][_id] = destination
            return await self.update_resource(_id, resource)

        attributes = resource["attributes"]
        if attributes.get("service_account"):
            # Service accounts must be created via the dedicated endpoint; the
            # regular v2 /api/v2/users endpoint creates regular users, not
            # service accounts.
            return await self._create_service_account(_id, resource)

        # Not a service account: the flag is read-only on the plain user
        # endpoints, so drop it from the payload (it survived prep_resource only
        # so the routing check above could read it).
        attributes.pop("service_account", None)
        destination_client = self.config.destination_client
        # handle is read-only in v2 (derived from email) and must not be sent.
        attributes.pop("disabled", None)
        attributes.pop("handle", None)
        resp = await destination_client.post(self.resource_config.base_path, {"data": resource})
        return _id, resp["data"]

    async def _create_service_account(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        """Create the user via POST /api/v2/service_accounts.

        Service accounts are users with ``service_account=true`` and cannot be
        created through the regular users endpoints. The service_accounts endpoint
        requires ``service_account: true`` in the payload, accepts optional role
        relationships, and (like v2 users) derives the handle from the email — so
        the read-only ``handle``/``disabled`` attributes must not be sent. The
        response is the created v2 user record; state and downstream references
        key on its UUID, so return it as-is.
        """
        destination_client = self.config.destination_client
        attributes = resource["attributes"]
        attributes.pop("disabled", None)
        attributes.pop("handle", None)
        try:
            resp = await destination_client.post(self.service_accounts_path, {"data": resource})
        except CustomClientHTTPError as e:
            if e.status_code == 403:
                self.config.logger.warning(
                    "users: service-account creation failed for source id %s with HTTP 403; "
                    "the destination application key requires the service_account_write permission",
                    _id,
                )
            raise
        return _id, resp["data"]

    def _warn_on_account_type_mismatch(self, _id: str, source: Dict, destination: Dict) -> None:
        """Warn when a read-only account type prevents exact reconciliation."""
        source_is_service_account = bool(source.get("attributes", {}).get("service_account"))
        destination_is_service_account = bool(destination.get("attributes", {}).get("service_account"))
        if source_is_service_account != destination_is_service_account:
            self.config.logger.warning(
                "users: account type differs for source id %s: source service_account=%s, "
                "destination service_account=%s; account type is read-only and requires manual remediation",
                _id,
                source_is_service_account,
                destination_is_service_account,
            )

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
            # service_account is read-only post-creation; never send it in a PATCH.
            resource["attributes"].pop("service_account", None)
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
