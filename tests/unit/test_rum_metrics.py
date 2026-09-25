# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMMetrics resource model.

RUM-based metrics (``/api/v2/rum/config/metrics``) are keyed by metric id (the
``{metric_id}`` path param == the metric name), mirroring spans_metrics. The
model uses ``skip_resource_mapping=True`` and falls back from a 409 on create
to a GET-by-id + PATCH, so a first run against a pre-populated destination
does not fail permanently.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_http_error(status: int) -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = status
    resp.message = "Conflict" if status == 409 else "Error"
    return CustomClientHTTPError(resp, message="rum metric already exists with that id")


def _metric(_id, query="@type:view"):
    return {
        "id": _id,
        "type": "rum_metrics",
        "attributes": {
            "compute": {"aggregation_type": "count", "include_percentiles": False, "path": "@view"},
            "event_type": "view",
            "filter": {"query": query},
            "group_by": [{"path": "@view.name", "tag_name": "view_name"}],
            "uniqueness": {"when": "match"},
        },
    }


def test_get_resources_returns_data_list():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": [_metric("rum.metric.count")]})

    resources = _run(rum.get_resources(client))

    assert resources == [_metric("rum.metric.count")]
    client.get.assert_awaited_once_with("/api/v2/rum/config/metrics")


def test_import_resource_by_id_gets_and_returns():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    source = AsyncMock()
    source.get = AsyncMock(return_value={"data": _metric("rum.metric.count")})
    rum.config.source_client = source

    _id, data = _run(rum.import_resource(_id="rum.metric.count"))

    assert _id == "rum.metric.count"
    assert data["id"] == "rum.metric.count"
    source.get.assert_awaited_once_with("/api/v2/rum/config/metrics/rum.metric.count")


def test_import_resource_passthrough_when_resource_supplied():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    rum.config.source_client = AsyncMock()

    resource = _metric("rum.metric.count")
    _id, data = _run(rum.import_resource(resource=resource))

    assert _id == "rum.metric.count"
    assert data is resource
    rum.config.source_client.get.assert_not_awaited()


def test_create_resource_posts_and_returns_data():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _metric("rum.metric.count")})
    rum.config.destination_client = dest

    resource = _metric("rum.metric.count")
    _id, data = _run(rum.create_resource("rum.metric.count", resource))

    assert _id == "rum.metric.count"
    assert data["id"] == "rum.metric.count"
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/config/metrics"
    assert post_payload == {"data": resource}


def test_update_resource_patches_destination_id():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(return_value={"data": _metric("rum.metric.count", query="@type:view updated")})
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_metrics"]["rum.metric.count"] = {"id": "rum.metric.count"}

    resource = _metric("rum.metric.count")
    _id, data = _run(rum.update_resource("rum.metric.count", resource))

    assert _id == "rum.metric.count"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/config/metrics/rum.metric.count"
    assert patch_payload == {"data": resource}


def test_delete_resource_deletes_destination_id():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    dest = AsyncMock()
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_metrics"]["rum.metric.count"] = {"id": "rum.metric.count"}

    _run(rum.delete_resource("rum.metric.count"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/config/metrics/rum.metric.count")


def test_create_resource_409_falls_back_to_get_then_patch():
    """POST 409 -> GET-by-id, state.destination hydrated, PATCH called."""
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    dest = AsyncMock()
    dest.post = AsyncMock(side_effect=_make_http_error(409))
    existing = _metric("rum.metric.count")
    dest.get = AsyncMock(return_value={"data": existing})
    dest.patch = AsyncMock(return_value={"data": _metric("rum.metric.count", query="@type:view updated")})
    rum.config.destination_client = dest

    _id, data = _run(rum.create_resource("rum.metric.count", _metric("rum.metric.count")))

    dest.post.assert_awaited_once()
    dest.get.assert_awaited_once_with("/api/v2/rum/config/metrics/rum.metric.count")
    assert (
        rum.config.state.destination["rum_metrics"]["rum.metric.count"] == existing
    ), "state.destination must be hydrated with the GET body so update_resource resolves the PATCH URL"
    dest.patch.assert_awaited_once()
    assert dest.patch.await_args.args[0] == "/api/v2/rum/config/metrics/rum.metric.count"
    assert _id == "rum.metric.count"


def test_create_resource_non_409_reraises():
    from datadog_sync.model.rum_metrics import RUMMetrics

    rum = RUMMetrics(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(side_effect=_make_http_error(500))
    dest.get = AsyncMock()
    dest.patch = AsyncMock()
    rum.config.destination_client = dest

    with pytest.raises(CustomClientHTTPError) as excinfo:
        _run(rum.create_resource("rum.metric.count", _metric("rum.metric.count")))
    assert excinfo.value.status_code == 500
    dest.get.assert_not_awaited()
    dest.patch.assert_not_awaited()
