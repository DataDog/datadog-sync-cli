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

Tests ``sa1``-``sa6`` cover routing users with ``service_account=true`` to
``POST /api/v2/service_accounts`` instead of the regular user endpoints, and
that the flag survives prep_resource, stays out of update diffs, and is never
sent on the plain-user create/update paths.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.users import UserRoleAssignmentError, Users
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff


class _FakeResp:
    def __init__(self, status):
        self.status = status
        self.message = "Forbidden"


def _http_error(status):
    return CustomClientHTTPError(_FakeResp(status))


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
        mock_config.destination_client.post = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {}}}
        )
        mock_config.destination_client.patch = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {}}}
        )
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")

        asyncio.run(instance.create_resource("src-a", src))

        mock_config.destination_client.post.assert_called_once()
        _, body = mock_config.destination_client.post.call_args.args
        attrs = body["data"]["attributes"]
        assert "handle" not in attrs
        assert "disabled" not in attrs

    def test_v2_create_email_backfill_when_handle_differs_from_email(self, mock_config):
        """The create POST uses the handle as the email so the destination
        derives a matching handle, then a PATCH restores the real email."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {"email": "user-a@example.com"}}}
        )
        mock_config.destination_client.patch = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {"email": "shared@example.com"}}}
        )
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")

        _id, user = asyncio.run(instance.create_resource("src-a", src))

        _, post_body = mock_config.destination_client.post.call_args.args
        assert post_body["data"]["attributes"]["email"] == "user-a@example.com"

        patch_path, patch_body = mock_config.destination_client.patch.call_args.args
        assert patch_path == "/api/v2/users/dest-x"
        assert patch_body["data"]["attributes"] == {"email": "shared@example.com"}
        assert user["attributes"]["email"] == "shared@example.com"

    def test_v2_create_no_backfill_when_handle_matches_email(self, mock_config):
        """No second call is made when the source handle equals the source email."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {"email": "same@example.com"}}}
        )
        mock_config.destination_client.patch = AsyncMock()
        src = _make_user("same@example.com", "same@example.com", "src-a")

        asyncio.run(instance.create_resource("src-a", src))

        mock_config.destination_client.patch.assert_not_called()

    def test_v2_create_email_backfill_failure_persists_partial_state(self, mock_config):
        """If the email-restoring PATCH fails, the created user (with the handle
        as a placeholder email) is persisted so a retry updates instead of
        re-creating into a 409 on the now-taken handle."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        created_user = {"id": "dest-x", "type": "users", "attributes": {"email": "user-a@example.com"}}
        mock_config.destination_client.post = AsyncMock(return_value={"data": created_user})
        fake_response = MagicMock(status=500, message="boom")
        mock_config.destination_client.patch = AsyncMock(side_effect=CustomClientHTTPError(fake_response, "boom"))
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(instance.create_resource("src-a", src))

        assert mock_config.state.destination["users"]["src-a"] == created_user

    def test_v2_create_email_backfill_timeout_persists_partial_state(self, mock_config):
        """A transport timeout after create must preserve the created user too."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        created_user = {"id": "dest-x", "type": "users", "attributes": {"email": "user-a@example.com"}}
        mock_config.destination_client.post = AsyncMock(return_value={"data": created_user})
        mock_config.destination_client.patch = AsyncMock(side_effect=asyncio.TimeoutError())
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")

        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(instance.create_resource("src-a", src))

        assert mock_config.state.destination["users"]["src-a"] == created_user

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
        mock_config.destination_client.post = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {}}}
        )
        mock_config.destination_client.patch = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {}}}
        )

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


def _make_service_account(handle, email, user_id, name="Service Account", roles=None):
    """Build a service-account user dict (service_account=True)."""
    user = _make_user(handle, email, user_id, name=name, roles=roles)
    user["attributes"]["service_account"] = True
    return user


class TestServiceAccountCreatePath:
    def test_service_account_uses_service_accounts_endpoint(self, mock_config):
        """sa1: a user with service_account=true is created via
        POST /api/v2/service_accounts, carrying the required service_account flag
        but not the read-only handle/disabled."""
        mock_config.use_v1_user_api = False
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-sa", "attributes": {}}})
        mock_config.destination_client.patch = AsyncMock()
        src = _make_service_account("service-account-handle", "svc-a@example.com", "src-sa")

        _id, r = asyncio.run(instance.create_resource("src-sa", src))

        assert _post_paths(mock_config) == ["/api/v2/service_accounts"]
        mock_config.destination_client.patch.assert_not_awaited()
        _, body = mock_config.destination_client.post.call_args.args
        attrs = body["data"]["attributes"]
        assert attrs["service_account"] is True
        assert "handle" not in attrs
        assert "disabled" not in attrs
        assert r["id"] == "dest-sa"

    def test_service_account_precedes_v1_flag(self, mock_config):
        """sa2: even with --use-v1-user-api on, a service account still routes to
        the service_accounts endpoint (v1 /api/v1/user creates regular users)."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-sa", "attributes": {}}})
        src = _make_service_account("svc-a@example.com", "svc-a@example.com", "src-sa")

        asyncio.run(instance.create_resource("src-sa", src))

        assert _post_paths(mock_config) == ["/api/v2/service_accounts"]

    def test_service_account_keeps_role_relationships(self, mock_config):
        """sa3: role relationships are sent to the service_accounts endpoint so
        the SA is created with its mapped roles."""
        mock_config.use_v1_user_api = False
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        role = {"id": "role-dst", "type": "roles"}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-sa", "attributes": {}}})
        src = _make_service_account("svc-a@example.com", "svc-a@example.com", "src-sa", roles=[role])

        asyncio.run(instance.create_resource("src-sa", src))

        _, body = mock_config.destination_client.post.call_args.args
        assert body["data"]["relationships"]["roles"]["data"] == [role]

    def test_regular_user_v2_body_omits_service_account(self, mock_config):
        """sa4: a regular (non-SA) user still creates via /api/v2/users and its
        body must not carry the service_account flag."""
        mock_config.use_v1_user_api = False
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-x", "attributes": {}}})
        mock_config.destination_client.patch = AsyncMock(
            return_value={"data": {"id": "dest-x", "type": "users", "attributes": {"email": "shared@example.com"}}}
        )
        src = _make_user("user-a@example.com", "shared@example.com", "src-a")
        src["attributes"]["service_account"] = False

        asyncio.run(instance.create_resource("src-a", src))

        assert _post_paths(mock_config) == ["/api/v2/users"]
        _, body = mock_config.destination_client.post.call_args.args
        assert "service_account" not in body["data"]["attributes"]

    def test_service_account_403_warns_about_required_permission(self, mock_config):
        """A missing service-account permission is actionable in the logs."""
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(side_effect=_http_error(403))
        src = _make_service_account("svc-a@example.com", "svc-a@example.com", "src-sa")

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(instance.create_resource("src-sa", src))

        mock_config.logger.warning.assert_called_once()
        warning_args = mock_config.logger.warning.call_args.args
        assert "service_account_write" in warning_args[0]
        assert warning_args[1:] == ("src-sa",)


class TestServiceAccountPrepPreservation:
    def test_prep_resource_preserves_service_account(self, mock_config):
        """sa6: prep_resource must not strip service_account — create-path routing
        depends on the flag surviving to create_resource (guards against it being
        re-added to excluded_attributes)."""
        from datadog_sync.utils.resource_utils import prep_resource

        # Instantiate once so build_excluded_attributes normalizes the config.
        Users(mock_config)
        resource = _make_service_account("svc-a@example.com", "svc-a@example.com", "src-sa")
        prep_resource(Users.resource_config, resource)
        assert resource["attributes"]["service_account"] is True


class TestServiceAccountDiffExclusion:
    def test_account_type_mismatch_warns_before_diff_is_ignored(self, mock_config):
        """The apply hook makes a read-only account-type mismatch visible."""
        instance = Users(mock_config)
        destination = _make_user("user-a@example.com", "shared@example.com", "dest-user")
        destination["attributes"]["service_account"] = False
        source = _make_service_account("user-a@example.com", "shared@example.com", "src-user")
        mock_config.state.destination["users"]["src-user"] = destination

        asyncio.run(instance.pre_resource_action_hook("src-user", source))

        mock_config.logger.warning.assert_called_once()
        warning_args = mock_config.logger.warning.call_args.args
        assert "account type differs" in warning_args[0]
        assert warning_args[1:] == ("src-user", True, False)

    def test_existing_handle_account_type_mismatch_warns_on_create_path(self, mock_config):
        """A first-run handle match also warns before taking the update path."""
        instance = Users(mock_config)
        destination = _make_user(
            "user-a@example.com",
            "shared@example.com",
            "dest-user",
            name="Service Account",
        )
        destination["attributes"]["service_account"] = False
        source = _make_service_account("user-a@example.com", "shared@example.com", "src-user")
        instance._existing_resources_map = {"user-a@example.com": destination}

        asyncio.run(instance.create_resource("src-user", source))

        mock_config.logger.warning.assert_called_once()
        warning_args = mock_config.logger.warning.call_args.args
        assert "account type differs" in warning_args[0]
        assert warning_args[1:] == ("src-user", True, False)

    def test_service_account_excluded_from_update_diff(self):
        """sa5: service_account is create-time routing metadata and must never
        drive a spurious PATCH (guards the diff exclusion)."""
        dest = _make_user("user-a@example.com", "shared@example.com", "same-id")
        dest["attributes"]["service_account"] = False
        src = _make_user("user-a@example.com", "shared@example.com", "same-id")
        src["attributes"]["service_account"] = True
        assert not check_diff(Users.resource_config, dest, src)
