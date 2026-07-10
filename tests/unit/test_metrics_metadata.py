# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for metrics_metadata destination-existence filter (HAMR-392 Jul8-T20)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.metrics_metadata import MetricsMetadata
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


def _http_error(status, message="err"):
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


@pytest.fixture
def metrics_metadata():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.destination_client = AsyncMock()
    return MetricsMetadata(mock_config)


def test_update_resource_dest_exists_calls_put(metrics_metadata):
    """When destination GET returns 200, the PUT proceeds and metadata is updated."""
    client = metrics_metadata.config.destination_client
    client.get = AsyncMock(return_value={"data": {"id": "my.metric", "type": "metrics"}})
    client.put = AsyncMock(return_value={"description": "updated"})

    resource = {"description": "updated", "unit": "byte"}
    _id, resp = asyncio.run(metrics_metadata.update_resource("my.metric", resource))

    assert _id == "my.metric"
    assert resp == {"description": "updated"}
    client.get.assert_awaited_once_with("/api/v1/metrics/my.metric")
    client.put.assert_awaited_once_with("/api/v1/metrics/my.metric", resource)


def test_update_resource_dest_missing_raises_skip(metrics_metadata):
    """When destination GET returns 404, PUT is skipped and SkipResource is raised."""
    client = metrics_metadata.config.destination_client
    client.get = AsyncMock(side_effect=_http_error(404))
    client.put = AsyncMock()

    with pytest.raises(SkipResource) as exc_info:
        asyncio.run(metrics_metadata.update_resource("missing.metric", {"description": "x"}))

    assert "missing.metric" in str(exc_info.value)
    assert "not present on destination" in str(exc_info.value)
    client.put.assert_not_awaited()


def test_update_resource_get_500_propagates(metrics_metadata):
    """When destination GET returns 500, the error propagates (retry layer handles it)."""
    client = metrics_metadata.config.destination_client
    client.get = AsyncMock(side_effect=_http_error(500, "Internal Server Error"))
    client.put = AsyncMock()

    with pytest.raises(CustomClientHTTPError) as exc_info:
        asyncio.run(metrics_metadata.update_resource("some.metric", {"description": "x"}))

    assert exc_info.value.status_code == 500
    client.put.assert_not_awaited()


def test_update_resource_get_403_propagates(metrics_metadata):
    """When destination GET returns 403 (permission), the error propagates unchanged."""
    client = metrics_metadata.config.destination_client
    client.get = AsyncMock(side_effect=_http_error(403, "Forbidden"))
    client.put = AsyncMock()

    with pytest.raises(CustomClientHTTPError) as exc_info:
        asyncio.run(metrics_metadata.update_resource("some.metric", {"description": "x"}))

    assert exc_info.value.status_code == 403
    client.put.assert_not_awaited()


def test_create_resource_delegates_to_update(metrics_metadata):
    """create_resource remains a thin wrapper around update_resource (behaviour unchanged)."""
    client = metrics_metadata.config.destination_client
    client.get = AsyncMock(return_value={"data": {"id": "my.metric"}})
    client.put = AsyncMock(return_value={"description": "created"})

    _id, resp = asyncio.run(metrics_metadata.create_resource("my.metric", {"description": "created"}))

    assert _id == "my.metric"
    assert resp == {"description": "created"}
    client.get.assert_awaited_once()
    client.put.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
