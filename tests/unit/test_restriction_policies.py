# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for restriction_policies resource handling.

These tests verify that the org: principal remapping in pre_apply_hook and
pre_resource_action_hook handles both success and failure paths correctly.
"""

import asyncio
from collections import defaultdict
import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.restriction_policies import RestrictionPolicies
from datadog_sync.utils.resource_utils import ResourceConnectionError
from datadog_sync.utils.workers import Counter


class TestRestrictionPoliciesOrgPrincipal:
    """Test suite for org: principal remapping in restriction_policies."""

    def _make_resource(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return RestrictionPolicies(mock_config)

    def _seed_source_with_policy(self, resource):
        """Populate source state with one restriction policy resource."""
        resource.config.state.source = {"restriction_policies": {"some-id": {"attributes": {"bindings": []}}}}

    def _seed_source_without_policy(self, resource):
        """Populate source state with no restriction policy resources."""
        resource.config.state.source = {"restriction_policies": {}}

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Successful GET /api/v2/current_user sets org_principal to 'org:{org_uuid}'."""
        resource = self._make_resource()
        self._seed_source_with_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={"data": {"relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}}}}
        )
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal == "org:00000000-0000-beef-0000-000000000000"
        mock_client.get.assert_awaited_once()

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Failed GET /api/v2/current_user leaves org_principal as None and raises."""
        resource = self._make_resource()
        self._seed_source_with_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        resource.config.destination_client = mock_client

        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None

    def test_pre_apply_hook_skips_current_user_when_source_empty(self):
        """Empty source state → GET is not called; org_principal stays None."""
        resource = self._make_resource()
        self._seed_source_without_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None
        mock_client.get.assert_not_awaited()

    def test_pre_resource_action_hook_replaces_org_when_principal_set(self):
        """When org_principal is set, org: entries in bindings are replaced."""
        resource = self._make_resource()
        resource.org_principal = "org:dest-pub-id"

        r = {
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]}
        }
        asyncio.run(resource.pre_resource_action_hook("some-id", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:dest-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"

    def test_pre_resource_action_hook_skips_when_org_principal_none(self):
        """When org_principal is None, org: principals are left unchanged."""
        resource = self._make_resource()
        assert resource.org_principal is None

        r = {
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]}
        }
        asyncio.run(resource.pre_resource_action_hook("some-id", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:source-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"


class TestRestrictionPoliciesConnectResources:
    """connect_resources drop/keep/hard-fail behavior for --drop-unresolvable-principals.

    'stale' == absent from BOTH destination and source (permanently gone).
    'pending' == present in source, absent from destination ('not yet synced').
    'valid' == present in destination (resolves and remaps).
    """

    def _make_resource(self, drop=False, skip_failed=False):
        config = MagicMock()
        config.state = MagicMock()
        config.state.source = defaultdict(dict)
        config.state.destination = defaultdict(dict)
        config.state.ensure_resource_loaded = MagicMock()
        config.drop_unresolvable_principals = drop
        config.skip_failed_resource_connections = skip_failed
        config.counter = Counter()
        config.logger = MagicMock()
        resource = RestrictionPolicies(config)
        # Seed the primary "id" connection so it always resolves; keeps these tests
        # focused on principal handling rather than the dashboard id link.
        config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
        return resource

    def _seed_valid_role(self, resource, src="role-good", dst="role-good-dst"):
        resource.config.state.destination["roles"][src] = {"id": dst}

    def _seed_pending_role(self, resource, src="role-pending"):
        resource.config.state.source["roles"][src] = {"id": src}

    def _policy(self, bindings):
        return {"id": "dashboard:dash-src", "attributes": {"bindings": bindings}}

    # --- flag OFF: byte-for-byte unchanged behavior --------------------------------

    def test_flag_off_stale_principal_hard_fails(self):
        resource = self._make_resource(drop=False)
        policy = self._policy([{"principals": ["role:role-gone"], "relation": "editor"}])

        with pytest.raises(ResourceConnectionError):
            resource.connect_resources("dashboard:dash-src", policy)

        resource.config.counter  # no drop recorded
        assert resource.config.counter.stale_principals_dropped_by_type == {}

    def test_flag_off_valid_principal_remaps_and_succeeds(self):
        resource = self._make_resource(drop=False)
        self._seed_valid_role(resource)
        policy = self._policy([{"principals": ["role:role-good"], "relation": "editor"}])

        resource.connect_resources("dashboard:dash-src", policy)  # no raise

        assert policy["attributes"]["bindings"][0]["principals"] == ["role:role-good-dst"]

    # --- flag ON: drop stale, keep syncing -----------------------------------------

    def test_flag_on_drops_stale_keeps_valid_and_syncs(self):
        resource = self._make_resource(drop=True)
        self._seed_valid_role(resource)
        policy = self._policy([{"principals": ["role:role-good", "role:role-gone"], "relation": "editor"}])

        resource.connect_resources("dashboard:dash-src", policy)  # no raise

        assert policy["attributes"]["bindings"][0]["principals"] == ["role:role-good-dst"]
        resource.config.logger.warning.assert_called()
        assert resource.config.counter.stale_principals_dropped_by_type["restriction_policies"] == [
            "dashboard:dash-src"
        ]

    def test_flag_on_pending_principal_still_hard_fails(self):
        resource = self._make_resource(drop=True)
        self._seed_pending_role(resource)  # in source, not destination
        policy = self._policy([{"principals": ["role:role-pending"], "relation": "editor"}])

        with pytest.raises(ResourceConnectionError) as exc_info:
            resource.connect_resources("dashboard:dash-src", policy)

        # Not an access-elevation case -- it's the legitimate retry-later path.
        assert exc_info.value.empty_binding_risk is False
        assert resource.config.counter.stale_principals_dropped_by_type == {}

    def test_flag_on_binding_emptied_raises_empty_binding_risk(self):
        resource = self._make_resource(drop=True)
        policy = self._policy([{"principals": ["role:role-gone"], "relation": "editor"}])

        with pytest.raises(ResourceConnectionError) as exc_info:
            resource.connect_resources("dashboard:dash-src", policy)

        assert exc_info.value.empty_binding_risk is True
        resource.config.logger.error.assert_called()
        assert policy["attributes"]["bindings"][0]["principals"] == []

    def test_multiple_bindings_only_one_empties_still_raises(self):
        resource = self._make_resource(drop=True)
        self._seed_valid_role(resource)
        policy = self._policy(
            [
                {"principals": ["role:role-good"], "relation": "viewer"},
                {"principals": ["role:role-gone"], "relation": "editor"},
            ]
        )

        with pytest.raises(ResourceConnectionError) as exc_info:
            resource.connect_resources("dashboard:dash-src", policy)

        assert exc_info.value.empty_binding_risk is True
        # Resource-level skip: the surviving binding was still filtered/remapped in place.
        assert policy["attributes"]["bindings"][0]["principals"] == ["role:role-good-dst"]
        assert policy["attributes"]["bindings"][1]["principals"] == []

    def test_skip_failed_resource_connections_suppresses_empty_binding_raise(self):
        resource = self._make_resource(drop=True, skip_failed=True)
        policy = self._policy([{"principals": ["role:role-gone"], "relation": "editor"}])

        # skip_failed_resource_connections stays authoritative: no raise even for the
        # empty-binding risk case.
        connection_result = resource.connect_resources("dashboard:dash-src", policy)

        assert policy["attributes"]["bindings"][0]["principals"] == []
        assert connection_result.empty_binding_escalation is True
        message = resource.config.logger.error.call_args.args[0]
        assert "continuing sync" in message
        assert "DESTINATION RESOURCE MAY BE UNRESTRICTED" in message
        assert "refusing to sync" not in message

    def test_middle_principal_drop_does_not_skip_neighbors(self):
        # Enumerate/index-shift regression: stale entry in the MIDDLE of 3.
        resource = self._make_resource(drop=True)
        self._seed_valid_role(resource, src="role-a", dst="role-a-dst")
        self._seed_valid_role(resource, src="role-c", dst="role-c-dst")
        policy = self._policy([{"principals": ["role:role-a", "role:role-gone", "role:role-c"], "relation": "editor"}])

        resource.connect_resources("dashboard:dash-src", policy)  # no raise

        # Both neighbors survive, in order; the middle stale one is gone.
        assert policy["attributes"]["bindings"][0]["principals"] == [
            "role:role-a-dst",
            "role:role-c-dst",
        ]

    def test_extract_source_ids_unaffected_by_drop_logic(self):
        # extract_source_ids must still surface an id that connect_resources would drop.
        resource = self._make_resource(drop=True)
        binding = {"principals": ["role:role-gone"], "relation": "editor"}

        assert resource.extract_source_ids("principals", binding, "roles") == ["role-gone"]

    def test_non_principal_id_connection_still_hard_fails(self):
        # A dangling dashboard "id" link fails via the generic path regardless of the flag.
        resource = self._make_resource(drop=True)
        self._seed_valid_role(resource)
        # Point at a dashboard id that is NOT in destination state.
        policy = {
            "id": "dashboard:missing-dash",
            "attributes": {"bindings": [{"principals": ["role:role-good"], "relation": "editor"}]},
        }

        with pytest.raises(ResourceConnectionError) as exc_info:
            resource.connect_resources("dashboard:missing-dash", policy)

        assert exc_info.value.empty_binding_risk is False

    def test_inert_when_off_end_to_end_raises_like_today(self):
        # Full connect_resources with the flag off + a would-be-dropped principal must
        # raise exactly as today (no silent drop).
        resource = self._make_resource(drop=False)
        policy = self._policy([{"principals": ["role:role-gone"], "relation": "editor"}])

        with pytest.raises(ResourceConnectionError):
            resource.connect_resources("dashboard:dash-src", policy)

        # Flag off => principal is NOT dropped; it stays in the (rebuilt) list.
        assert policy["attributes"]["bindings"][0]["principals"] == ["role:role-gone"]

    def test_org_and_non_composite_principals_pass_through(self):
        # org: principals (already remapped by the hook) and any non-user/role/team or
        # colon-less token must pass through untouched; an empty-principals binding is skipped.
        resource = self._make_resource(drop=True)
        self._seed_valid_role(resource)
        policy = self._policy(
            [
                {"principals": ["org:dest-org", "weirdnoprefix", "role:role-good"], "relation": "editor"},
                {"principals": [], "relation": "viewer"},
            ]
        )

        resource.connect_resources("dashboard:dash-src", policy)  # no raise

        assert policy["attributes"]["bindings"][0]["principals"] == [
            "org:dest-org",
            "weirdnoprefix",
            "role:role-good-dst",
        ]
        assert policy["attributes"]["bindings"][1]["principals"] == []
