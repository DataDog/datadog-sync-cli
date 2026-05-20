# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for users resource import handling.

These tests verify the skip behaviour applied in ``Users.import_resource``:
disabled users and service-account users are not propagated to the
destination. Regular users pass through unchanged.
"""

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
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(users.import_resource(resource=resource))
        assert "User is disabled" in str(exc_info.value)
        assert "user-id" in str(exc_info.value)

    def test_import_resource_skips_service_account(self):
        users = _make_users()
        resource = {
            "id": "sa-id",
            "attributes": {"disabled": False, "service_account": True, "email": "sa@example.com"},
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(users.import_resource(resource=resource))
        assert "service account" in str(exc_info.value)
        assert "sa-id" in str(exc_info.value)

    def test_import_resource_disabled_service_account_uses_disabled_reason(self):
        """A disabled service account hits the disabled check first; the more specific
        reason wins in logs. This guards the ordering of the two skip checks."""
        users = _make_users()
        resource = {
            "id": "sa-disabled-id",
            "attributes": {"disabled": True, "service_account": True, "email": "sa@example.com"},
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(users.import_resource(resource=resource))
        assert "User is disabled" in str(exc_info.value)

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
        """Defensive: older user shapes may not carry ``service_account`` at all.
        Using ``.get()`` means the absent field is treated as falsy and the user
        is imported normally."""
        users = _make_users()
        resource = {
            "id": "user-id",
            "attributes": {"disabled": False, "email": "x@example.com"},
        }
        result_id, result = asyncio.run(users.import_resource(resource=resource))
        assert result_id == "user-id"
        assert result is resource
