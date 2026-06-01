# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for host_tags 404 skip contract (NATHAN-53)."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.model.host_tags import HostTags
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


def _http_error(status, message="Host doesn't exist"):
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


@pytest.fixture
def host_tags():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.destination_client = AsyncMock()
    return HostTags(mock_config)


def test_update_resource_404_raises_skip_resource(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(404))
    with pytest.raises(SkipResource) as exc_info:
        asyncio.run(host_tags.update_resource("missing-host.example.com", ["env:prod"]))
    assert "missing-host.example.com" in str(exc_info.value)


def test_update_resource_404_logs_at_info_level(host_tags, caplog):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(404))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(SkipResource):
            asyncio.run(host_tags.update_resource("missing-host.example.com", ["env:prod"]))
    skip_records = [
        r
        for r in caplog.records
        if r.name == LOGGER_NAME
        and "missing-host.example.com" in r.getMessage()
        and "host no longer exists" in r.getMessage().lower()
    ]
    assert skip_records, "Expected an INFO log identifying the skipped host. caplog records: %r" % [
        (r.levelname, r.getMessage()) for r in caplog.records
    ]
    assert all(r.levelno == logging.INFO for r in skip_records)


def test_update_resource_500_propagates(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(500, "Internal Server Error"))
    with pytest.raises(CustomClientHTTPError) as exc_info:
        asyncio.run(host_tags.update_resource("some-host", ["env:prod"]))
    assert exc_info.value.status_code == 500


def test_update_resource_400_propagates(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(400, "Bad Request"))
    with pytest.raises(CustomClientHTTPError) as exc_info:
        asyncio.run(host_tags.update_resource("some-host", ["env:prod"]))
    assert exc_info.value.status_code == 400


def test_update_resource_403_propagates(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(403, "Forbidden"))
    with pytest.raises(CustomClientHTTPError) as exc_info:
        asyncio.run(host_tags.update_resource("some-host", ["env:prod"]))
    assert exc_info.value.status_code == 403


def test_update_resource_200_unchanged(host_tags):
    host_tags.config.destination_client.put = AsyncMock(return_value={"tags": ["env:prod", "team:hamr"]})
    _id, tags = asyncio.run(host_tags.update_resource("live-host", ["env:prod", "team:hamr"]))
    assert _id == "live-host"
    assert tags == ["env:prod", "team:hamr"]
    host_tags.config.destination_client.put.assert_awaited_once_with(
        "/api/v1/tags/hosts/live-host", {"tags": ["env:prod", "team:hamr"]}
    )


def test_create_resource_delegates_404_skip(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(404))
    with pytest.raises(SkipResource):
        asyncio.run(host_tags.create_resource("missing-host", ["env:prod"]))


def test_multi_host_loop_continues_after_404(host_tags):
    host_tags.config.destination_client.put = AsyncMock(
        side_effect=[
            {"tags": ["env:prod"]},
            _http_error(404),
            {"tags": ["env:staging"]},
        ]
    )
    synced, skipped = [], []
    for host_id, tags in [
        ("host-a", ["env:prod"]),
        ("host-b", ["env:prod"]),
        ("host-c", ["env:staging"]),
    ]:
        try:
            _id, returned_tags = asyncio.run(host_tags.update_resource(host_id, tags))
            synced.append((_id, returned_tags))
        except SkipResource:
            skipped.append(host_id)
    assert synced == [("host-a", ["env:prod"]), ("host-c", ["env:staging"])]
    assert skipped == ["host-b"]
    assert host_tags.config.destination_client.put.await_count == 3


def test_all_404_completes_cleanly(host_tags):
    host_tags.config.destination_client.put = AsyncMock(side_effect=_http_error(404))
    skipped = 0
    for host_id in ("host-a", "host-b", "host-c"):
        try:
            asyncio.run(host_tags.update_resource(host_id, ["env:prod"]))
        except SkipResource:
            skipped += 1
        except CustomClientHTTPError:
            pytest.fail("all-404 path must not surface CustomClientHTTPError")
    assert skipped == 3
