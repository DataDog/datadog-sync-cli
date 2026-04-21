# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for service_level_objectives resource handling.

These tests verify that metric-based SLOs with queries missing the .as_count()
modifier are skipped at sync time rather than failing with a 400.

Note: The Datadog SLO API uses "query" (singular dict with "numerator"/"denominator")
for metric-based SLOs, NOT "queries" (list). This matches the cassette data at
tests/integration/resources/cassettes/test_service_level_objectives/.
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from datadog_sync.model.service_level_objectives import ServiceLevelObjectives
from datadog_sync.utils.resource_utils import SkipResource


class TestSLOPreResourceActionHook:
    """Test suite for SLO pre_resource_action_hook validation."""

    def _make_slos(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return ServiceLevelObjectives(mock_config)

    def test_metric_slo_missing_as_count_in_numerator_raises_skip(self):
        """Metric SLO with numerator missing .as_count() should be skipped."""
        slos = self._make_slos()
        resource = {
            "id": "9d6ac152e1ce5fbf861798ebcac1ac47",
            "type": "metric",
            "query": {"numerator": "sum:custom.metric{*}", "denominator": "sum:custom.total{*}.as_count()"},
        }
        with pytest.raises(SkipResource):
            asyncio.run(slos.pre_resource_action_hook("9d6ac152e1ce5fbf861798ebcac1ac47", resource))

    def test_metric_slo_missing_as_count_in_denominator_raises_skip(self):
        """Metric SLO with denominator missing .as_count() should be skipped."""
        slos = self._make_slos()
        resource = {
            "id": "abc123",
            "type": "metric",
            "query": {"numerator": "sum:custom.metric{*}.as_count()", "denominator": "sum:custom.total{*}"},
        }
        with pytest.raises(SkipResource):
            asyncio.run(slos.pre_resource_action_hook("abc123", resource))

    def test_metric_slo_valid_queries_does_not_skip(self):
        """Metric SLO with both numerator and denominator using .as_count() should NOT be skipped."""
        slos = self._make_slos()
        resource = {
            "id": "valid123",
            "type": "metric",
            "query": {
                "numerator": "sum:custom.metric{*}.as_count()",
                "denominator": "sum:custom.total{*}.as_count()",
            },
        }
        # Should not raise
        asyncio.run(slos.pre_resource_action_hook("valid123", resource))

    def test_monitor_slo_not_affected(self):
        """Monitor-based SLO should not be checked for .as_count()."""
        slos = self._make_slos()
        resource = {
            "id": "monitor456",
            "type": "monitor",
            "monitor_ids": [12345],
        }
        # Should not raise
        asyncio.run(slos.pre_resource_action_hook("monitor456", resource))

    def test_time_slice_slo_not_affected(self):
        """Time-slice SLO should not be checked for .as_count()."""
        slos = self._make_slos()
        resource = {
            "id": "timeslice789",
            "type": "time_slice",
            "sli_specification": {},
        }
        # Should not raise
        asyncio.run(slos.pre_resource_action_hook("timeslice789", resource))

    def test_metric_slo_empty_query_string_does_not_skip(self):
        """Empty query string should not trigger a skip (guard against false positives)."""
        slos = self._make_slos()
        resource = {
            "id": "empty789",
            "type": "metric",
            "query": {"numerator": "", "denominator": ""},
        }
        # Empty strings are falsy — no skip triggered
        asyncio.run(slos.pre_resource_action_hook("empty789", resource))

    def test_metric_slo_no_query_does_not_crash(self):
        """Metric SLO with no 'query' key should not crash."""
        slos = self._make_slos()
        resource = {
            "id": "noqueries",
            "type": "metric",
        }
        # Should not raise
        asyncio.run(slos.pre_resource_action_hook("noqueries", resource))

    def test_metric_slo_null_query_does_not_crash(self):
        """Metric SLO with query=None should not crash (guard against AttributeError)."""
        slos = self._make_slos()
        resource = {
            "id": "nullquery",
            "type": "metric",
            "query": None,
        }
        # Should not raise (isinstance guard handles None)
        asyncio.run(slos.pre_resource_action_hook("nullquery", resource))

    def test_missing_type_key_does_not_skip(self):
        """Resource without a 'type' key should not trigger the check."""
        slos = self._make_slos()
        resource = {"id": "notype"}
        # Should not raise
        asyncio.run(slos.pre_resource_action_hook("notype", resource))

    def test_skip_message_mentions_field(self):
        """SkipResource message should identify which field is missing the modifier."""
        slos = self._make_slos()
        resource = {
            "id": "msgtest",
            "type": "metric",
            "query": {"numerator": "sum:bad.metric{*}", "denominator": "sum:ok.total{*}.as_count()"},
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(slos.pre_resource_action_hook("msgtest", resource))
        assert "numerator" in str(exc_info.value)
