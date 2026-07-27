# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for handle-keyed users.

The Datadog destination enforces user uniqueness on the ``handle``, not the
``email`` — multiple users may share an email. Keying ``_existing_resources_map``
by email collapsed distinct-handle users onto one derived handle and caused a
409 Conflict on the second create. These tests cover switching the user mapping
key to the exact-case handle.

Tests ``a``/``a2``/``a3``/``f`` are red against ``resource_mapping_key=
"attributes.email"`` and green after the switch to ``"attributes.handle"`` plus
the manual handle pop before the v2 POST. Test ``g`` is a green/green guard that
handle stays excluded from update diffs across the excluded_attributes ->
exclude_regex_paths migration.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from datadog_sync.model.users import UserRoleAssignmentError, Users
from datadog_sync.utils.resource_utils import check_diff


def _make_user(handle, email, user_id, name="User", roles=None):
    """Build a user dict shaped like the v2 GET/create response."""
    return {
        "id": user_id,
        "type": "users",
        "attributes": {
            "handle": handle,
            "email": email,
            "name": name,
            "disabled": False,
        },
        "relationships": {"roles": {"data": roles or []}},
    }


class TestHandleMappingKey:
    def test_mapping_key_is_exact_case_handle(self, mock_config):
        """a: mapping key is the handle, preserved exact-case (no lowercasing)."""
        instance = Users(mock_config)
        resource = {"attributes": {"handle": "User-A@example.com", "email": "shared@example.com"}}
        assert instance.get_resource_mapping_key(resource) == "User-A@example.com"

    def test_map_keeps_shared_email_distinct_by_handle(self, mock_config):
        """a2: two destination users sharing an email but with distinct handles
        remain two entries in the map (email keying would collapse to one)."""
        instance = Users(mock_config)
        dest = [
            _make_user("user-a@example.com", "shared@example.com", "dest-a"),
            _make_user("user-b@example.com", "shared@example.com", "dest-b"),
        ]
        instance.get_resources = AsyncMock(return_value=dest)
        asyncio.run(instance.map_existing_resources())
        assert set(instance._existing_resources_map.keys()) == {
            "user-a@example.com",
            "user-b@example.com",
        }

    def test_source_matched_by_handle_not_email(self, mock_config):
        """a3: against a pre-existing destination user, a source user is matched
        by handle — a same-email/different-handle source is NOT a match."""
        instance = Users(mock_config)
        instance.get_resources = AsyncMock(
            return_value=[_make_user("user-a@example.com", "shared@example.com", "dest-a")]
        )
        asyncio.run(instance.map_existing_resources())

        same_handle = _make_user("user-a@example.com", "shared@example.com", "src-a")
        assert instance.get_resource_mapping_key(same_handle) in instance._existing_resources_map

        diff_handle_same_email = _make_user("user-b@example.com", "shared@example.com", "src-b")
        assert instance.get_resource_mapping_key(diff_handle_same_email) not in instance._existing_resources_map


class TestV2CreatePayload:
    def test_v2_post_body_excludes_handle_and_disabled(self, mock_config):
        """f: the v2 create body must not carry read-only handle or disabled."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-x", "attributes": {}}})
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")

        asyncio.run(instance.create_resource("src-a", src))

        mock_config.destination_client.post.assert_called_once()
        _, body = mock_config.destination_client.post.call_args.args
        attrs = body["data"]["attributes"]
        assert "handle" not in attrs
        assert "disabled" not in attrs

    def test_v2_patch_body_excludes_handle(self, mock_config):
        """f2: the v2 update (PATCH) body must not carry the read-only handle."""
        instance = Users(mock_config)
        _id = "src-a"
        mock_config.state.destination["users"][_id] = _make_user("user-a@example.com", "shared@example.com", "dest-a")
        # A differing name forces a diff -> the PATCH branch.
        src = _make_user("user-a@example.com", "shared@example.com", "dest-a", name="New Name")
        mock_config.destination_client.patch = AsyncMock(return_value={"data": {"id": "dest-a", "attributes": {}}})

        asyncio.run(instance.update_resource(_id, src))

        mock_config.destination_client.patch.assert_called_once()
        _, body = mock_config.destination_client.patch.call_args.args
        assert "handle" not in body["data"]["attributes"]


class TestHandleDiffExclusion:
    def test_handle_excluded_from_update_diff(self):
        """g: two users differing only by handle produce no diff (guards that
        handle stays diff-excluded after moving off excluded_attributes)."""
        dest = _make_user("user-a@example.com", "shared@example.com", "same-id")
        src = _make_user("user-b@example.com", "shared@example.com", "same-id")
        assert not check_diff(Users.resource_config, dest, src)


def _post_paths(mock_config):
    return [c.args[0] for c in mock_config.destination_client.post.call_args_list]


class TestV2CreatePath:
    def test_create_uses_v2(self, mock_config):
        """b: create always goes through the v2 endpoint."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-x", "attributes": {}}})

        asyncio.run(instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a")))

        assert _post_paths(mock_config) == ["/api/v2/users"]


class TestUpdatePathRegression:
    def test_successful_role_retry_remains_in_persisted_state(self, mock_config):
        """A successful role retry is not lost when PATCH omits relationships."""
        instance = Users(mock_config)
        successful_role = {"id": "role-dst-success", "type": "roles"}
        dest_user = _make_user("user-a@example.com", "shared@example.com", "dest-a")
        mock_config.state.destination["users"]["src-a"] = dest_user
        instance.add_user_to_role = AsyncMock(return_value=True)
        updated_user = {
            "id": "dest-a",
            "type": "users",
            "attributes": {
                "handle": "user-a@example.com",
                "email": "shared@example.com",
                "name": "Updated User",
                "disabled": False,
            },
        }
        mock_config.destination_client.patch = AsyncMock(return_value={"data": updated_user})

        def source_user():
            return _make_user(
                "user-a@example.com",
                "shared@example.com",
                "src-a",
                name="Updated User",
                roles=[successful_role],
            )

        asyncio.run(instance._update_resource("src-a", source_user()))
        asyncio.run(instance._update_resource("src-a", source_user()))

        stored = mock_config.state.destination["users"]["src-a"]
        assert stored["relationships"]["roles"]["data"] == [successful_role]
        instance.add_user_to_role.assert_awaited_once_with("dest-a", "role-dst-success")
        mock_config.destination_client.patch.assert_awaited_once()

    def test_role_retry_persists_partial_state_and_reports_failure(self, mock_config):
        """A later run retries missing roles without reporting full success."""
        instance = Users(mock_config)
        failed_role = {"id": "role-dst-failed", "type": "roles"}
        successful_role = {"id": "role-dst-success", "type": "roles"}
        dest_user = _make_user("user-a@example.com", "shared@example.com", "dest-a")
        source_user = _make_user(
            "user-a@example.com",
            "shared@example.com",
            "src-a",
            name="Updated User",
            roles=[failed_role, successful_role],
        )
        mock_config.state.destination["users"]["src-a"] = dest_user
        instance.add_user_to_role = AsyncMock(side_effect=[False, True])
        updated_user = {
            "id": "dest-a",
            "type": "users",
            "attributes": {
                "handle": "user-a@example.com",
                "email": "shared@example.com",
                "name": "Updated User",
                "disabled": False,
            },
        }
        mock_config.destination_client.patch = AsyncMock(return_value={"data": updated_user})

        with pytest.raises(UserRoleAssignmentError) as exc_info:
            asyncio.run(instance.update_resource("src-a", source_user))

        assert exc_info.value.failed_role_ids == ("role-dst-failed",)
        assert [call.args for call in instance.add_user_to_role.await_args_list] == [
            ("dest-a", "role-dst-failed"),
            ("dest-a", "role-dst-success"),
        ]
        mock_config.destination_client.patch.assert_awaited_once()
        stored = mock_config.state.destination["users"]["src-a"]
        assert stored["attributes"]["name"] == "Updated User"
        assert stored["relationships"]["roles"]["data"] == [successful_role]

    def test_existing_handle_takes_update_path_no_create(self, mock_config):
        """e: an existing destination handle routes to the update path — no v2
        create, no duplicate."""
        instance = Users(mock_config)
        dest_user = _make_user("user-a@example.com", "shared@example.com", "dest-a")
        instance._existing_resources_map = {"user-a@example.com": dest_user}
        mock_config.destination_client.post = AsyncMock()
        mock_config.destination_client.patch = AsyncMock(return_value={"data": dest_user})

        _id, r = asyncio.run(
            instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a"))
        )
        mock_config.destination_client.post.assert_not_called()
        assert r["id"] == "dest-a"
