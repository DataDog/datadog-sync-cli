# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for restriction_policies resource handling.

These tests verify that the org: principal remapping in pre_apply_hook and
pre_resource_action_hook handles both success and failure paths correctly,
and that the deterministic skip filter (read-only dashboard targets, and
policies that would self-demote the syncing user) raises SkipResource before
the API call is issued.
"""

import asyncio
import logging
from collections import defaultdict

import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.restriction_policies import RestrictionPolicies
from datadog_sync.utils.resource_utils import SkipResource


def _make_resource(allow_self_lockout: bool = False):
    """Build a RestrictionPolicies instance with a lightweight mock config.

    Mirrors the unit-test pattern from tests/unit/conftest.py::mock_config —
    state.source / state.destination are defaultdicts of dicts so resource
    types can be indexed without explicit pre-population.
    """
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.state.source = defaultdict(dict)
    mock_config.state.destination = defaultdict(dict)
    mock_config.allow_self_lockout = allow_self_lockout
    return RestrictionPolicies(mock_config)


class TestRestrictionPoliciesOrgPrincipal:
    """Test suite for org: principal remapping in restriction_policies."""

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Successful GET /api/v2/current_user sets org_principal to 'org:{org_uuid}'."""
        resource = _make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={
                "data": {
                    "id": "17d29c8a-6285-11f0-be9b-76de006a05ea",
                    "relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}},
                }
            }
        )
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal == "org:00000000-0000-beef-0000-000000000000"

    def test_pre_apply_hook_captures_current_user_uuid(self):
        """pre_apply_hook also captures the syncing user's UUID for self-demote checks."""
        resource = _make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={
                "data": {
                    "id": "17d29c8a-6285-11f0-be9b-76de006a05ea",
                    "relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}},
                }
            }
        )
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.current_user_uuid == "17d29c8a-6285-11f0-be9b-76de006a05ea"

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Failed GET /api/v2/current_user leaves org_principal as None and raises."""
        resource = _make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        resource.config.destination_client = mock_client

        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None
        assert resource.current_user_uuid is None

    def test_pre_resource_action_hook_replaces_org_when_principal_set(self):
        """When org_principal is set, org: entries in bindings are replaced."""
        resource = _make_resource()
        resource.org_principal = "org:dest-pub-id"

        r = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]},
        }
        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:dest-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"

    def test_pre_resource_action_hook_skips_when_org_principal_none(self):
        """When org_principal is None, org: principals are left unchanged."""
        resource = _make_resource()
        assert resource.org_principal is None

        r = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]},
        }
        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:source-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"


class TestRestrictionPoliciesSkipFilter:
    """Test suite for the deterministic skip filter in pre_resource_action_hook.

    Two failure modes are skipped at the sync-cli level rather than forwarded
    to the API as guaranteed-failing requests:

      1. Target dashboard is read-only on the destination (template / built-in /
         shared) — the API rejects restriction-policy attachment with 403.
      2. The policy would remove the syncing service account's own ``editor``
         binding, demoting itself to ``viewer`` or no access — the API rejects
         with 400 "users cannot decrease their own level of access".
    """

    # ------------------------------------------------------------------
    # Read-only dashboard skip
    # ------------------------------------------------------------------
    def test_skips_when_target_dashboard_is_read_only(self):
        resource = _make_resource()
        # Destination dashboard state keyed by source dashboard id.
        # ``is_read_only`` is the actual Datadog dashboards API field
        # (see datadog_sync/model/dashboards.py — it appears in excluded_attributes).
        resource.config.state.destination["dashboards"]["src-dash"] = {
            "id": "dst-dash-uuid",
            "is_read_only": True,
        }
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

        assert "read-only" in str(exc_info.value).lower()
        assert "dashboard:src-dash" in str(exc_info.value)

    def test_does_not_skip_when_target_dashboard_is_writable(self):
        resource = _make_resource()
        resource.config.state.destination["dashboards"]["src-dash"] = {
            "id": "dst-dash-uuid",
            "is_read_only": False,
        }
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        # Should not raise — writable dashboards pass through.
        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_when_target_dashboard_missing_read_only_attr(self):
        """Absent ``is_read_only`` (missing key) is treated as not-read-only — pass through.

        Treating missing-as-writable is intentional: the API is the source of
        truth for read-only state, and a missing field would result in the API
        returning a 403 (which is exactly the noisy event we want to skip) only
        when the dashboard IS read-only. If the field is missing for any reason
        we let the request go through rather than mask a writable dashboard.
        """
        resource = _make_resource()
        resource.config.state.destination["dashboards"]["src-dash"] = {"id": "dst-dash-uuid"}
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_non_dashboard_targets(self):
        """SLO and notebook policy targets are not subject to the read-only check.

        Only dashboards have the ``is_read_only`` flag; the prod 403 noise comes
        from built-in/template dashboards specifically. Other target types
        pass through this branch unconditionally.
        """
        resource = _make_resource()
        policy = {
            "id": "slo:src-slo",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        # Should not raise — non-dashboard targets are not gated.
        asyncio.run(resource.pre_resource_action_hook("slo:src-slo", policy))

    def test_does_not_skip_when_target_dashboard_not_in_destination_state(self):
        """Dashboard absent from destination state — the connect_id path will
        report missing-connections and the resource will be skipped via the
        ResourceConnectionError path instead. The read-only branch must not
        trip on missing entries.
        """
        resource = _make_resource()
        # Empty destination dashboards mapping — dashboard not (yet) created.
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        # Should not raise SkipResource — must fall through cleanly so the
        # downstream missing-connections cascade handles it.
        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    # ------------------------------------------------------------------
    # Self-demote skip
    # ------------------------------------------------------------------
    def test_skips_when_policy_would_self_demote_syncing_user(self):
        """Syncing user has editor on destination but the new policy gives them
        only viewer (different relation) — the API rejects with 400; pre-filter."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    # Syncing user moved to viewer — would self-demote.
                    {"relation": "viewer", "principals": [f"user:{sa_uuid}"]},
                ]
            },
        }

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

        assert "self-demote" in str(exc_info.value).lower()
        assert sa_uuid in str(exc_info.value)

    def test_skips_when_policy_omits_syncing_user_from_editor(self):
        """Syncing user not listed in any binding at all — they would lose
        their editor binding (effectively self-demote to viewer). API rejects."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "editor", "principals": ["user:other-user"]},
                ]
            },
        }

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

        assert "self-demote" in str(exc_info.value).lower()

    def test_does_not_skip_when_syncing_user_keeps_editor(self):
        """Syncing user listed under the editor binding — no self-demote."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "editor", "principals": [f"user:{sa_uuid}", "user:other"]},
                ]
            },
        }

        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_self_demote_when_allow_self_lockout_is_true(self):
        """When operator explicitly opts in via --allow-self-lockout, the
        sync-cli sends the request with ?allow_self_lockout=true; the API
        accepts it. We must not pre-filter in that case — the operator is
        intentionally taking the action."""
        resource = _make_resource(allow_self_lockout=True)
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "viewer", "principals": [f"user:{sa_uuid}"]},
                ]
            },
        }

        # Should NOT raise — allow_self_lockout means the operator opted in.
        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_self_demote_when_current_user_uuid_unknown(self):
        """If pre_apply_hook didn't capture the uuid (e.g. legacy in-process
        state with org_principal pre-set), don't second-guess — let the API
        return the existing error rather than mistakenly skip valid policies."""
        resource = _make_resource()
        resource.current_user_uuid = None
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "viewer", "principals": ["user:someone-else"]},
                ]
            },
        }

        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_when_policy_has_no_bindings(self):
        """Empty bindings clears all restrictions — the syncing user retains
        whatever org-default access they have. Not a self-demote case for the
        purposes of this filter."""
        resource = _make_resource()
        resource.current_user_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": []},
        }

        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    def test_does_not_skip_when_only_org_team_role_editor_bindings_present(self):
        """Editor bindings are entirely org:/team:/role: principals — no
        direct user:<X> editor binding exists, so we cannot infer self-demote
        from the payload alone. The SA's effective access may come from a
        membership; let the API decide. Matches the integration-fixture
        case where policies grant editor to org/team principals only."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {
                        "relation": "editor",
                        "principals": [
                            "org:30187db5-8582-11ef-969b-8248c7cda362",
                            "team:d19a4fc2-aeda-4b9e-856a-b9e48c0e19fa",
                            "role:f0cc21b6-6f38-49f0-8641-e25fb3b98476",
                        ],
                    },
                ]
            },
        }

        asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

    # ------------------------------------------------------------------
    # Integration: hook prevents the API call
    # ------------------------------------------------------------------
    def test_passes_through_when_target_is_writable_and_no_self_demote(self):
        """Normal case: writable dashboard, syncing user retains editor.
        Hook returns normally; downstream code calls the API."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        resource.config.state.destination["dashboards"]["src-dash"] = {
            "id": "dst-dash-uuid",
            "is_read_only": False,
        }
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "editor", "principals": [f"user:{sa_uuid}", "user:other"]},
                ]
            },
        }

        # Returns None (no skip) — proceed to API call.
        result = asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))
        assert result is None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def test_logs_include_target_id_and_reason_for_read_only_skip(self, caplog):
        """Log line for the read-only skip must include the policy id and a
        machine-parsable reason marker so operators can dashboards/alert on it."""
        resource = _make_resource()
        # Route the config logger to caplog's standard-library logger so the
        # message text is captured regardless of how the MagicMock-wrapped
        # logger forwards calls.
        real_logger = logging.getLogger("datadog_sync.test.restriction_policies.readonly")
        resource.config.logger = real_logger
        resource.config.state.destination["dashboards"]["src-dash"] = {
            "id": "dst-dash-uuid",
            "is_read_only": True,
        }
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {"bindings": [{"relation": "editor", "principals": ["user:some-user"]}]},
        }

        with caplog.at_level(logging.INFO, logger=real_logger.name):
            with pytest.raises(SkipResource):
                asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "dashboard:src-dash" in joined
        assert "read-only" in joined.lower()
        assert "restriction_policies" in joined or "restriction policies" in joined.lower()

    def test_logs_include_target_id_and_reason_for_self_demote_skip(self, caplog):
        """Log line for the self-demote skip must include the policy id and
        the syncing user uuid so operators can correlate with the API error."""
        resource = _make_resource()
        sa_uuid = "17d29c8a-6285-11f0-be9b-76de006a05ea"
        resource.current_user_uuid = sa_uuid
        real_logger = logging.getLogger("datadog_sync.test.restriction_policies.selfdemote")
        resource.config.logger = real_logger
        policy = {
            "id": "dashboard:src-dash",
            "attributes": {
                "bindings": [
                    {"relation": "viewer", "principals": [f"user:{sa_uuid}"]},
                ]
            },
        }

        with caplog.at_level(logging.INFO, logger=real_logger.name):
            with pytest.raises(SkipResource):
                asyncio.run(resource.pre_resource_action_hook("dashboard:src-dash", policy))

        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "dashboard:src-dash" in joined
        assert sa_uuid in joined
        assert "self-demote" in joined.lower()
