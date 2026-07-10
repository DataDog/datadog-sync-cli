# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the role create/update ERROR-level logging on unhandled failures.

Motivation: managed-sync runs sync-cli once per resource type in separate
processes. When a role fails to sync, downstream types (monitors, dashboards,
users, etc.) that reference that role can't be told which source id
disappeared. Before this change, sync-cli's roles module re-raised the
HTTP error without logging the source id + role name, so cascade failures
downstream could not be correlated back to the specific role that failed.

These tests verify that role create + update failures log the source id
and role name at ERROR level before re-raising.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.roles import Roles
from datadog_sync.utils.resource_utils import CustomClientHTTPError


class _FakeResp:
    def __init__(self, status):
        self.status = status
        self.message = "Bad Request"


def _make_roles():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.allow_partial_permissions_roles = []
    return Roles(mock_config)


def _get_error_log_calls(mock_logger):
    """Return concatenated messages from all logger.error() calls on the mock.
    The roles module uses ``self.config.logger.error(fmt, *args)``; MagicMock
    records the call args, so we render them back like %-formatting would."""
    calls = mock_logger.error.call_args_list
    rendered = []
    for call in calls:
        args = call.args
        if not args:
            continue
        fmt = args[0]
        try:
            rendered.append(fmt % args[1:])
        except Exception:
            rendered.append(str(args))
    return " | ".join(rendered)


def test_create_resource_logs_source_id_on_error():
    """When create_resource re-raises a non-retryable error, the source id
    and role name must appear in an ERROR log so downstream cascade
    correlation is possible."""
    roles = _make_roles()
    roles._existing_resources_map = {}  # role is NOT already on destination -> POST path

    err = CustomClientHTTPError(_FakeResp(400), message='{"detail":"boom"}')
    roles.config.destination_client = MagicMock()
    roles.config.destination_client.post = AsyncMock(side_effect=err)

    resource = {
        "id": "role-uuid-abc-123",
        "attributes": {"name": "MyCustomRole"},
        "relationships": {"permissions": {"data": []}},
    }

    with pytest.raises(CustomClientHTTPError):
        asyncio.run(roles.create_resource("role-uuid-abc-123", resource))

    joined = _get_error_log_calls(roles.config.logger)
    assert "role-uuid-abc-123" in joined, f"expected source id in ERROR log; got: {joined!r}"
    assert "MyCustomRole" in joined
    assert "role sync failed" in joined


def test_update_resource_logs_source_id_on_error():
    """update_resource must also log source id + name at ERROR before
    re-raising."""
    roles = _make_roles()
    # State has the destination role mapped from the source id; update takes
    # the "found in destination" path.
    roles.config.state.destination = {"roles": {"role-uuid-def-456": {"id": "dest-id"}}}

    err = CustomClientHTTPError(_FakeResp(400), message='{"detail":"nope"}')
    roles.config.destination_client = MagicMock()
    roles.config.destination_client.patch = AsyncMock(side_effect=err)

    resource = {
        "id": "role-uuid-def-456",
        "attributes": {"name": "AnotherCustomRole"},
        "relationships": {"permissions": {"data": []}},
    }

    with pytest.raises(CustomClientHTTPError):
        asyncio.run(roles.update_resource("role-uuid-def-456", resource))

    joined = _get_error_log_calls(roles.config.logger)
    assert "role-uuid-def-456" in joined, f"expected source id in ERROR log; got: {joined!r}"
    assert "AnotherCustomRole" in joined
    assert "role update failed" in joined
