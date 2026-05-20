# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for ``Users.import_resource`` skip behavior."""

import asyncio
from unittest.mock import MagicMock

import pytest

from datadog_sync.model.users import Users
from datadog_sync.utils.resource_utils import SkipResource


def _make_users():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    return Users(mock_config)


class TestUsersImportResource:
    def test_import_resource_skips_disabled_user(self):
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": True, "service_account": False, "email": "x@example.com"},
        }
        with pytest.raises(SkipResource, match=r"user-id.*User is disabled"):
            asyncio.run(users.import_resource(resource=resource))

    def test_import_resource_skips_service_account(self):
        users = _make_users()
        resource = {
            "id": "sa-id",
            "attributes": {"disabled": False, "service_account": True, "email": "sa@example.com"},
        }
        with pytest.raises(SkipResource, match=r"sa-id.*service account"):
            asyncio.run(users.import_resource(resource=resource))

    def test_import_resource_disabled_takes_precedence_over_service_account(self):
        # Disabled is checked first; the more specific reason wins in logs.
        users = _make_users()
        resource = {
            "id": "sa-disabled-id",
            "attributes": {"disabled": True, "service_account": True, "email": "sa@example.com"},
        }
        with pytest.raises(SkipResource, match=r"User is disabled"):
            asyncio.run(users.import_resource(resource=resource))

    def test_import_resource_processes_regular_user(self):
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": False, "service_account": False, "email": "x@example.com"},
        }
        result_id, result = asyncio.run(users.import_resource(resource=resource))
        assert result_id == "user-id"
        assert result is resource

    def test_import_resource_missing_service_account_field(self):
        # Older user shapes may omit service_account; .get() treats absent as falsy.
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": False, "email": "x@example.com"},
        }
        result_id, result = asyncio.run(users.import_resource(resource=resource))
        assert result_id == "user-id"
        assert result is resource


class TestUsersPreResourceActionHook:
    """Apply-time skip — catches SA users already in state from runs before the import-time skip shipped."""

    def test_pre_resource_action_hook_skips_service_account(self):
        users = _make_users()
        resource = {
            "id": "sa-id",
            "attributes": {"disabled": False, "service_account": True, "email": "sa@example.com"},
        }
        with pytest.raises(SkipResource, match=r"sa-id.*service account"):
            asyncio.run(users.pre_resource_action_hook("sa-id", resource))

    def test_pre_resource_action_hook_allows_regular_user(self):
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": False, "service_account": False, "email": "x@example.com"},
        }
        # Must not raise.
        asyncio.run(users.pre_resource_action_hook("user-id", resource))

    def test_pre_resource_action_hook_allows_user_without_service_account_field(self):
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": False, "email": "x@example.com"},
        }
        # Must not raise.
        asyncio.run(users.pre_resource_action_hook("user-id", resource))
