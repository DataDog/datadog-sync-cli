# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the Notebooks API-payload sanitization + state restoration.

Pins the contract for the two 400 Bad Request constructs the destination
Notebooks API rejects and that sync-cli must paper over without mutating the
resource stored in state (so source and destination state compare equal and
the resource does not update every run):

1. ``attributes.time`` with ``start == end`` -> the API requires
   ``start < end``. We nudge ``start`` back one second on the *copy* sent to
   the API, then restore the original ``time`` onto the response stored in
   destination state.
2. a ``sort`` object inside a cell's ``transformations`` list carrying an
   extra ``"type": "sort"`` key -> the API rejects it as an additional
   property. We strip ``type`` from the copy sent to the API, then stamp it
   back onto the response stored in destination state.

The invariant under test: after ``create_resource`` / ``update_resource``
the dict returned for destination state is equal to the source ``resource``
on every diffed field (``id`` is excluded from diff, so the destination id
may differ), while the payload actually sent to the API carried the
sanitized values.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from copy import deepcopy

import pytest

from datadog_sync.model.notebooks import Notebooks


@pytest.fixture
def notebooks():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.destination_client = AsyncMock()
    return Notebooks(mock_config)


def _nb(notebook_id, *, time=None, cells=None):
    return {
        "id": notebook_id,
        "type": "notebooks",
        "attributes": {
            "name": "N",
            "cells": cells if cells is not None else [],
            "time": time if time is not None else {"live_span": "1h"},
            "schema_version": 1,
        },
    }


def _analysis_cell_with_sort_type():
    """A cell whose transformation sort carries the rejected ``type`` key.

    Synthetic shape mirroring the API contract that triggers the 400:
    ``definition.query.query.transformations[0].sort = {"type": "sort", ...}``.
    All identifiers are obviously synthetic.
    """
    return {
        "type": "notebook_cells",
        "id": "cell-src-a",
        "attributes": {
            "definition": {
                "type": "analysis_transformation",
                "query": {
                    "name": "transformation_0",
                    "query": {
                        "type": "structured_analysis",
                        "source_dataset": "datasource_test",
                        "transformations": [
                            {
                                "type": "aggregation",
                                "sort": {"type": "sort", "order": "desc", "column": "col_count"},
                                "compute": [{"column": "", "aggregation": "count"}],
                                "group_by": [{"column": "col_entity"}],
                            },
                            {"type": "limit", "limit": 1000000},
                        ],
                    },
                },
                "data_source": "analysis_dataset",
            }
        },
    }


def _query_table_cell_with_order_by_sort():
    """A query_table cell whose ``sort`` uses ``order_by`` (no ``type`` key).

    This sort shape must NOT be touched by the sanitizer: only transformation
    sort objects carry ``"type": "sort"``.
    """
    return {
        "type": "notebook_cells",
        "id": "cell-src-b",
        "attributes": {
            "definition": {
                "type": "query_table",
                "title": "test-query-table",
                "requests": [
                    {
                        "sort": {"count": 500, "order_by": [{"type": "formula", "index": 0, "order": "desc"}]},
                        "queries": [
                            {"name": "query1", "query": "max:foo", "aggregator": "max", "data_source": "metrics"}
                        ],
                        "formulas": [{"formula": "query1", "cell_display_mode": "bar"}],
                        "response_format": "scalar",
                    }
                ],
                "has_search_bar": False,
            }
        },
    }


# -- _sanitize_for_api: source is never mutated -------------------------------


def test_sanitize_does_not_mutate_source_time(notebooks):
    source = _nb(1, time={"start": "2025-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00"})
    original = {k: v for k, v in source["attributes"]["time"].items()}

    notebooks._sanitize_for_api(source)

    assert source["attributes"]["time"] == original, "source resource must be left unchanged"


def test_sanitize_does_not_mutate_source_sort_type(notebooks):
    cell = _analysis_cell_with_sort_type()
    source = _nb(1, cells=[cell])
    original_sort = {
        k: v for k, v in cell["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"].items()
    }

    notebooks._sanitize_for_api(source)

    assert (
        cell["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"] == original_sort
    ), "source sort must be left unchanged"


# -- _sanitize_for_api: time window ------------------------------------------


def test_sanitize_nudges_start_back_one_second_when_start_equals_end(notebooks):
    source = _nb(1, time={"start": "2025-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00"})

    payload = notebooks._sanitize_for_api(source)

    assert payload["attributes"]["time"]["start"] == "2024-12-31T23:59:59+00:00"
    assert payload["attributes"]["time"]["end"] == "2025-01-01T00:00:00+00:00"


def test_sanitize_handles_z_suffix_timezone(notebooks):
    source = _nb(1, time={"start": "2025-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"})

    payload = notebooks._sanitize_for_api(source)

    assert payload["attributes"]["time"]["start"] == "2024-12-31T23:59:59+00:00"


def test_sanitize_leaves_time_alone_when_start_lt_end(notebooks):
    source = _nb(1, time={"start": "2024-12-31T23:59:59+00:00", "end": "2025-01-01T00:00:00+00:00"})

    payload = notebooks._sanitize_for_api(source)

    assert payload["attributes"]["time"]["start"] == "2024-12-31T23:59:59+00:00"


def test_sanitize_leaves_time_alone_when_only_live_span(notebooks):
    source = _nb(1, time={"live_span": "1h"})

    payload = notebooks._sanitize_for_api(source)

    assert payload["attributes"]["time"] == {"live_span": "1h"}


# -- _sanitize_for_api: sort type --------------------------------------------


def test_sanitize_strips_type_from_transformation_sort(notebooks):
    source = _nb(1, cells=[_analysis_cell_with_sort_type()])

    payload = notebooks._sanitize_for_api(source)

    sort = payload["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"]
    assert "type" not in sort, "sanitized payload must not carry the rejected type key"
    assert sort == {"order": "desc", "column": "col_count"}


def test_sanitize_does_not_touch_order_by_sort(notebooks):
    """The query_table ``sort`` (order_by shape, no top-level ``type``) must be
    passed through untouched — only transformation sorts carry ``type:sort``."""
    source = _nb(1, cells=[_query_table_cell_with_order_by_sort()])
    original = {
        "count": 500,
        "order_by": [{"type": "formula", "index": 0, "order": "desc"}],
    }

    payload = notebooks._sanitize_for_api(source)

    assert (
        payload["attributes"]["cells"][0]["attributes"]["definition"]["requests"][0]["sort"] == original
    ), "order_by sort must be untouched"


# -- create_resource / update_resource: payload sanitized, state restored -----


def test_create_resource_sends_sanitized_payload_and_restores_state(notebooks):
    """End-to-end contract for create:

    - the POST payload carries the sanitized (API-safe) values, and
    - the dict returned for destination state equals the source on every
      diffed field (the API response's sanitized values are restored to the
      source's originals), so the resource does not re-diff every run.
    """
    source = _nb(
        1,
        time={"start": "2025-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00"},
        cells=[_analysis_cell_with_sort_type()],
    )

    # API echoes back the sanitized payload (start nudged, sort type stripped)
    # plus a new destination id.
    def _echo(path, payload):
        echoed = deepcopy(payload["data"])
        echoed["id"] = 4242
        return {"data": echoed}

    notebooks.config.destination_client.post = AsyncMock(side_effect=_echo)

    _id, stored = asyncio.run(notebooks.create_resource("1", source))

    sent = notebooks.config.destination_client.post.await_args.args[1]["data"]
    assert sent["attributes"]["time"]["start"] == "2024-12-31T23:59:59+00:00", "API payload must be sanitized"
    sent_sort = sent["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0][
        "sort"
    ]
    assert "type" not in sent_sort, "API payload sort must have type stripped"

    # Destination state is restored to match source on the sanitized fields.
    assert (
        stored["attributes"]["time"] == source["attributes"]["time"]
    ), "destination state time must be restored to the source's original (start==end)"
    assert (
        stored["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"]
        == source["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"]
    ), "destination state sort must be restored to the source's original (type present)"
    # The destination id from the response is preserved (needed for updates).
    assert stored["id"] == 4242


def test_update_resource_sends_sanitized_payload_and_restores_state(notebooks):
    """Same contract as create, but for the PUT path (uses destination id)."""
    source = _nb(
        1,
        time={"start": "2025-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00"},
        cells=[_analysis_cell_with_sort_type()],
    )
    notebooks.config.state.destination = {"notebooks": {"1": {"id": 99}}}

    def _echo(path, payload):
        echoed = deepcopy(payload["data"])
        echoed["id"] = 99
        return {"data": echoed}

    notebooks.config.destination_client.put = AsyncMock(side_effect=_echo)

    _id, stored = asyncio.run(notebooks.update_resource("1", source))

    # PUT targeted the destination id, not the source id.
    assert notebooks.config.destination_client.put.await_args.args[0] == "/api/v1/notebooks/99"
    sent = notebooks.config.destination_client.put.await_args.args[1]["data"]
    assert sent["attributes"]["time"]["start"] == "2024-12-31T23:59:59+00:00"
    sent_sort = sent["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0][
        "sort"
    ]
    assert "type" not in sent_sort

    assert stored["attributes"]["time"] == source["attributes"]["time"]
    assert (
        stored["attributes"]["cells"][0]["attributes"]["definition"]["query"]["query"]["transformations"][0]["sort"][
            "type"
        ]
        == "sort"
    ), "restored destination state must carry the original sort type"


def test_create_resource_noop_when_no_sanitization_needed(notebooks):
    """A notebook that needs no sanitization round-trips unchanged (modulo id)."""
    source = _nb(1, time={"live_span": "1h"}, cells=[_query_table_cell_with_order_by_sort()])

    def _echo(path, payload):
        echoed = deepcopy(payload["data"])
        echoed["id"] = 77
        return {"data": echoed}

    notebooks.config.destination_client.post = AsyncMock(side_effect=_echo)

    _id, stored = asyncio.run(notebooks.create_resource("1", source))

    assert stored["attributes"]["time"] == {"live_span": "1h"}
    assert (
        stored["attributes"]["cells"][0]["attributes"]["definition"]["requests"][0]["sort"]
        == source["attributes"]["cells"][0]["attributes"]["definition"]["requests"][0]["sort"]
    )
