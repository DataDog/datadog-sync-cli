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
from unittest.mock import AsyncMock

from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Command
from datadog_sync.model.users import Users
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resource_utils import check_diff


def _make_user(handle, email, user_id, name="User"):
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
        "relationships": {"roles": {"data": []}},
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
        instance = Users(mock_config)
        instance._existing_resources_map = {}
        mock_config.destination_client.post = AsyncMock(
            return_value={"data": {"id": "dest-x", "attributes": {}}}
        )
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
        mock_config.state.destination["users"][_id] = _make_user(
            "user-a@example.com", "shared@example.com", "dest-a"
        )
        # A differing name forces a diff -> the PATCH branch.
        src = _make_user("user-a@example.com", "shared@example.com", "dest-a", name="New Name")
        mock_config.destination_client.patch = AsyncMock(
            return_value={"data": {"id": "dest-a", "attributes": {}}}
        )

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
        result = CliRunner(mix_stderr=False).invoke(
            cli, ["sync", "--use-v1-user-api=true", "--validate=false"]
        )
        assert result.exit_code != 2

    def test_migrate_accepts_v1_flag(self):
        """p3: migrate reuses @sync_options, so it recognizes the flag too."""
        result = CliRunner(mix_stderr=False).invoke(
            cli, ["migrate", "--use-v1-user-api=true", "--validate=false"]
        )
        assert result.exit_code != 2
