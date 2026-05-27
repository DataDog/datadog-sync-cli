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


# These role names are reserved and managed by Datadog — they cannot be created,
# updated, or deleted via the API and must be excluded from all sync operations.
BUILTIN_ROLE_NAMES = frozenset(
    {
        "Datadog Admin Role",
        "Datadog Read Only Role",
        "Datadog Standard Role",
    }
)


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
        resource_mapping_key="attributes.name",
    )
    # Additional Roles specific attributes
    source_permissions: Dict = {}
    destination_permissions: Dict = {}
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

    async def map_existing_resources(self) -> None:
        self._existing_resources_map = await self.get_destination_roles_mapping()

    async def pre_apply_hook(self) -> None:
        pass

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        await self.remap_permissions(resource)
        self._reconcile_builtin_role_permissions(_id, resource)

    async def create_resource(self, _id, resource) -> Tuple[str, Dict]:
        # this method uses role name from matching
        role_name = resource["attributes"]["name"]

        if role_name in BUILTIN_ROLE_NAMES and role_name not in self._existing_resources_map:
            raise SkipResource(
                _id, self.resource_type, f"'{role_name}' is a built-in Datadog role and cannot be created"
            )

        # remove the 'managed' attribute since it can not be passed into creation
        resource["attributes"].pop("managed", None)

        # role does not exist at the destination, so create it
        if role_name not in self._existing_resources_map:
            destination_client = self.config.destination_client

            # Retry loop to handle multiple invalid permissions
            max_retries = 1 + len(self.config.allow_partial_permissions_roles)
            retry_count = 0

            while retry_count < max_retries:
                payload = {"data": resource}
                try:
                    resp = await destination_client.post(self.resource_config.base_path, payload)
                    self._reconcile_persisted_permissions(_id, resource, resp["data"])
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
                                if role_name in self._existing_resources_map:
                                    matching_destination_role = self._existing_resources_map[role_name]
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
        matching_destination_role = self._existing_resources_map[role_name]
        role_copy = copy.deepcopy(resource)
        role_copy.update(matching_destination_role)

        if role_name in BUILTIN_ROLE_NAMES:
            return _id, role_copy

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

        if role_name in BUILTIN_ROLE_NAMES:
            raise SkipResource(
                _id, self.resource_type, f"'{role_name}' is a built-in Datadog role and cannot be updated"
            )

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
                self._reconcile_persisted_permissions(_id, resource, resp["data"])
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
        role_name = self.config.state.destination[self.resource_type][_id].get("attributes", {}).get("name")
        if role_name in BUILTIN_ROLE_NAMES:
            raise SkipResource(
                _id, self.resource_type, f"'{role_name}' is a built-in Datadog role and cannot be deleted"
            )
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
            matched_permissions = []
            for permission in resource["relationships"]["permissions"]["data"]:
                if permission["id"] in self.destination_permissions:
                    permission["id"] = self.destination_permissions[permission["id"]]
                    matched_permissions.append(permission)
                else:
                    self.config.logger.warning(
                        "permission '%s' exists in source but not in destination, skipping",
                        permission["id"],
                    )
            resource["relationships"]["permissions"]["data"] = matched_permissions

    def _reconcile_builtin_role_permissions(self, _id: str, resource: Dict) -> None:
        """Trim a built-in role's permissions to match what the destination org actually grants.

        Built-in roles (Datadog Admin/Standard/Read Only) cannot be created, updated, or deleted
        via the API. Their permission set is owned by the destination org and can legitimately
        differ from the source org (feature flags, sub-org policies, product entitlements).

        After `remap_permissions` runs the working copy's permissions are destination UUIDs.
        The destination role from `_existing_resources_map` also exposes the destination's
        actual permission UUIDs. For built-in roles we intersect the two and write the result
        back to the working copy AND to `state.source` so that:

          - The in-process diff check (sync path) sees no divergence and skips the API call
            that would otherwise be rejected as "built-in role".
          - The on-disk source state (written by `dump_state` at the end of sync) reflects
            the reconciled permission set, so the follow-up `diffs` invocation reads matching
            source/destination state and produces no diff.

        This runs in `pre_resource_action_hook`, which fires for both sync and diffs flows.
        In the sync flow `resource` is a deep copy of `state.source`, so we explicitly update
        the source dict in addition to the working copy. In the diffs flow `resource` IS the
        same reference as `state.source[type][_id]`, so the working-copy update covers both.
        Non-built-in roles are unchanged — those are handled by `_reconcile_persisted_permissions`
        on the API response path.
        """
        try:
            role_name = resource.get("attributes", {}).get("name")
        except AttributeError:
            return

        if role_name not in BUILTIN_ROLE_NAMES:
            return

        existing = self._existing_resources_map.get(role_name) if self._existing_resources_map else None
        if not existing:
            return

        try:
            destination_perms = existing.get("relationships", {}).get("permissions", {}).get("data", [])
            working_perms = resource.get("relationships", {}).get("permissions", {}).get("data", [])
        except AttributeError:
            return

        if not working_perms:
            return

        destination_ids = {p.get("id") for p in destination_perms if isinstance(p, dict)}
        trimmed = [p for p in working_perms if isinstance(p, dict) and p.get("id") in destination_ids]

        if len(trimmed) == len(working_perms):
            return

        dropped_count = len(working_perms) - len(trimmed)
        self.config.logger.debug(
            "trimming %d permission(s) from built-in role '%s' to match destination",
            dropped_count,
            role_name,
        )

        resource["relationships"]["permissions"]["data"] = trimmed

        # In the sync path `resource` is a deepcopy from `state.source`, so also trim the
        # source dict so dump_state persists the reconciled set to disk for the follow-up
        # diffs invocation. The source dict's permission entries are destination UUIDs at
        # this point (remap_permissions has already run on it in the diffs path; in the
        # sync path the source dict still holds permission *names*, so reverse-map UUID
        # back to name via destination_permissions when needed).
        source_state = self.config.state.source.get(self.resource_type, {}).get(_id)
        if not source_state:
            return
        source_perms_container = source_state.get("relationships", {}).get("permissions", {})
        source_perms = source_perms_container.get("data", [])
        if not source_perms:
            return

        # Reverse map: destination UUID -> permission name (for sync path where source
        # state still holds permission names after import_resource's name rewrite).
        uuid_to_name = (
            {uuid: name for name, uuid in self.destination_permissions.items()}
            if self.destination_permissions
            else {}
        )
        # A permission entry in source_perms may carry either a name (sync path, pre-remap)
        # or a destination UUID (diffs path, where pre_resource_action_hook just ran on the
        # same reference). Keep an entry if its id matches a kept destination UUID, OR if
        # its id is the name corresponding to a kept destination UUID.
        kept_names = {uuid_to_name.get(uuid) for uuid in destination_ids if uuid in uuid_to_name}
        kept_names.discard(None)
        source_perms_container["data"] = [
            p
            for p in source_perms
            if isinstance(p, dict) and (p.get("id") in destination_ids or p.get("id") in kept_names)
        ]

    def _reconcile_persisted_permissions(self, _id: str, requested: Dict, persisted: Dict) -> None:
        """Reconcile the source state with the permissions the destination actually persisted.

        The Datadog roles API silently drops some permission IDs on create/update — e.g. permissions
        that imply each other, are gated by feature flags, or are not grantable to a given role —
        without returning a 400. After a successful create/update, the response's permission list is
        authoritative. If it is a strict subset of what we requested, we trim the source state in
        memory to match, so subsequent `diffs` invocations (which re-load source state from disk via
        importing) and in-process diff checks do not flag the dropped permissions as divergence.

        The on-disk source state files (written by `import`) are intentionally NOT mutated — those
        reflect the source org. We only adjust the in-memory representation used for diff
        comparisons within this run.
        """
        try:
            requested_perms = requested.get("relationships", {}).get("permissions", {}).get("data", [])
            persisted_perms = persisted.get("relationships", {}).get("permissions", {}).get("data", [])
        except AttributeError:
            return

        if not requested_perms:
            return

        persisted_ids = {p.get("id") for p in persisted_perms if isinstance(p, dict)}
        dropped = [p for p in requested_perms if isinstance(p, dict) and p.get("id") not in persisted_ids]

        if not dropped:
            return

        role_name = requested.get("attributes", {}).get("name", _id)
        dropped_ids = [p.get("id") for p in dropped]
        self.config.logger.warning(
            "destination silently dropped %d permission(s) for role '%s': %s. "
            "Trimming source state for this run to converge diffs.",
            len(dropped_ids),
            role_name,
            dropped_ids,
        )

        # Trim the in-memory source state so subsequent diff comparisons in this run see the same
        # permission set on both sides. `import_resource` rewrites source-state permission entries
        # so their `id` field holds the permission *name* (not the source UUID); `remap_permissions`
        # then maps name -> destination UUID on the per-resource working copy. So we reverse-look up
        # the dropped destination UUIDs back to their permission names and trim source state by name.
        if not self.destination_permissions:
            return
        uuid_to_name = {uuid: name for name, uuid in self.destination_permissions.items()}
        dropped_names = {uuid_to_name[uuid] for uuid in dropped_ids if uuid in uuid_to_name}
        if not dropped_names:
            return
        source_state = self.config.state.source.get(self.resource_type, {}).get(_id)
        if not source_state:
            return
        source_perms_container = source_state.get("relationships", {}).get("permissions", {})
        source_perms = source_perms_container.get("data", [])
        if not source_perms:
            return
        source_perms_container["data"] = [
            p for p in source_perms if isinstance(p, dict) and p.get("id") not in dropped_names
        ]

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
