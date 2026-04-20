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
        assert "group-by" in str(exc_info.value).lower() or "groupby" in str(exc_info.value).lower() or "group" in str(exc_info.value).lower()

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
