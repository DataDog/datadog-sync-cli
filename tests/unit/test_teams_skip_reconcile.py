# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""teams: regression guard + framework safety-net proof.

teams' exists-path is already correct: create_resource writes state.destination
from the map entry then delegates to update_resource (PATCH), so it never raises
SkipResource in the exists-path and the framework is a pure no-op. The safety-net
test proves the framework WOULD reconcile teams if its exists-path regressed.

All identifiers are obviously synthetic (``team-src``, ``team-dst``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.teams import Teams
from datadog_sync.utils.resource_utils import SkipResource


def _team(name, handle, team_id=None):
    return {
        "id": team_id or f"team-id-{name}",
        "type": "team",
        "attributes": {"name": name, "handle": handle},
    }


def _make_teams(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    teams = Teams(config=config)
    teams._existing_resources_map = existing_map or {}
    return teams, config


class TestTeamsSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        _id = "team-src"
        dest_team = _team("team-dst", "team-dst-handle", team_id="team-dst")
        updated = _team("team-dst", "team-dst-handle", team_id="team-dst")
        updated["attributes"]["marker"] = "patched"
        destination_client = MagicMock()
        destination_client.patch = AsyncMock(return_value={"data": updated})
        teams, config = _make_teams(
            existing_map={"team-dst:team-dst-handle": dest_team},
            destination_client=destination_client,
        )
        resource = _team("team-dst", "team-dst-handle", team_id=_id)

        asyncio.run(teams._create_resource(_id, resource))

        destination_client.patch.assert_called_once()
        assert config.state.destination["teams"][_id] == updated

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "team-src"
        dest_team = _team("team-dst", "team-dst-handle", team_id="team-dst")
        teams, config = _make_teams(existing_map={"team-dst:team-dst-handle": dest_team})
        teams.create_resource = AsyncMock(side_effect=SkipResource(_id, "teams", "exists"))
        resource = _team("team-dst", "team-dst-handle", team_id=_id)

        with pytest.raises(SkipResource):
            asyncio.run(teams._create_resource(_id, resource))

        assert config.state.destination["teams"][_id] == dest_team

    def test_create_non_existing_still_posts(self):
        _id = "team-src"
        posted = _team("team-src", "team-src-handle", team_id="team-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        teams, config = _make_teams(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _team("team-src", "team-src-handle", team_id=_id)

        out_id, out_r = asyncio.run(teams.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
