# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for monitors resource handling.

These tests verify that custom check (service check) monitors with missing or
empty options.groupby are skipped at sync time rather than failing with a 400.
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from datadog_sync.model.monitors import Monitors
from datadog_sync.utils.resource_utils import SkipResource


class TestMonitorsPreResourceActionHook:
    """Test suite for monitors pre_resource_action_hook validation."""

    def _make_monitors(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return Monitors(mock_config)

    def test_service_check_missing_groupby_raises_skip(self):
        """Service check monitor with no options.groupby should be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 20438785,
            "type": "service check",
            "name": "Custom check without groupby",
            "options": {},
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(monitors.pre_resource_action_hook("20438785", resource))
        assert (
            "group-by" in str(exc_info.value).lower()
            or "groupby" in str(exc_info.value).lower()
            or "group" in str(exc_info.value).lower()
        )

    def test_service_check_empty_groupby_raises_skip(self):
        """Service check monitor with empty options.groupby list should be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 27474747,
            "type": "service check",
            "name": "Custom check with empty groupby",
            "options": {"groupby": []},
        }
        with pytest.raises(SkipResource):
            asyncio.run(monitors.pre_resource_action_hook("27474747", resource))

    def test_service_check_valid_groupby_does_not_skip(self):
        """Service check monitor with a populated options.groupby should NOT be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 12345,
            "type": "service check",
            "name": "Custom check with valid groupby",
            "options": {"groupby": ["host", "service"]},
        }
        # Should not raise
        asyncio.run(monitors.pre_resource_action_hook("12345", resource))

    def test_non_service_check_monitor_not_affected(self):
        """Non-service-check monitors without groupby should not be skipped."""
        monitors = self._make_monitors()
        for monitor_type in ["metric alert", "event alert", "log alert", "query alert"]:
            resource = {
                "id": 99999,
                "type": monitor_type,
                "name": f"Monitor type {monitor_type}",
                "options": {},
            }
            # Should not raise
            asyncio.run(monitors.pre_resource_action_hook("99999", resource))

    def test_service_check_no_options_key_raises_skip(self):
        """Service check monitor with no 'options' key at all should be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 11111,
            "type": "service check",
            "name": "Custom check no options",
        }
        with pytest.raises(SkipResource):
            asyncio.run(monitors.pre_resource_action_hook("11111", resource))

    def test_service_check_null_groupby_raises_skip(self):
        """Service check monitor with options.groupby=None should be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 33333,
            "type": "service check",
            "name": "Custom check with null groupby",
            "options": {"groupby": None},
        }
        with pytest.raises(SkipResource):
            asyncio.run(monitors.pre_resource_action_hook("33333", resource))

    def test_service_check_null_options_raises_skip(self):
        """Service check monitor with options=None should not crash and should be skipped."""
        monitors = self._make_monitors()
        resource = {
            "id": 44444,
            "type": "service check",
            "name": "Custom check with null options",
            "options": None,
        }
        with pytest.raises(SkipResource):
            asyncio.run(monitors.pre_resource_action_hook("44444", resource))


class TestMonitorsSchemaMigrations:
    """Schema migrations that adapt us1-accepted payloads to us3-required shapes."""

    def _make_monitors(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return Monitors(mock_config)

    def test_custom_schedule_injects_on_missing_data(self):
        """custom_schedule present, on_missing_data absent -> on_missing_data injected as 'default'."""
        monitors = self._make_monitors()
        resource = {
            "id": 140860375,
            "type": "query alert",
            "options": {
                "scheduling_options": {
                    "custom_schedule": {
                        "recurrences": [
                            {
                                "rrule": "FREQ=WEEKLY;INTERVAL=1",
                                "timezone": "Europe/London",
                                "start": "2024-03-06T12:45:00",
                            }
                        ]
                    }
                }
            },
        }
        asyncio.run(monitors.pre_resource_action_hook("140860375", resource))
        assert resource["options"]["on_missing_data"] == "default"

    def test_custom_schedule_preserves_existing_on_missing_data(self):
        """Explicit on_missing_data on source is not overwritten by the injection."""
        monitors = self._make_monitors()
        resource = {
            "id": 140860375,
            "type": "query alert",
            "options": {
                "scheduling_options": {"custom_schedule": {"recurrences": []}},
                "on_missing_data": "show_no_data",
            },
        }
        asyncio.run(monitors.pre_resource_action_hook("140860375", resource))
        assert resource["options"]["on_missing_data"] == "show_no_data"

    def test_custom_schedule_null_on_missing_data_treated_as_absent(self):
        """options.on_missing_data=None should be treated as absent and injected with 'default'."""
        monitors = self._make_monitors()
        resource = {
            "id": 140860376,
            "type": "query alert",
            "options": {
                "scheduling_options": {"custom_schedule": {"recurrences": []}},
                "on_missing_data": None,
            },
        }
        asyncio.run(monitors.pre_resource_action_hook("140860376", resource))
        assert resource["options"]["on_missing_data"] == "default"

    def test_warning_recovery_dropped_when_warning_is_null(self):
        """warning=None is treated as absent; orphan warning_recovery is dropped."""
        monitors = self._make_monitors()
        resource = {
            "id": 64697029,
            "type": "query alert",
            "options": {"thresholds": {"warning": None, "warning_recovery": 80}},
        }
        asyncio.run(monitors.pre_resource_action_hook("64697029", resource))
        assert "warning_recovery" not in resource["options"]["thresholds"]

    def test_warning_recovery_preserved_when_warning_is_zero(self):
        """warning=0 is a valid threshold; warning_recovery must be preserved (even if 0/negative)."""
        monitors = self._make_monitors()
        resource = {
            "id": 64697030,
            "type": "query alert",
            "options": {"thresholds": {"warning": 0, "warning_recovery": -1}},
        }
        asyncio.run(monitors.pre_resource_action_hook("64697030", resource))
        assert resource["options"]["thresholds"]["warning_recovery"] == -1
        assert resource["options"]["thresholds"]["warning"] == 0

    def test_no_custom_schedule_no_on_missing_data_injection(self):
        """Monitors without custom_schedule should not have on_missing_data added."""
        monitors = self._make_monitors()
        resource = {
            "id": 1,
            "type": "query alert",
            "options": {"thresholds": {"critical": 90}},
        }
        asyncio.run(monitors.pre_resource_action_hook("1", resource))
        assert "on_missing_data" not in resource["options"]

    def test_custom_schedule_empty_dict_does_not_trigger_injection(self):
        """scheduling_options.custom_schedule falsy (missing or empty) -> no injection."""
        monitors = self._make_monitors()
        resource = {
            "id": 2,
            "type": "query alert",
            "options": {"scheduling_options": {"custom_schedule": None}},
        }
        asyncio.run(monitors.pre_resource_action_hook("2", resource))
        assert "on_missing_data" not in resource["options"]

    def test_warning_recovery_dropped_when_warning_absent(self):
        """warning_recovery is stripped when there is no warning threshold to recover from."""
        monitors = self._make_monitors()
        resource = {
            "id": 64697028,
            "type": "query alert",
            "options": {"thresholds": {"critical": 90, "warning_recovery": 80}},
        }
        asyncio.run(monitors.pre_resource_action_hook("64697028", resource))
        assert "warning_recovery" not in resource["options"]["thresholds"]
        # unrelated fields untouched
        assert resource["options"]["thresholds"]["critical"] == 90

    def test_warning_recovery_preserved_when_warning_set(self):
        """warning_recovery is kept when warning threshold is set — that's the valid shape."""
        monitors = self._make_monitors()
        resource = {
            "id": 3,
            "type": "query alert",
            "options": {"thresholds": {"critical": 90, "warning": 70, "warning_recovery": 65}},
        }
        asyncio.run(monitors.pre_resource_action_hook("3", resource))
        assert resource["options"]["thresholds"]["warning_recovery"] == 65
        assert resource["options"]["thresholds"]["warning"] == 70

    def test_schema_migrations_tolerate_missing_options(self):
        """Monitors without an options dict should pass through unchanged."""
        monitors = self._make_monitors()
        resource = {"id": 4, "type": "query alert"}
        asyncio.run(monitors.pre_resource_action_hook("4", resource))
        assert "options" not in resource

    def test_schema_migrations_tolerate_null_options(self):
        """options=None must not crash — matches existing null-safety in the hook."""
        monitors = self._make_monitors()
        resource = {"id": 5, "type": "query alert", "options": None}
        asyncio.run(monitors.pre_resource_action_hook("5", resource))
        assert resource["options"] is None

    def test_schema_migrations_tolerate_null_scheduling_options(self):
        """options.scheduling_options=None must not crash the custom_schedule check."""
        monitors = self._make_monitors()
        resource = {"id": 6, "type": "query alert", "options": {"scheduling_options": None}}
        asyncio.run(monitors.pre_resource_action_hook("6", resource))
        assert "on_missing_data" not in resource["options"]

    def test_schema_migrations_tolerate_null_thresholds(self):
        """options.thresholds=None must not crash the warning_recovery drop."""
        monitors = self._make_monitors()
        resource = {"id": 7, "type": "query alert", "options": {"thresholds": None}}
        asyncio.run(monitors.pre_resource_action_hook("7", resource))
        assert resource["options"]["thresholds"] is None


class TestMonitorsRestrictionPolicyPrincipals:
    """Test suite for restriction_policy principal remapping in monitors."""

    def _make_monitors(self, destination_state=None):
        from collections import defaultdict

        mock_config = MagicMock()
        mock_config.state = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        monitors = Monitors(mock_config)
        return monitors

    # --- connect_id tests ---

    def test_connect_id_remaps_user_principal(self):
        """user: principal is remapped when user exists in destination state."""
        state = {
            "users": {"src-user-id": {"id": "dst-user-id"}},
            "roles": {},
            "teams": {},
        }
        monitors = self._make_monitors(state)
        binding = {"principals": ["user:src-user-id", "role:src-role-id"], "relation": "editor"}
        result = monitors.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "user:dst-user-id"
        assert binding["principals"][1] == "role:src-role-id"
        assert result == []

    def test_connect_id_remaps_role_principal(self):
        """role: principal is remapped when role exists in destination state."""
        state = {
            "users": {},
            "roles": {"src-role-id": {"id": "dst-role-id"}},
            "teams": {},
        }
        monitors = self._make_monitors(state)
        binding = {"principals": ["role:src-role-id"], "relation": "viewer"}
        result = monitors.connect_id("principals", binding, "roles")
        assert binding["principals"][0] == "role:dst-role-id"
        assert result == []

    def test_connect_id_remaps_team_principal(self):
        """team: principal is remapped when team exists in destination state."""
        state = {
            "users": {},
            "roles": {},
            "teams": {"src-team-id": {"id": "dst-team-id"}},
        }
        monitors = self._make_monitors(state)
        binding = {"principals": ["team:src-team-id"], "relation": "editor"}
        result = monitors.connect_id("principals", binding, "teams")
        assert binding["principals"][0] == "team:dst-team-id"
        assert result == []

    def test_connect_id_missing_user_returns_failed(self):
        """user: principal not in destination state is added to failed_connections."""
        state = {"users": {}, "roles": {}, "teams": {}}
        monitors = self._make_monitors(state)
        binding = {"principals": ["user:missing-user-id"], "relation": "editor"}
        result = monitors.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "user:missing-user-id"
        assert result == ["missing-user-id"]

    def test_connect_id_skips_non_matching_type(self):
        """user: principal is not modified when resource_to_connect is roles."""
        state = {"users": {}, "roles": {}, "teams": {}}
        monitors = self._make_monitors(state)
        binding = {"principals": ["user:some-user-id"], "relation": "editor"}
        result = monitors.connect_id("principals", binding, "roles")
        assert binding["principals"][0] == "user:some-user-id"
        assert result == []

    def test_connect_id_org_principal_passes_through_silently(self):
        """org: principal is not modified and not added to failed_connections."""
        state = {"users": {}, "roles": {}, "teams": {}}
        monitors = self._make_monitors(state)
        binding = {"principals": ["org:some-org-id"], "relation": "editor"}
        result = monitors.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "org:some-org-id"
        assert result == []

    # --- pre_resource_action_hook tests ---

    def test_pre_resource_action_hook_replaces_org_principal(self):
        """org: principal in restriction_policy bindings is replaced when org_principal is set."""
        monitors = self._make_monitors()
        monitors.org_principal = "org:dest-pub-id"
        resource = {
            "type": "metric alert",
            "restriction_policy": {
                "bindings": [{"principals": ["org:src-pub-id", "user:some-user"], "relation": "editor"}]
            },
        }
        asyncio.run(monitors.pre_resource_action_hook("12345", resource))
        assert resource["restriction_policy"]["bindings"][0]["principals"][0] == "org:dest-pub-id"
        assert resource["restriction_policy"]["bindings"][0]["principals"][1] == "user:some-user"

    def test_pre_resource_action_hook_skips_org_when_no_org_principal(self):
        """org: principal is left unchanged when org_principal is None."""
        monitors = self._make_monitors()
        assert monitors.org_principal is None
        resource = {
            "type": "metric alert",
            "restriction_policy": {"bindings": [{"principals": ["org:src-pub-id"], "relation": "editor"}]},
        }
        asyncio.run(monitors.pre_resource_action_hook("12345", resource))
        assert resource["restriction_policy"]["bindings"][0]["principals"][0] == "org:src-pub-id"

    # --- pre_apply_hook tests ---

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Successful GET /api/v2/current_user sets org_principal to 'org:{org_uuid}'."""
        from unittest.mock import AsyncMock

        monitors = self._make_monitors()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={"data": {"relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}}}}
        )
        monitors.config.destination_client = mock_client
        asyncio.run(monitors.pre_apply_hook())
        assert monitors.org_principal == "org:00000000-0000-beef-0000-000000000000"

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Failed GET /api/v2/current_user leaves org_principal as None and raises."""
        from unittest.mock import AsyncMock

        monitors = self._make_monitors()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        monitors.config.destination_client = mock_client
        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(monitors.pre_apply_hook())
        assert monitors.org_principal is None
