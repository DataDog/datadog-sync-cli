# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for team_memberships id-file fan-out + _ID_FILE_SUPPORTED_TYPES."""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.team_memberships import TeamMemberships
from datadog_sync.utils.configuration import _ID_FILE_SUPPORTED_TYPES


class _MockState:
    """Minimal state mock used to assert fetch-path side effects."""

    def __init__(self):
        self.source = defaultdict(dict)

    def set_source(self, resource_type, _id, resource):
        self.source[resource_type][_id] = resource


def _make_member(team_id, user_id):
    return {
        "type": "team_memberships",
        "id": f"TeamMembership-{team_id}-{user_id}",
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
    config.source_client.paginated_request = MagicMock(
        return_value=AsyncMock(return_value=members)
    )
    config.source_client.get = MagicMock()

    tm = TeamMemberships(config=config)
    return tm, config, members


class TestTeamMembershipsIDFileSupport:
    def test_team_memberships_in_id_file_supported_types(self):
        assert "team_memberships" in _ID_FILE_SUPPORTED_TYPES

    def test_get_resources_by_ids_team_id_fans_out_all_memberships(self):
        tm, config, _ = _make_team_memberships(
            "team-abc", ["user-1", "user-2", "user-3"]
        )
        resources, missing, errored = asyncio.run(
            tm.get_resources_by_ids(
                config.source_client, ["team-abc"], max_concurrent_reads=10
            )
        )
        assert len(resources) == 3
        assert missing == []
        assert errored == []
        assert {r["relationships"]["team"]["data"]["id"] for r in resources} == {
            "team-abc"
        }

    def test_get_resources_by_ids_is_side_effect_free_for_source_state(self):
        tm, config, _ = _make_team_memberships("team-abc", ["user-1", "user-2"])
        asyncio.run(
            tm.get_resources_by_ids(
                config.source_client, ["team-abc"], max_concurrent_reads=10
            )
        )
        assert config.state.source["team_memberships"] == {}

    def test_get_resources_by_ids_empty_team_is_reported_as_skipped(self):
        tm, config, _ = _make_team_memberships("team-empty", [])
        resources, missing, errored = asyncio.run(
            tm.get_resources_by_ids(
                config.source_client, ["team-empty"], max_concurrent_reads=10
            )
        )
        assert resources == []
        assert missing == []
        assert len(errored) == 1
        assert errored[0][0] == "team-empty"
        assert errored[0][1] == "skipped"
        assert "no members" in errored[0][2]

    def test_get_resources_by_ids_cross_team_memberships_keep_both_rows(self):
        config = MagicMock()
        config.state = _MockState()
        config.source_client = AsyncMock()
        config.source_client.paginated_request = MagicMock(
            side_effect=[
                AsyncMock(return_value=[_make_member("team-A", "shared-user")]),
                AsyncMock(return_value=[_make_member("team-B", "shared-user")]),
            ]
        )
        config.source_client.get = MagicMock()

        tm = TeamMemberships(config=config)
        resources, missing, errored = asyncio.run(
            tm.get_resources_by_ids(
                config.source_client, ["team-A", "team-B"], max_concurrent_reads=10
            )
        )

        assert missing == []
        assert errored == []
        keys = {
            (
                r["relationships"]["team"]["data"]["id"],
                r["relationships"]["user"]["data"]["id"],
            )
            for r in resources
        }
        assert keys == {("team-A", "shared-user"), ("team-B", "shared-user")}

    def test_get_resources_by_ids_api_error_is_classified_as_permanent(self):
        config = MagicMock()
        config.source_client = AsyncMock()
        config.state = _MockState()
        config.source_client.paginated_request = MagicMock(
            return_value=AsyncMock(side_effect=Exception("membership API 500"))
        )
        config.source_client.get = MagicMock()

        tm = TeamMemberships(config=config)
        resources, missing, errored = asyncio.run(
            tm.get_resources_by_ids(
                config.source_client, ["team-xyz"], max_concurrent_reads=10
            )
        )

        assert resources == []
        assert missing == []
        assert len(errored) == 1
        assert errored[0][0] == "team-xyz"
        assert errored[0][1] == "permanent"
        assert "membership API 500" in errored[0][2]

    def test_import_resource_resource_arg_path_unaffected(self):
        config = MagicMock()
        config.state = _MockState()
        tm = TeamMemberships(config=config)

        existing_resource = _make_member("team-x", "user-y")
        _id, result = asyncio.run(
            tm.import_resource(_id=existing_resource["id"], resource=existing_resource)
        )

        assert _id == existing_resource["id"]
        assert result == existing_resource

    def test_import_resource_id_only_path_is_rejected(self):
        config = MagicMock()
        tm = TeamMemberships(config=config)

        with pytest.raises(ValueError, match="direct ID import is not supported"):
            asyncio.run(tm.import_resource(_id="team-abc"))
