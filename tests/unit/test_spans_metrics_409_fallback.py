# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for spans_metrics create_resource 409 fallback.

Pins the behavior that when the destination `/api/v2/apm/config/metrics` POST
returns 409 (metric name already exists), create_resource does a GET-by-id,
hydrates state.destination, and delegates to update_resource. Without this
fallback, `skip_resource_mapping=True` leaves state.destination empty and
first-run against a pre-populated destination fails permanently on 409.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.spans_metrics import SpansMetrics
from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests. See other test files in this suite for the pattern.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_http_error(status: int) -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = status
    resp.message = "Conflict" if status == 409 else "Error"
    return CustomClientHTTPError(resp, message="metric already exists with that name")


def test_create_resource_happy_path(mock_config):
    """POST succeeds → return (_id, resp['data']). No GET, no PATCH."""
    sm = SpansMetrics(mock_config)
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": {"id": "m1", "attributes": {}}})
    dest.get = AsyncMock()
    dest.patch = AsyncMock()
    mock_config.destination_client = dest

    _id, data = _run(sm.create_resource("m1", {"id": "m1", "attributes": {}}))

    assert _id == "m1"
    assert data == {"id": "m1", "attributes": {}}
    dest.post.assert_awaited_once()
    dest.get.assert_not_awaited()
    dest.patch.assert_not_awaited()


def test_create_resource_409_falls_back_to_get_then_patch(mock_config):
    """POST 409 → GET-by-id, state.destination[type][_id] populated, PATCH called."""
    sm = SpansMetrics(mock_config)
    dest = AsyncMock()
    dest.post = AsyncMock(side_effect=_make_http_error(409))
    existing_body = {"id": "ProcessDepositMessage", "attributes": {"managed": False}}
    dest.get = AsyncMock(return_value={"data": existing_body})
    dest.patch = AsyncMock(return_value={"data": {"id": "ProcessDepositMessage", "attributes": {"managed": True}}})
    mock_config.destination_client = dest

    _id, data = _run(sm.create_resource("ProcessDepositMessage", {"id": "ProcessDepositMessage", "attributes": {}}))

    dest.post.assert_awaited_once()
    dest.get.assert_awaited_once_with("/api/v2/apm/config/metrics/ProcessDepositMessage")
    assert mock_config.state.destination["spans_metrics"]["ProcessDepositMessage"] == existing_body, (
        "state.destination must be hydrated with the GET body so update_resource can resolve the PATCH URL"
    )
    dest.patch.assert_awaited_once()
    patch_url = dest.patch.await_args.args[0]
    assert patch_url == "/api/v2/apm/config/metrics/ProcessDepositMessage", (
        f"PATCH must target the id-scoped path, got: {patch_url}"
    )
    assert _id == "ProcessDepositMessage"
    assert data == {"id": "ProcessDepositMessage", "attributes": {"managed": True}}


def test_create_resource_non_409_reraises(mock_config):
    """Non-409 errors (400/403/500) propagate unchanged — no GET, no PATCH."""
    sm = SpansMetrics(mock_config)
    dest = AsyncMock()
    dest.post = AsyncMock(side_effect=_make_http_error(500))
    dest.get = AsyncMock()
    dest.patch = AsyncMock()
    mock_config.destination_client = dest

    with pytest.raises(CustomClientHTTPError) as excinfo:
        _run(sm.create_resource("m1", {"id": "m1", "attributes": {}}))
    assert excinfo.value.status_code == 500
    dest.get.assert_not_awaited()
    dest.patch.assert_not_awaited()
