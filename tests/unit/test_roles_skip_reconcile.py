# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""roles: destination-state reconciliation behavior on SkipResource paths.

Roles has no live skip-without-write bug today: every reachable ``SkipResource``
raise either (a) is a genuine non-existence skip (built-in role absent from the
destination map -> key not in map -> framework correctly writes nothing), or
(b) fires from ``update_resource`` after ``state.destination`` was already
populated (the "already exists" path writes state.destination before delegating)
-> framework insert-if-absent no-ops. These tests pin both invariants and add a
framework safety-net proof showing the wrapper WOULD reconcile roles if its
exists-path ever regressed into a skip-without-write.

Note: the ``create_resource`` permission-edge SkipResource (inside the
``if role_name not in map`` branch, which then checks ``if role_name in map``)
is unreachable in a single call because ``_existing_resources_map`` is static
during apply -- documented here as a dead-code finding for the audit.

All identifiers are obviously synthetic (``role-src``, ``role-dst``, ``perm-x``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.roles import Roles
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


class _FakeResponse:
    def __init__(self, status, message):
        self.status = status
        self.message = message


def _role(name, perm_ids=None, role_id=None):
    return {
        "type": "roles",
        "id": role_id or f"role-id-{name}",
        "attributes": {"name": name},
        "relationships": {"permissions": {"data": [{"id": p, "type": "permission"} for p in (perm_ids or [])]}},
    }


def _make_roles(existing_map=None, destination_client=None, allow_partial=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    config.allow_partial_permissions_roles = allow_partial or []
    roles = Roles(config=config)
    roles._existing_resources_map = existing_map or {}
    return roles, config


class TestRolesSkipReconcile:
    def test_create_built_in_not_in_map_skip_does_not_write(self):
        # Built-in role absent from the destination map -> genuine non-existence
        # skip. Framework must NOT invent a destination entry (no false positive).
        roles, config = _make_roles(existing_map={})
        _id = "role-src"
        resource = _role("Datadog Admin Role", ["perm-x"], role_id=_id)

        with pytest.raises(SkipResource, match="built-in Datadog role"):
            asyncio.run(roles._create_resource(_id, resource))

        assert config.state.destination["roles"] == {}

    def test_update_permission_edge_skip_preserves_state_destination(self):
        # Reachable update_resource permission edge: state.destination already
        # holds the role; patch 400 -> remove perm-x -> no diff with in-state
        # destination -> SkipResource. Insert-if-absent must leave the pre-existing
        # entry untouched (same object identity).
        _id = "role-src"
        in_state = _role("role-src", ["perm-y"], role_id="role-dst")
        resource = _role("role-src", ["perm-x", "perm-y"], role_id=_id)

        err = CustomClientHTTPError(
            _FakeResponse(400, '{"detail":"invalid UUID [perm-x]"}'),
        )
        destination_client = MagicMock()
        destination_client.patch = AsyncMock(side_effect=err)
        roles, config = _make_roles(
            existing_map={"role-src": in_state},
            destination_client=destination_client,
            allow_partial=["perm-x"],
        )
        config.state.destination["roles"][_id] = in_state

        with pytest.raises(SkipResource, match="already exists at destination"):
            asyncio.run(roles._update_resource(_id, resource))

        # Insert-if-absent: pre-existing entry preserved, not overwritten by map.
        assert config.state.destination["roles"][_id] is in_state

    def test_framework_reconciles_if_create_raised_skip(self):
        # Safety-net proof: if roles.create_resource ever regressed into a
        # skip-without-write with the role present in the map, the framework
        # wrapper would reconcile state.destination.
        _id = "role-src"
        dest_role = _role("role-src", ["perm-y"], role_id="role-dst")
        roles, config = _make_roles(existing_map={"role-src": dest_role})
        # Stub create_resource to raise (bypassing the real delegate logic).
        roles.create_resource = AsyncMock(side_effect=SkipResource(_id, "roles", "exists"))
        resource = _role("role-src", ["perm-y"], role_id=_id)

        with pytest.raises(SkipResource):
            asyncio.run(roles._create_resource(_id, resource))

        assert config.state.destination["roles"][_id] == dest_role

    def test_create_non_existing_role_still_posts(self):
        _id = "role-src"
        posted = _role("role-src", ["perm-y"], role_id="role-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        roles, config = _make_roles(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _role("role-src", ["perm-y"], role_id=_id)

        out_id, out_r = asyncio.run(roles.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
