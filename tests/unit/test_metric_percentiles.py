# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from datadog_sync.model.metric_percentiles import (
    FAILURE_CLASS_DESTINATION_METRIC_NOT_CONFIGURABLE,
    MetricPercentiles,
)
from datadog_sync.utils.resource_utils import (
    FAILURE_CLASS_DESTINATION_METRIC_MISSING,
    CustomClientHTTPError,
    SkipResource,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _http_error(status: int, message: str = "err") -> CustomClientHTTPError:
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


@pytest.fixture
def metric_percentiles(mock_config):
    mock_config.destination_client = AsyncMock()
    return MetricPercentiles(mock_config)


def _metric_data(*names: str):
    return [{"id": name, "type": "metric"} for name in names]


def test_get_resources_two_calls_merge_enabled_and_disabled(metric_percentiles):
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            {"data": _metric_data("enabled.metric.one", "enabled.metric.two"), "meta": {"pagination": {}}},
            {"data": _metric_data("disabled.metric.one"), "meta": {"pagination": {}}},
        ]
    )

    resources = _run(metric_percentiles.get_resources(client))

    assert client.get.await_count == 2
    enabled_call, disabled_call = client.get.await_args_list
    assert enabled_call.args[0] == "/api/v2/metrics"
    assert enabled_call.kwargs["params"]["filter[include_percentiles]"] == "true"
    assert enabled_call.kwargs["params"]["filter[metric_type]"] == "distribution"
    assert enabled_call.kwargs["params"]["window[seconds]"] == 14 * 86400
    assert "page[cursor]" not in enabled_call.kwargs["params"]
    assert disabled_call.kwargs["params"]["filter[include_percentiles]"] == "false"

    assert sorted(resources, key=lambda r: r["metric_name"]) == [
        {"metric_name": "disabled.metric.one", "include_percentiles": False},
        {"metric_name": "enabled.metric.one", "include_percentiles": True},
        {"metric_name": "enabled.metric.two", "include_percentiles": True},
    ]


def test_get_resources_follows_next_cursor(metric_percentiles):
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            {"data": _metric_data("enabled.page.one"), "meta": {"pagination": {"next_cursor": "cursor-1"}}},
            {"data": _metric_data("enabled.page.two"), "meta": {"pagination": {}}},
            {"data": _metric_data("disabled.page.one"), "meta": {"pagination": {}}},
        ]
    )

    resources = _run(metric_percentiles.get_resources(client))

    assert client.get.await_count == 3
    _, second_call, _ = client.get.await_args_list
    assert second_call.kwargs["params"]["page[cursor]"] == "cursor-1"
    assert {r["metric_name"] for r in resources} == {
        "enabled.page.one",
        "enabled.page.two",
        "disabled.page.one",
    }


def test_get_resources_metric_reported_only_once_per_boolean(metric_percentiles):
    # A metric can only be reported by one of the two calls (include_percentiles is
    # a single boolean field), but guard against the merge silently deduplicating
    # across a boundary in a way that would hide a real destination inconsistency.
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            {"data": _metric_data("only.metric"), "meta": {"pagination": {}}},
            {"data": [], "meta": {"pagination": {}}},
        ]
    )

    resources = _run(metric_percentiles.get_resources(client))

    assert resources == [{"metric_name": "only.metric", "include_percentiles": True}]


def test_update_resource_existing_metric_enables_percentiles(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(return_value={})

    _id, resource = _run(
        metric_percentiles.update_resource("custom.metric", {"metric": "custom.metric", "include_percentiles": True})
    )

    assert _id == "custom.metric"
    assert resource == {"metric": "custom.metric", "include_percentiles": True}
    client.get.assert_not_awaited()
    client.patch.assert_awaited_once_with(
        "/metric/distribution/summary_aggr/percentiles/enable",
        {"metric_names": ["custom.metric"]},
    )


def test_update_resource_existing_metric_disables_percentiles(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(return_value={})

    _run(metric_percentiles.update_resource("custom.metric", {"metric": "custom.metric", "include_percentiles": False}))

    client.get.assert_not_awaited()
    client.patch.assert_awaited_once_with(
        "/metric/distribution/summary_aggr/percentiles/disable",
        {"metric_names": ["custom.metric"]},
    )


def test_update_resource_missing_destination_metric_patch_raises_skip(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(side_effect=_http_error(404, '{"errors":["custom.metric not found"]}'))

    with pytest.raises(SkipResource) as exc_info:
        _run(
            metric_percentiles.update_resource(
                "custom.metric",
                {"metric": "custom.metric", "include_percentiles": True},
            )
        )

    assert "custom.metric" in str(exc_info.value)
    assert "not present on destination" in str(exc_info.value)
    assert exc_info.value.failure_class == FAILURE_CLASS_DESTINATION_METRIC_MISSING
    assert exc_info.value.outcome_details == {
        "metric_name": "custom.metric",
        "operation": "percentiles_enable",
    }
    client.get.assert_not_awaited()
    client.patch.assert_awaited_once()


def test_update_resource_metric_not_found_patch_raises_skip(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(side_effect=_http_error(500, '{"detail":"metric not found"}'))

    with pytest.raises(SkipResource) as exc_info:
        _run(
            metric_percentiles.update_resource(
                "custom.metric",
                {"metric": "custom.metric", "include_percentiles": True},
            )
        )

    assert "custom.metric" in str(exc_info.value)
    assert "not present on destination" in str(exc_info.value)
    assert exc_info.value.failure_class == FAILURE_CLASS_DESTINATION_METRIC_MISSING
    assert exc_info.value.outcome_details == {
        "metric_name": "custom.metric",
        "operation": "percentiles_enable",
    }
    client.get.assert_not_awaited()
    client.patch.assert_awaited_once()


def test_update_resource_destination_reports_unsuccessful_raises_skip(metric_percentiles):
    # The bulk toggle endpoint returns 200 even when it declines to touch a metric
    # (e.g. its summary_aggr source isn't percentile-configurable) - it reports the
    # rejection in "unsuccessful" instead of raising. A no-op like that must not be
    # reported as a successful sync.
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(return_value={"updated": [], "unsuccessful": ["custom.metric"]})

    with pytest.raises(SkipResource) as exc_info:
        _run(
            metric_percentiles.update_resource(
                "custom.metric",
                {"metric": "custom.metric", "include_percentiles": True},
            )
        )

    assert "custom.metric" in str(exc_info.value)
    assert exc_info.value.failure_class == FAILURE_CLASS_DESTINATION_METRIC_NOT_CONFIGURABLE
    assert exc_info.value.outcome_details == {
        "metric_name": "custom.metric",
        "operation": "percentiles_enable",
    }
    client.patch.assert_awaited_once()


def test_update_resource_destination_reports_other_metric_unsuccessful_does_not_raise(metric_percentiles):
    # Only this resource's own id in "unsuccessful" should trigger a skip.
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(return_value={"updated": ["custom.metric"], "unsuccessful": ["other.metric"]})

    _id, resource = _run(
        metric_percentiles.update_resource(
            "custom.metric",
            {"metric": "custom.metric", "include_percentiles": True},
        )
    )

    assert _id == "custom.metric"
    assert resource == {"metric": "custom.metric", "include_percentiles": True}


def test_update_resource_non_metric_not_found_400_patch_error_propagates(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(side_effect=_http_error(400, "Bad Request"))

    with pytest.raises(CustomClientHTTPError) as exc_info:
        _run(
            metric_percentiles.update_resource(
                "custom.metric",
                {"metric": "custom.metric", "include_percentiles": True},
            )
        )

    assert exc_info.value.status_code == 400
    client.get.assert_not_awaited()


def test_update_resource_non_metric_not_found_patch_error_propagates(metric_percentiles):
    client = metric_percentiles.config.destination_client
    client.get = AsyncMock()
    client.patch = AsyncMock(side_effect=_http_error(500, "Internal Server Error"))

    with pytest.raises(CustomClientHTTPError) as exc_info:
        _run(
            metric_percentiles.update_resource(
                "custom.metric",
                {"metric": "custom.metric", "include_percentiles": True},
            )
        )

    assert exc_info.value.status_code == 500
    client.get.assert_not_awaited()
