# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""team_memberships: destination-state reconciliation on the exists-skip path.

``team_memberships.create_resource`` discovers (via ``_existing_resources_map``
keyed by ``team_id:user_id``) that a membership already exists on the
destination and raises ``SkipResource("User is already a member of the team")``
without writing ``state.destination``. Without framework reconciliation, no
destination state file is ever persisted for that id, so downstream consumers
that trust the bucket count the id as failed even though the membership is
confirmed present via the live API. The framework wrapper now reconciles this.

All identifiers are obviously synthetic (``team-src``, ``team-dst`` ...).
"""

import asyncio
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from datadog_sync.model.team_memberships import TeamMemberships
from datadog_sync.utils.resource_utils import SkipResource


def _membership(team_id, user_id, membership_id=None):
    return {
        "type": "team_memberships",
        "id": membership_id or f"TeamMembership-{team_id}-{user_id}",
        "attributes": {"role": "member"},
        "relationships": {
            "team": {"data": {"type": "team", "id": team_id}},
            "user": {"data": {"type": "users", "id": user_id}},
        },
    }


def _make_team_memberships(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    tm = TeamMemberships(config=config)
    tm._existing_resources_map = existing_map or {}
    return tm, config


class TestTeamMembershipsSkipReconcile:
    def test_create_skip_reconciles_state_destination(self):
        # Post-connect shape: source resource already carries *destination* ids.
        existing_dest_member = _membership("team-dst", "user-dst", "TeamMembership-team-dst-user-dst")
        tm, config = _make_team_memberships(
            existing_map={"team-dst:user-dst": existing_dest_member},
        )
        src_resource = _membership("team-dst", "user-dst", "TeamMembership-team-src-user-src")
        _id = "TeamMembership-team-src-user-src"

        with pytest.raises(SkipResource, match="already a member"):
            asyncio.run(tm._create_resource(_id, src_resource))

        assert config.state.destination["team_memberships"][_id] == existing_dest_member

    def test_update_delegating_to_create_reconciles(self):
        # update_resource with no in-state destination delegates to create_resource,
        # which raises SkipResource on the existing membership. The _update_resource
        # wrapper must reconcile.
        existing_dest_member = _membership("team-dst", "user-dst", "TeamMembership-team-dst-user-dst")
        tm, config = _make_team_memberships(
            existing_map={"team-dst:user-dst": existing_dest_member},
        )
        src_resource = _membership("team-dst", "user-dst", "TeamMembership-team-src-user-src")
        _id = "TeamMembership-team-src-user-src"

        with pytest.raises(SkipResource):
            asyncio.run(tm._update_resource(_id, src_resource))

        assert config.state.destination["team_memberships"][_id] == existing_dest_member

    def test_update_no_diff_skip_does_not_overwrite(self):
        # update_resource finds the membership in-state AND in the map with no diff,
        # raising SkipResource("No differences detected"). Insert-if-absent must
        # leave the pre-existing entry untouched (same object identity), not replace
        # it with the live map entry.
        existing_dest_member = _membership("team-dst", "user-dst", "TeamMembership-team-dst-user-dst")
        sentinel = _membership("team-dst", "user-dst", "TeamMembership-team-dst-user-dst")
        tm, config = _make_team_memberships(
            existing_map={"team-dst:user-dst": existing_dest_member},
        )
        _id = "TeamMembership-team-src-user-src"
        config.state.destination["team_memberships"][_id] = sentinel
        src_resource = _membership("team-dst", "user-dst", "TeamMembership-team-src-user-src")

        with pytest.raises(SkipResource, match="No differences detected"):
            asyncio.run(tm._update_resource(_id, src_resource))

        # Insert-if-absent: the framework must not overwrite the pre-existing entry.
        assert config.state.destination["team_memberships"][_id] is sentinel

    def test_create_still_posts_when_not_existing(self):
        from unittest.mock import AsyncMock

        posted = _membership("team-dst", "user-dst", "TeamMembership-team-dst-user-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        tm, config = _make_team_memberships(
            existing_map={},
            destination_client=destination_client,
        )
        src_resource = _membership("team-dst", "user-dst", "TeamMembership-team-src-user-src")
        _id = "TeamMembership-team-src-user-src"

        out_id, out_r = asyncio.run(tm.create_resource(_id, src_resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
