# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from datadog_sync.model.metric_tag_configurations import MetricTagConfigurations
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _http_error(status: int, message: str = "err") -> CustomClientHTTPError:
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


def _resource(metric_name: str = "custom.metric") -> dict:
    return {
        "id": metric_name,
        "type": "manage_tags",
        "attributes": {"metric_type": "count", "tags": ["env", "service"]},
    }


@pytest.fixture
def metric_tag_configurations(mock_config):
    mock_config.destination_client = AsyncMock()
    return MetricTagConfigurations(mock_config)


def test_create_resource_happy_path(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.post = AsyncMock(return_value={"data": _resource()})
    client.get = AsyncMock()
    client.patch = AsyncMock()
    metric_tag_configurations.config.state.source["metric_tag_configurations"]["custom.metric"] = _resource()

    _id, data = _run(metric_tag_configurations.create_resource("custom.metric", _resource()))

    assert _id == "custom.metric"
    assert data == _resource()
    client.post.assert_awaited_once_with("/api/v2/metrics/custom.metric/tags", {"data": _resource()})
    client.get.assert_not_awaited()
    client.patch.assert_not_awaited()


def test_create_resource_missing_destination_metric_raises_skip(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.post = AsyncMock(side_effect=_http_error(400, "Cannot configure tags on a metric that does not exist"))
    client.get = AsyncMock()
    client.patch = AsyncMock()
    metric_tag_configurations.config.state.source["metric_tag_configurations"]["missing.metric"] = _resource(
        "missing.metric"
    )

    with pytest.raises(SkipResource) as exc_info:
        _run(metric_tag_configurations.create_resource("missing.metric", _resource("missing.metric")))

    assert "missing.metric" in str(exc_info.value)
    assert "not present on destination" in str(exc_info.value)
    client.post.assert_awaited_once()
    client.get.assert_not_awaited()
    client.patch.assert_not_awaited()


def test_create_resource_existing_config_conflict_gets_existing_then_patches(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.post = AsyncMock(side_effect=_http_error(409, "Conflicts with existing configuration; use PATCH to update"))
    client.get = AsyncMock(return_value={"data": {"id": "custom.metric", "attributes": {"tags": ["env"]}}})
    client.patch = AsyncMock(return_value={"data": {"id": "custom.metric", "attributes": {"tags": ["env", "service"]}}})
    metric_tag_configurations.config.state.source["metric_tag_configurations"]["custom.metric"] = _resource()

    _id, data = _run(metric_tag_configurations.create_resource("custom.metric", _resource()))

    client.post.assert_awaited_once()
    client.get.assert_awaited_once_with("/api/v2/metrics/custom.metric/tags")
    assert metric_tag_configurations.config.state.destination["metric_tag_configurations"]["custom.metric"] == {
        "id": "custom.metric",
        "attributes": {"tags": ["env"]},
    }
    client.patch.assert_awaited_once()
    patch_url = client.patch.await_args.args[0]
    assert patch_url == "/api/v2/metrics/custom.metric/tags"
    assert client.patch.await_args.args[1]["data"]["attributes"] == {"tags": ["env", "service"]}
    assert _id == "custom.metric"
    assert data == {"id": "custom.metric", "attributes": {"tags": ["env", "service"]}}


def test_create_resource_non_matching_409_propagates(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.post = AsyncMock(side_effect=_http_error(409, "conflict"))
    client.get = AsyncMock()
    client.patch = AsyncMock()
    metric_tag_configurations.config.state.source["metric_tag_configurations"]["custom.metric"] = _resource()

    with pytest.raises(CustomClientHTTPError) as exc_info:
        _run(metric_tag_configurations.create_resource("custom.metric", _resource()))

    assert exc_info.value.status_code == 409
    client.get.assert_not_awaited()
    client.patch.assert_not_awaited()


def test_update_resource_missing_destination_metric_raises_skip(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.patch = AsyncMock(side_effect=_http_error(400, "Cannot configure tags on a metric that does not exist"))
    metric_tag_configurations.config.state.destination["metric_tag_configurations"]["missing.metric"] = _resource(
        "missing.metric"
    )

    with pytest.raises(SkipResource) as exc_info:
        _run(metric_tag_configurations.update_resource("missing.metric", _resource("missing.metric")))

    assert "missing.metric" in str(exc_info.value)
    assert "not present on destination" in str(exc_info.value)
    client.patch.assert_awaited_once()


def test_update_resource_non_missing_metric_error_propagates(metric_tag_configurations):
    client = metric_tag_configurations.config.destination_client
    client.patch = AsyncMock(side_effect=_http_error(500, "Internal Server Error"))
    metric_tag_configurations.config.state.destination["metric_tag_configurations"]["custom.metric"] = _resource()

    with pytest.raises(CustomClientHTTPError) as exc_info:
        _run(metric_tag_configurations.update_resource("custom.metric", _resource()))

    assert exc_info.value.status_code == 500
