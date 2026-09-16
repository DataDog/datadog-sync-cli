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
from collections import defaultdict
from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.service_level_objectives import ServiceLevelObjectives, _extract_metric_names
from datadog_sync.utils.resource_utils import (
    FAILURE_CLASS_DESTINATION_METRIC_MISSING,
    CustomClientHTTPError,
    ResourceConnectionError,
    SkipResource,
)


def _http_error(status, message="err"):
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


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


class TestSLOConnectResources:
    def _make_slos(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.state.source = defaultdict(dict)
        mock_config.state.destination = defaultdict(dict)
        mock_config.state.ensure_resource_loaded = MagicMock()
        mock_config.state.ensure_resource_type_loaded = MagicMock()
        mock_config.skip_failed_resource_connections = False
        mock_config.logger = MagicMock()
        return ServiceLevelObjectives(mock_config)

    def test_stale_monitor_dependency_raises_typed_skip(self):
        slos = self._make_slos()
        resource = {"id": "slo-src", "type": "monitor", "monitor_ids": [123]}

        with pytest.raises(SkipResource) as exc_info:
            slos.connect_resources("slo-src", resource)

        assert exc_info.value.failure_class == "stale_dependency"
        assert exc_info.value.outcome_reason == "stale_dependency"
        assert exc_info.value.outcome_details == {"monitors": "123"}
        slos.config.state.ensure_resource_loaded.assert_any_call("monitors", "123")

    def test_source_present_monitor_dependency_still_hard_fails(self):
        slos = self._make_slos()
        slos.config.state.source["monitors"]["123"] = {"id": 123}
        resource = {"id": "slo-src", "type": "monitor", "monitor_ids": [123]}

        with pytest.raises(ResourceConnectionError):
            slos.connect_resources("slo-src", resource)

        assert resource["monitor_ids"] == [123]

    def test_destination_present_monitor_dependency_is_mapped(self):
        slos = self._make_slos()
        slos.config.state.destination["monitors"]["123"] = {"id": 456}
        resource = {"id": "slo-src", "type": "monitor", "monitor_ids": [123]}

        slos.connect_resources("slo-src", resource)

        assert resource["monitor_ids"] == [456]

    def test_destination_synthetics_monitor_dependency_is_mapped(self):
        slos = self._make_slos()
        slos.config.state.destination["synthetics_tests"]["abc-def#123"] = {"monitor_id": 456}
        resource = {"id": "slo-src", "type": "monitor", "monitor_ids": [123]}

        slos.connect_resources("slo-src", resource)

        assert resource["monitor_ids"] == [456]


class TestSLOMetricProbe:
    """Tests for destination-metric pre-probe on metric SLO create/update.

    A metric SLO's numerator/denominator queries reference metrics that must
    exist on the destination. When one or more are missing, sync-cli raises a
    typed SkipResource (failure_class=destination_metric_missing) carrying all
    missing names so callers can materialize them before retrying.
    """

    def _make_slos(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.destination_client = AsyncMock()
        return ServiceLevelObjectives(mock_config)

    def _metric_slo(self, slo_id="slo-test", numerator=None, denominator=None):
        return {
            "id": slo_id,
            "type": "metric",
            "query": {
                "numerator": numerator or "sum:metric.src.hits{env:staging1}.as_count()",
                "denominator": denominator or "sum:metric.src.total{env:staging1}.as_count()",
            },
        }

    def test_create_missing_metric_raises_typed_skip(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock(side_effect=_http_error(404))
        client.post = AsyncMock()
        resource = self._metric_slo()

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(slos.create_resource("slo-test", resource))

        assert exc_info.value.failure_class == FAILURE_CLASS_DESTINATION_METRIC_MISSING
        assert exc_info.value.outcome_reason == FAILURE_CLASS_DESTINATION_METRIC_MISSING
        assert exc_info.value.outcome_details["operation"] == "slo_create"
        assert exc_info.value.outcome_details["metric_names"] == "metric.src.hits,metric.src.total"
        client.post.assert_not_awaited()

    def test_create_all_metrics_present_proceeds_to_post(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock(return_value={"type": "count"})
        client.post = AsyncMock(return_value={"data": [{"id": "dst-slo"}]})
        resource = self._metric_slo()

        _id, resp = asyncio.run(slos.create_resource("slo-test", resource))

        assert _id == "slo-test"
        assert resp == {"id": "dst-slo"}
        assert client.get.await_count == 2
        client.post.assert_awaited_once()

    def test_create_some_metrics_missing_lists_all_missing(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        # first metric exists, second 404s
        client.get = AsyncMock(side_effect=[{"type": "count"}, _http_error(404)])
        client.post = AsyncMock()
        resource = self._metric_slo(
            numerator="sum:metric.a{env:x}.as_count() - sum:metric.b{env:x}.as_count()",
            denominator="sum:metric.a{env:x}.as_count()",
        )

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(slos.create_resource("slo-test", resource))

        assert exc_info.value.outcome_details["metric_names"] == "metric.b"
        client.post.assert_not_awaited()

    def test_create_multi_metric_slo_all_missing_lists_both(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock(side_effect=_http_error(404))
        client.post = AsyncMock()
        resource = self._metric_slo(
            numerator=(
                "sum:trace.servlet.request.hits{env:s}.as_count() - "
                "sum:trace.servlet.request.errors{env:s}.as_count()"
            ),
            denominator="sum:trace.servlet.request.hits{env:s}.as_count()",
        )

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(slos.create_resource("slo-test", resource))

        names = exc_info.value.outcome_details["metric_names"].split(",")
        assert set(names) == {"trace.servlet.request.hits", "trace.servlet.request.errors"}
        client.post.assert_not_awaited()

    def test_update_missing_metric_raises_typed_skip(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock(side_effect=_http_error(404))
        client.put = AsyncMock()
        slos.config.state.destination = {"service_level_objectives": {"slo-test": {"id": "dst-slo"}}}
        resource = self._metric_slo()

        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(slos.update_resource("slo-test", resource))

        assert exc_info.value.failure_class == FAILURE_CLASS_DESTINATION_METRIC_MISSING
        assert exc_info.value.outcome_details["operation"] == "slo_update"
        client.put.assert_not_awaited()

    def test_create_monitor_slo_skips_probe(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock()
        client.post = AsyncMock(return_value={"data": [{"id": "dst-slo"}]})
        resource = {"id": "mon-slo", "type": "monitor", "monitor_ids": [123]}

        _id, _ = asyncio.run(slos.create_resource("mon-slo", resource))

        assert _id == "mon-slo"
        client.get.assert_not_awaited()
        client.post.assert_awaited_once()

    def test_probe_get_500_propagates(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock(side_effect=_http_error(500, "Internal Server Error"))
        client.post = AsyncMock()
        resource = self._metric_slo()

        with pytest.raises(CustomClientHTTPError) as exc_info:
            asyncio.run(slos.create_resource("slo-test", resource))

        assert exc_info.value.status_code == 500
        client.post.assert_not_awaited()

    def test_create_metric_slo_no_query_does_not_probe(self):
        slos = self._make_slos()
        client = slos.config.destination_client
        client.get = AsyncMock()
        client.post = AsyncMock(return_value={"data": [{"id": "dst-slo"}]})
        resource = {"id": "noquery", "type": "metric"}

        asyncio.run(slos.create_resource("noquery", resource))

        client.get.assert_not_awaited()
        client.post.assert_awaited_once()


class TestExtractMetricNames:
    """Unit tests for _extract_metric_names query parsing."""

    def test_simple_query(self):
        resource = {
            "type": "metric",
            "query": {
                "numerator": "sum:custom.metric{env:x}.as_count()",
                "denominator": "sum:custom.total{env:x}.as_count()",
            },
        }
        assert set(_extract_metric_names(resource)) == {"custom.metric", "custom.total"}

    def test_arithmetic_numerator(self):
        resource = {
            "type": "metric",
            "query": {
                "numerator": "sum:trace.servlet.request.hits{env:s} - sum:trace.servlet.request.errors{env:s}",
                "denominator": "sum:trace.servlet.request.hits{env:s}",
            },
        }
        assert set(_extract_metric_names(resource)) == {
            "trace.servlet.request.hits",
            "trace.servlet.request.errors",
        }

    def test_dedupes_across_numerator_denominator(self):
        resource = {
            "type": "metric",
            "query": {
                "numerator": "sum:same.metric{env:x}.as_count()",
                "denominator": "sum:same.metric{env:x}.as_count()",
            },
        }
        assert _extract_metric_names(resource) == ["same.metric"]

    def test_sli_specification_count_queries(self):
        resource = {
            "type": "metric",
            "sli_specification": {
                "count": {
                    "queries": [
                        {
                            "data_source": "metrics",
                            "name": "query1",
                            "query": "sum:gateway.request.status_code{env:p}",
                        },
                        {
                            "data_source": "metrics",
                            "name": "query2",
                            "query": "sum:gateway.request.status_code{env:p,http_status:5*}",
                        },
                    ]
                }
            },
        }
        assert _extract_metric_names(resource) == ["gateway.request.status_code"]

    def test_both_query_and_sli_spec_deduped(self):
        resource = {
            "type": "metric",
            "query": {"numerator": "sum:shared.metric{env:x}", "denominator": "sum:other.metric{env:x}"},
            "sli_specification": {"count": {"queries": [{"query": "sum:shared.metric{env:x}"}]}},
        }
        assert set(_extract_metric_names(resource)) == {"shared.metric", "other.metric"}

    def test_no_metric_names_for_monitor_slo(self):
        resource = {"type": "monitor", "monitor_ids": [123]}
        assert _extract_metric_names(resource) == []

    def test_empty_query_strings(self):
        resource = {"type": "metric", "query": {"numerator": "", "denominator": ""}}
        assert _extract_metric_names(resource) == []

    def test_missing_query_key(self):
        resource = {"type": "metric"}
        assert _extract_metric_names(resource) == []
