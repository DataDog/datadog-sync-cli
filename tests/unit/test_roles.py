# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for roles resource handling.

These tests verify that the 3 built-in Datadog roles (Admin, Read Only,
Standard) are skipped at create, update, and delete when they would be
mutated via the API. Existing destination built-ins still need to map
source IDs to destination IDs so dependent resources can be assigned.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.roles import BUILTIN_ROLE_NAMES, Roles
from datadog_sync.utils.resource_utils import SkipResource


def _make_roles():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.allow_partial_permissions_roles = []
    return Roles(mock_config)


class TestRolesBuiltinGuardCreate:
    @pytest.mark.parametrize("role_name", sorted(BUILTIN_ROLE_NAMES))
    def test_create_skips_builtin_role(self, role_name):
        roles = _make_roles()
        resource = {"attributes": {"name": role_name}}
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(roles.create_resource("source-id", resource))
        assert role_name in str(exc_info.value)
        assert "cannot be created" in str(exc_info.value)

    def test_create_maps_existing_builtin_role_without_api_call(self):
        roles = _make_roles()
        destination_role = {
            "id": "destination-role-id",
            "attributes": {"name": "Datadog Admin Role", "managed": True},
            "relationships": {"permissions": {"data": []}},
        }
        roles._existing_resources_map = {"Datadog Admin Role": destination_role}
        roles.config.state.destination = {"roles": {}}
        roles.config.destination_client.post = AsyncMock()
        roles.config.destination_client.patch = AsyncMock()

        resource = {
            "id": "source-role-id",
            "attributes": {"name": "Datadog Admin Role", "managed": True},
            "relationships": {"permissions": {"data": []}},
        }

        asyncio.run(roles._create_resource("source-role-id", resource))

        assert roles.config.state.destination["roles"]["source-role-id"] == destination_role
        roles.config.destination_client.post.assert_not_awaited()
        roles.config.destination_client.patch.assert_not_awaited()

    def test_create_does_not_skip_non_builtin_role(self):
        roles = _make_roles()
        roles._existing_resources_map = {}
        roles.config.destination_client.post = AsyncMock(return_value={"data": {"id": "new-id"}})
        resource = {"attributes": {"name": "Custom Engineering Role"}, "relationships": {}}
        # Should not raise SkipResource
        result_id, result = asyncio.run(roles.create_resource("source-id", resource))
        assert result_id == "source-id"
        roles.config.destination_client.post.assert_awaited_once()


class TestRolesBuiltinGuardUpdate:
    @pytest.mark.parametrize("role_name", sorted(BUILTIN_ROLE_NAMES))
    def test_update_skips_builtin_role(self, role_name):
        roles = _make_roles()
        resource = {"attributes": {"name": role_name}}
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(roles.update_resource("source-id", resource))
        assert role_name in str(exc_info.value)
        assert "cannot be updated" in str(exc_info.value)

    def test_update_does_not_skip_non_builtin_role(self):
        roles = _make_roles()
        roles.config.state.destination = {"roles": {"source-id": {"id": "dest-id"}}}
        roles.config.destination_client.patch = AsyncMock(return_value={"data": {"id": "dest-id"}})
        resource = {"attributes": {"name": "Custom Engineering Role"}, "relationships": {}}
        # Should not raise SkipResource
        result_id, result = asyncio.run(roles.update_resource("source-id", resource))
        assert result_id == "source-id"
        roles.config.destination_client.patch.assert_awaited_once()


class TestRolesBuiltinGuardDelete:
    @pytest.mark.parametrize("role_name", sorted(BUILTIN_ROLE_NAMES))
    def test_delete_skips_builtin_role(self, role_name):
        roles = _make_roles()
        roles.config.state.destination = {"roles": {"source-id": {"id": "dest-id", "attributes": {"name": role_name}}}}
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(roles.delete_resource("source-id"))
        assert role_name in str(exc_info.value)
        assert "cannot be deleted" in str(exc_info.value)

    def test_delete_does_not_skip_non_builtin_role(self):
        roles = _make_roles()
        roles.config.state.destination = {
            "roles": {"source-id": {"id": "dest-id", "attributes": {"name": "Custom Engineering Role"}}}
        }
        roles.config.destination_client.delete = AsyncMock(return_value=None)
        # Should not raise SkipResource
        asyncio.run(roles.delete_resource("source-id"))
        roles.config.destination_client.delete.assert_awaited_once()


class TestBuiltinRoleNamesConstant:
    def test_contains_expected_builtin_roles(self):
        assert BUILTIN_ROLE_NAMES == frozenset(
            {
                "Datadog Admin Role",
                "Datadog Read Only Role",
                "Datadog Standard Role",
            }
        )
