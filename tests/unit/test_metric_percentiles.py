# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from datadog_sync.model.metric_percentiles import MetricPercentiles
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


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
    client.get.assert_not_awaited()
    client.patch.assert_awaited_once()


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
