# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for downtime_schedules source-side fetch parameters.

Pins that `get_resources` (LIST) and `import_resource` (per-id GET) both
request `include=created_by` on the source-side downtime API. Without
`include=created_by`, the response body omits the `relationships` block
entirely, and consumers that key on `relationships.created_by.data.id`
(e.g. downstream OBO-grouping tooling that routes each resource under
its creator's identity) fall back to a service-account identity for
every downtime.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from datadog_sync.model.downtime_schedules import DowntimeSchedules


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests. See other test files in this suite for the pattern.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_get_resources_passes_include_created_by(mock_config):
    """Regression pin: LIST fetch must pass `include=created_by`. Without it
    the source API omits `relationships`, and OBO grouping downstream loses
    the creator identity — every downtime falls to service-account fallback
    and hits restricted-monitor ACLs, producing 403s at POST time."""
    downtime = DowntimeSchedules(mock_config)

    client = MagicMock()
    inner_get = AsyncMock(return_value=[{"id": "d1", "attributes": {}}])
    client.paginated_request = MagicMock(return_value=inner_get)
    client.get = MagicMock()

    _run(downtime.get_resources(client))

    client.paginated_request.assert_called_once_with(client.get)
    call_args = inner_get.call_args
    assert call_args.kwargs["params"] == {"include": "created_by"}, (
        f"expected params={{'include': 'created_by'}}, got {call_args.kwargs.get('params')}"
    )


def test_import_resource_by_id_passes_include_created_by(mock_config):
    """Per-id GET path must also pass `include=created_by`. Path is exercised
    by the id-file / on-demand loading flow (base_resource.py:248) — same
    downstream OBO consumer applies."""
    downtime = DowntimeSchedules(mock_config)
    source_client = AsyncMock()
    source_client.get = AsyncMock(
        return_value={
            "data": {
                "id": "d1",
                "type": "downtime",
                "attributes": {"canceled": None},
                "relationships": {"created_by": {"data": {"id": "u1", "type": "users"}}},
            },
            "included": [{"type": "users", "id": "u1", "attributes": {}}],
        }
    )
    mock_config.source_client = source_client

    _id, resource = _run(downtime.import_resource(_id="d1"))

    source_client.get.assert_called_once()
    call_args = source_client.get.call_args
    assert call_args.kwargs["params"] == {"include": "created_by"}, (
        f"expected params={{'include': 'created_by'}}, got {call_args.kwargs.get('params')}"
    )


def test_import_resource_by_id_unwraps_jsonapi_envelope(mock_config):
    """Per-id GET returns the JSON:API envelope `{"data": {...}, "included": [...]}`.
    The LIST path is unwrapped by `paginated_request` via `response_list_accessor`,
    but the per-id path was raw before this change. Unwrap `data` here so the
    rest of the method (which reads `resource["attributes"]`) sees a bare
    resource object either way — and so the added top-level `included` block
    doesn't leak into downstream state."""
    downtime = DowntimeSchedules(mock_config)
    source_client = AsyncMock()
    envelope = {
        "data": {
            "id": "d1",
            "type": "downtime",
            "attributes": {"canceled": None, "schedule": {"start": "2027-01-01T00:00:00Z"}},
            "relationships": {"created_by": {"data": {"id": "u1", "type": "users"}}},
        },
        "included": [{"type": "users", "id": "u1", "attributes": {"handle": "user@example.com"}}],
    }
    source_client.get = AsyncMock(return_value=envelope)
    mock_config.source_client = source_client

    _id, resource = _run(downtime.import_resource(_id="d1"))

    assert _id == "d1"
    # resource must be the bare data object, not the envelope
    assert "included" not in resource, "the top-level included block must be stripped"
    assert resource["id"] == "d1"
    assert resource["type"] == "downtime"
    assert resource["attributes"]["schedule"]["start"] == "2027-01-01T00:00:00Z"
    # relationships.created_by.data.id must survive — this is the whole point
    assert resource["relationships"]["created_by"]["data"]["id"] == "u1"


def test_import_resource_with_pre_fetched_resource_is_untouched(mock_config):
    """When called with `resource=<already-fetched>` (LIST-driven flow, the
    common case), the hook does no fetch — the input is returned as-is. This
    is the pre-existing behavior; the envelope-unwrap in the `_id` branch
    must not affect this path."""
    downtime = DowntimeSchedules(mock_config)
    pre_fetched = {
        "id": "d1",
        "type": "downtime",
        "attributes": {"canceled": None},
        "relationships": {"created_by": {"data": {"id": "u1", "type": "users"}}},
    }

    _id, resource = _run(downtime.import_resource(resource=pre_fetched))

    assert _id == "d1"
    assert resource is pre_fetched
