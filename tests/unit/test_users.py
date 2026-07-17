# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for handle-keyed users + the --use-v1-user-api flag.

The Datadog destination enforces user uniqueness on the ``handle``, not the
``email`` — multiple users may share an email. Keying ``_existing_resources_map``
by email collapsed distinct-handle users onto one derived handle and caused a
409 Conflict on the second create. These tests cover switching the user mapping
key to the exact-case handle (PR1) and wiring the opt-in --use-v1-user-api flag.

Tests ``a``/``a2``/``a3``/``f`` are red against ``resource_mapping_key=
"attributes.email"`` and green after the switch to ``"attributes.handle"`` plus
the manual handle pop before the v2 POST. Test ``g`` is a green/green guard that
handle stays excluded from update diffs across the excluded_attributes ->
exclude_regex_paths migration. ``p1``/``p2``/``p3`` guard the flag wiring.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Command
from datadog_sync.model.users import UserRoleAssignmentError, Users
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff


def _http_error(status):
    """Build a CustomClientHTTPError with the given status code."""
    response = MagicMock()
    response.status = status
    response.message = "Conflict" if status == 409 else "Error"
    return CustomClientHTTPError(response)


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


def _base_kwargs(tmp_path):
    """Minimal build_config kwargs that avoid network/validation."""
    return dict(
        resources="users",
        resource_per_file=True,
        source_api_key="k",
        source_app_key="k",
        destination_api_key="k",
        destination_app_key="k",
        source_api_url="https://example.com",
        destination_api_url="https://example.com",
        storage_type="local",
        source_resources_path=str(tmp_path / "source"),
        destination_resources_path=str(tmp_path / "dest"),
        max_workers=1,
        send_metrics=False,
        verify_ddr_status=False,
        validate=False,
        show_progress_bar=False,
        allow_self_lockout=False,
        force_missing_dependencies=False,
        skip_failed_resource_connections=False,
    )


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
        mock_config.use_v1_user_api = False
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


class TestV1UserApiFlagWiring:
    def test_build_config_flag_true(self, tmp_path):
        """p1: --use-v1-user-api flows from kwargs into Configuration."""
        cfg = build_config(Command.IMPORT, use_v1_user_api=True, **_base_kwargs(tmp_path))
        assert cfg.use_v1_user_api is True

    def test_build_config_flag_default_false(self, tmp_path):
        """p1: absent flag defaults to False (guards a typo'd kwarg key)."""
        cfg = build_config(Command.IMPORT, **_base_kwargs(tmp_path))
        assert cfg.use_v1_user_api is False

    def test_sync_accepts_v1_flag(self):
        """p2: the sync command recognizes the flag (exit 2 == usage error)."""
        result = CliRunner(mix_stderr=False).invoke(cli, ["sync", "--use-v1-user-api=true", "--validate=false"])
        assert result.exit_code != 2

    def test_migrate_accepts_v1_flag(self):
        """p3: migrate reuses @sync_options, so it recognizes the flag too."""
        result = CliRunner(mix_stderr=False).invoke(cli, ["migrate", "--use-v1-user-api=true", "--validate=false"])
        assert result.exit_code != 2


def _mock_paginated(config, pages):
    """Make destination_client.paginated_request(get)(...) yield ``pages`` in order."""
    inner = AsyncMock(side_effect=pages)
    config.destination_client.paginated_request = MagicMock(return_value=inner)
    return inner


def _post_paths(mock_config):
    return [c.args[0] for c in mock_config.destination_client.post.call_args_list]


class TestV2CreatePath:
    def test_flag_off_uses_v2_not_v1(self, mock_config):
        """b: with the flag off, create goes through the v2 endpoint and never
        touches the v1 API (preserves the default upstream behavior)."""
        mock_config.use_v1_user_api = False
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {"id": "dest-x", "attributes": {}}})

        asyncio.run(instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a")))

        assert _post_paths(mock_config) == ["/api/v2/users"]


class TestV1CreatePath:
    def test_flag_on_uses_v1_with_handle_not_v2(self, mock_config):
        """d1: with the flag on, create posts to /api/v1/user carrying the handle,
        never calls the v2 users endpoint, and returns the reconciled v2 UUID."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        dest_user = _make_user("user-a@example.com", "shared@example.com", "dest-uuid-a")
        mock_config.destination_client.post = AsyncMock(return_value={"data": {}})
        _mock_paginated(mock_config, [[dest_user]])

        _id, r = asyncio.run(
            instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a"))
        )

        v1_calls = [c for c in mock_config.destination_client.post.call_args_list if c.args[0] == "/api/v1/user"]
        assert len(v1_calls) == 1
        assert v1_calls[0].args[1]["handle"] == "user-a@example.com"
        assert "/api/v2/users" not in _post_paths(mock_config)
        assert r["id"] == "dest-uuid-a"

    def test_shared_email_distinct_handles_both_created_via_v1(self, mock_config):
        """The core fix. Two users share an email but have distinct handles: one
        handle equals the shared email, the other differs. Under v2 the first
        create derives its handle from the email and steals the second user's
        handle, so the second 409s and can never be created. Via v1 each user is
        created with its OWN handle, so both succeed."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        dest_a = _make_user("abc@example.com", "jsmith@example.com", "dest-a")
        dest_b = _make_user("jsmith@example.com", "jsmith@example.com", "dest-b")
        mock_config.destination_client.post = AsyncMock(return_value={"data": {}})
        _mock_paginated(mock_config, [[dest_a], [dest_b]])

        asyncio.run(instance.create_resource("src-a", _make_user("abc@example.com", "jsmith@example.com", "src-a")))
        asyncio.run(instance.create_resource("src-b", _make_user("jsmith@example.com", "jsmith@example.com", "src-b")))

        v1_handles = [
            c.args[1]["handle"]
            for c in mock_config.destination_client.post.call_args_list
            if c.args[0] == "/api/v1/user"
        ]
        assert v1_handles == ["abc@example.com", "jsmith@example.com"]
        assert "/api/v2/users" not in _post_paths(mock_config)

    def test_assigns_only_missing_roles_and_returns_updated_state(self, mock_config):
        """v1 create assigns mapped roles after resolving the v2 UUID.

        Roles already present on the reconciled user are not posted again, and
        successful assignments are reflected in the destination state returned
        by create_resource.
        """
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        existing_role = {"id": "role-dst-existing", "type": "roles"}
        missing_role = {"id": "role-dst-missing", "type": "roles"}
        dest_user = _make_user(
            "user-a@example.com",
            "shared@example.com",
            "dest-uuid-a",
            roles=[existing_role],
        )
        source_user = _make_user(
            "user-a@example.com",
            "shared@example.com",
            "src-a",
            roles=[existing_role, missing_role],
        )
        mock_config.destination_client.post = AsyncMock(return_value={"data": {}})
        _mock_paginated(mock_config, [[dest_user]])

        _, created = asyncio.run(instance.create_resource("src-a", source_user))

        assert _post_paths(mock_config) == [
            "/api/v1/user",
            "/api/v2/roles/role-dst-missing/users",
        ]
        assert created["relationships"]["roles"]["data"] == [existing_role, missing_role]

    def test_role_failure_persists_partial_state_and_reports_failure(self, mock_config):
        """A failed role assignment is reported after later roles are attempted.

        Only successful assignments are recorded in returned destination state,
        leaving failed roles eligible for retry on a later sync. The exception
        lets the apply handler count the otherwise-partial create as a failure.
        """
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        failed_role = {"id": "role-dst-failed", "type": "roles"}
        successful_role = {"id": "role-dst-success", "type": "roles"}
        dest_user = _make_user("user-a@example.com", "shared@example.com", "dest-uuid-a")
        source_user = _make_user(
            "user-a@example.com",
            "shared@example.com",
            "src-a",
            roles=[failed_role, successful_role],
        )

        async def post(path, _body):
            if path == "/api/v2/roles/role-dst-failed/users":
                raise _http_error(403)
            return {"data": {}}

        mock_config.destination_client.post = AsyncMock(side_effect=post)
        _mock_paginated(mock_config, [[dest_user]])

        with pytest.raises(
            UserRoleAssignmentError, match="1 role assignment failed while reconciling user"
        ) as exc_info:
            asyncio.run(instance.create_resource("src-a", source_user))

        assert exc_info.value.failed_role_ids == ("role-dst-failed",)
        assert _post_paths(mock_config) == [
            "/api/v1/user",
            "/api/v2/roles/role-dst-failed/users",
            "/api/v2/roles/role-dst-success/users",
        ]
        created = mock_config.state.destination["users"]["src-a"]
        assert created["relationships"]["roles"]["data"] == [successful_role]
        mock_config.logger.error.assert_called_once()

    def test_requires_handle(self, mock_config):
        """d0: v1 creation raises when there is no handle to send."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        with pytest.raises(ValueError):
            asyncio.run(instance._create_via_v1("", "Person Name", "e@example.com"))

    def test_v1_post_failure_reraises(self, mock_config):
        """d4: a v1 POST failure propagates so the apply loop counts it failed
        and continues (DR-safe)."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(side_effect=_http_error(400))
        with pytest.raises(CustomClientHTTPError):
            asyncio.run(
                instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a"))
            )


class TestReconcileByHandle:
    def test_no_exact_match_raises(self, mock_config):
        """If no exact-handle match is ever found after the v1 create, it raises."""
        mock_config.use_v1_user_api = True
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(return_value={"data": {}})
        other = _make_user("user-b@example.com", "shared@example.com", "dest-b")
        _mock_paginated(mock_config, [[other], [other], [other]])
        with pytest.raises(ValueError):
            asyncio.run(
                instance.create_resource("src-a", _make_user("user-a@example.com", "shared@example.com", "src-a"))
            )

    def test_requeries_on_empty_then_matches(self, mock_config):
        """d3b: read-after-write — an empty first page then a match re-queries
        (exactly two calls) rather than giving up on the first empty result."""
        instance = Users(mock_config)
        match = _make_user("user-a@example.com", "shared@example.com", "dest-uuid-a")
        inner = _mock_paginated(mock_config, [[], [match]])
        user = asyncio.run(instance._get_destination_user_by_handle("user-a@example.com"))
        assert user["id"] == "dest-uuid-a"
        assert inner.call_count == 2

    def test_selects_exact_case_handle_among_candidates(self, mock_config):
        """d5: with multiple filter candidates, only the exact-case handle wins."""
        instance = Users(mock_config)
        a = _make_user("user-a@example.com", "shared@example.com", "dest-a")
        b = _make_user("user-b@example.com", "shared@example.com", "dest-b")
        _mock_paginated(mock_config, [[b, a]])
        user = asyncio.run(instance._get_destination_user_by_handle("user-a@example.com"))
        assert user["id"] == "dest-a"


class TestUpdatePathRegression:
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
        """e: an existing destination handle routes to the update path — no v1 or
        v2 create, no duplicate."""
        mock_config.use_v1_user_api = True
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
