# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""users: regression guards + framework safety-net proof.

Users' exists-path is already correct: ``create_resource`` writes
``state.destination`` from the map entry then delegates to ``update_resource``
(POST->PATCH), so it never raises ``SkipResource`` in the exists-path and the
framework is a pure no-op for it. The "User is disabled" skip is an *import-time*
skip (raised in ``import_resource``), entirely outside the create/update
wrappers, so the framework cannot and should not reconcile it.

All identifiers are obviously synthetic (``user-src``, ``user-dst@example.com``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.users import Users
from datadog_sync.utils.resource_utils import SkipResource


def _user(handle, email="user@example.com", user_id=None, disabled=False, service_account=False):
    return {
        "id": user_id or f"user-id-{handle}",
        "type": "users",
        "attributes": {
            "handle": handle,
            "email": email,
            "name": f"Test {handle}",
            "service_account": service_account,
            "disabled": disabled,
        },
        "relationships": {},
    }


def _make_users(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    users = Users(config=config)
    users._existing_resources_map = existing_map or {}
    return users, config


class TestUsersSkipReconcile:
    def test_existing_user_create_delegates_to_update_and_writes(self):
        # Exists-path: key in map -> create_resource writes state.destination
        # from the map entry, then delegates to update_resource. With no diff,
        # update_resource returns the in-state user (no PATCH). The wrapper then
        # writes the returned user. Framework insert-if-absent no-ops because
        # create_resource already populated state.destination.
        _id = "user-src"
        dest_user = _user("user-dst", email="user-dst@example.com", user_id="user-dst")
        destination_client = MagicMock()
        destination_client.patch = AsyncMock()  # should NOT be called (no diff)
        users, config = _make_users(
            existing_map={"user-dst": dest_user},
            destination_client=destination_client,
        )
        # Source resource matches the destination user (handle/service_account
        # excluded from diff), so update_resource takes the no-diff return path.
        resource = _user("user-dst", email="user-dst@example.com", user_id=_id)

        asyncio.run(users._create_resource(_id, resource))

        destination_client.patch.assert_not_called()
        assert config.state.destination["users"][_id] == dest_user

    def test_disabled_user_import_skip_unrelated_to_framework(self):
        # "User is disabled." is raised in import_resource (import path), not in
        # the create/update wrappers, so the framework never runs. Document the
        # boundary: the skip happens, and state.destination is untouched.
        users, config = _make_users(existing_map={"user-dst": _user("user-dst", user_id="user-dst")})
        disabled = _user("user-dst", user_id="user-src", disabled=True)

        with pytest.raises(SkipResource, match="User is disabled"):
            asyncio.run(users.import_resource(resource=disabled))

        assert config.state.destination["users"] == {}

    def test_framework_reconciles_if_create_raised_skip(self):
        # Safety-net proof: if users.create_resource ever regressed into a
        # skip-without-write with the handle present in the map, the framework
        # wrapper would reconcile state.destination.
        _id = "user-src"
        dest_user = _user("user-dst", email="user-dst@example.com", user_id="user-dst")
        users, config = _make_users(existing_map={"user-dst": dest_user})
        users.create_resource = AsyncMock(side_effect=SkipResource(_id, "users", "exists"))
        resource = _user("user-dst", email="user-dst@example.com", user_id=_id)

        with pytest.raises(SkipResource):
            asyncio.run(users._create_resource(_id, resource))

        assert config.state.destination["users"][_id] == dest_user

    def test_create_non_existing_user_still_posts(self):
        _id = "user-src"
        # handle == email avoids the v2 email-backfill second call.
        posted = _user("user-src", email="user-src", user_id="user-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        users, config = _make_users(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _user("user-src", email="user-src", user_id=_id)

        out_id, out_r = asyncio.run(users.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
