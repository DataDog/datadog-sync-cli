# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for team_memberships fan-out + _ID_FILE_SUPPORTED_TYPES."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collections import defaultdict

from datadog_sync.utils.configuration import _ID_FILE_SUPPORTED_TYPES
from datadog_sync.model.team_memberships import TeamMemberships
from datadog_sync.utils.resource_utils import SkipResource


class _MockState:
    """Minimal state mock that supports set_source, get_source_keys, delete_source."""

    def __init__(self):
        self.source = defaultdict(dict)

    def set_source(self, resource_type, _id, resource):
        self.source[resource_type][_id] = resource

    def get_source_keys(self, resource_type):
        return list(self.source[resource_type].keys())

    def delete_source(self, resource_type, _id):
        self.source[resource_type].pop(_id, None)


def _make_member(team_id, user_id):
    return {
        "type": "team_memberships",
        "id": f"{team_id}:{user_id}",
        "attributes": {"role": "member"},
        "relationships": {
            "team": {"data": {"type": "team", "id": team_id}},
            "user": {"data": {"type": "users", "id": user_id}},
        },
    }


def _make_team_memberships(team_id, user_ids):
    config = MagicMock()
    config.source_client = MagicMock()
    config.state = _MockState()

    members = [_make_member(team_id, uid) for uid in user_ids]
    # paginated_request(fn) returns an async callable that returns the member list
    config.source_client.paginated_request = MagicMock(return_value=AsyncMock(return_value=members))
    config.source_client.get = MagicMock()

    tm = TeamMemberships(config=config)
    return tm, config, members


class TestTeamMembershipsIDFileSupport:

    def test_team_memberships_in_id_file_supported_types(self):
        assert "team_memberships" in _ID_FILE_SUPPORTED_TYPES

    def test_import_resource_id_fan_out_produces_n_rows(self):
        tm, config, _ = _make_team_memberships("team-abc", ["user-1", "user-2", "user-3"])
        asyncio.run(tm.import_resource(_id="team-abc"))
        assert len(config.state.source["team_memberships"]) == 3

    def test_import_resource_id_composite_key_format(self):
        tm, config, _ = _make_team_memberships("team-abc", ["user-1", "user-2"])
        asyncio.run(tm.import_resource(_id="team-abc"))
        keys = set(config.state.source["team_memberships"].keys())
        assert "team-abc:user-1" in keys
        assert "team-abc:user-2" in keys
        for k in keys:
            assert ":" in k, f"keys must be composite team_id:user_id, got {k!r}"

    def test_import_resource_id_empty_team_writes_nothing(self):
        tm, config, _ = _make_team_memberships("team-empty", [])
        with pytest.raises(SkipResource):
            asyncio.run(tm.import_resource(_id="team-empty"))
        all_keys = list(config.state.source["team_memberships"].keys())
        assert len(all_keys) == 0, (
            "empty team must write no state rows at all (no bare team_id key, " f"no composite keys); got: {all_keys}"
        )

    def test_import_resource_resource_arg_path_unaffected(self):
        """2-arg import_resource path must remain unchanged (used by get_resources())."""
        config = MagicMock()
        config.state = _MockState()
        tm = TeamMemberships(config=config)
        existing_resource = _make_member("team-x", "user-y")
        _id, result = asyncio.run(tm.import_resource(_id="team-x:user-y", resource=existing_resource))
        assert _id == "team-x:user-y"
        assert result == existing_resource

    def test_get_resources_unaffected(self):
        """get_resources() is not called by import_resource; verify it still exists."""
        config = MagicMock()
        tm = TeamMemberships(config=config)
        assert hasattr(tm, "get_resources"), "get_resources() must still exist"

    def test_import_resource_id_api_error_propagates(self):
        config = MagicMock()
        config.source_client = AsyncMock()
        config.state = _MockState()
        config.source_client.paginated_request = MagicMock(
            return_value=AsyncMock(side_effect=Exception("membership API 500"))
        )
        config.source_client.get = MagicMock()
        tm = TeamMemberships(config=config)
        with pytest.raises(Exception, match="membership API 500"):
            asyncio.run(tm.import_resource(_id="team-xyz"))
        assert len(config.state.source["team_memberships"]) == 0

    def test_import_resource_id_idempotent(self):
        tm, config, _ = _make_team_memberships("team-abc", ["user-1", "user-2"])
        asyncio.run(tm.import_resource(_id="team-abc"))
        asyncio.run(tm.import_resource(_id="team-abc"))
        keys = [k for k in config.state.source["team_memberships"].keys() if k.startswith("team-abc:user-")]
        assert len(keys) == 2, "two calls with same members must yield exactly 2 unique rows"

    def test_import_resource_id_cross_team_user_both_rows_written(self):
        config = MagicMock()
        config.state = _MockState()

        def make_members_for(team_id):
            return [_make_member(team_id, "shared-user")]

        config.source_client = AsyncMock()
        config.source_client.paginated_request = MagicMock(
            side_effect=[
                AsyncMock(return_value=make_members_for("team-A")),
                AsyncMock(return_value=make_members_for("team-B")),
            ]
        )
        config.source_client.get = MagicMock()
        tm = TeamMemberships(config=config)
        asyncio.run(tm.import_resource(_id="team-A"))
        asyncio.run(tm.import_resource(_id="team-B"))

        keys = set(config.state.source["team_memberships"].keys())
        assert "team-A:shared-user" in keys
        assert "team-B:shared-user" in keys

    def test_import_resource_id_member_removed_between_calls(self):
        """Second import with fewer members removes stale rows from first import."""
        config = MagicMock()
        config.state = _MockState()
        config.source_client = AsyncMock()

        first_members = [_make_member("team-x", "user-1"), _make_member("team-x", "user-2")]
        second_members = [_make_member("team-x", "user-1")]  # user-2 removed

        config.source_client.paginated_request = MagicMock(
            side_effect=[
                AsyncMock(return_value=first_members),
                AsyncMock(return_value=second_members),
            ]
        )
        config.source_client.get = MagicMock()
        tm = TeamMemberships(config=config)
        asyncio.run(tm.import_resource(_id="team-x"))
        asyncio.run(tm.import_resource(_id="team-x"))

        composite_keys = [k for k in config.state.source["team_memberships"].keys() if k.startswith("team-x:user-")]
        assert "team-x:user-1" in composite_keys
        assert "team-x:user-2" not in composite_keys, "user-2 was removed between imports; stale row must be deleted"

    def test_state_writer_overwrites_not_appends_for_team(self):
        """Calling _import_team_memberships_by_team_id twice with different member sets
        must result in ONLY the second call's rows — pins the overwrite-not-append invariant."""
        config = MagicMock()
        config.state = _MockState()
        config.source_client = AsyncMock()

        first_members = [_make_member("team-A", "user-1"), _make_member("team-A", "user-2")]
        second_members = [_make_member("team-A", "user-1"), _make_member("team-A", "user-3")]

        config.source_client.paginated_request = MagicMock(
            side_effect=[
                AsyncMock(return_value=first_members),
                AsyncMock(return_value=second_members),
            ]
        )
        config.source_client.get = MagicMock()
        tm = TeamMemberships(config=config)
        asyncio.run(tm._import_team_memberships_by_team_id("team-A"))
        asyncio.run(tm._import_team_memberships_by_team_id("team-A"))

        composite_keys = {k for k in config.state.source["team_memberships"].keys() if k.startswith("team-A:user-")}
        # Must contain ONLY second call's rows
        assert composite_keys == {"team-A:user-1", "team-A:user-3"}, (
            f"After second call, state must contain ONLY second call's rows. "
            f"Got: {composite_keys}. "
            "If user-2 is present, stale-row removal is broken."
        )
