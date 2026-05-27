# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for notebooks resource handling.

Pins the lightweight-list + per-id GET pattern: the LIST request must not
carry include_cells=true, and import_resource must always GET the per-notebook
detail (cells included) regardless of whether it was invoked with _id or with
a LIST item dict. Also pins the failure-path contract: restricted (403) and
deleted-between-list-and-fetch (404) notebooks raise SkipResource so they do
not poison the run; other HTTP errors propagate.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.notebooks import Notebooks
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


@pytest.fixture
def notebooks():
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.source_client = AsyncMock()
    return Notebooks(mock_config)


def _detail_payload(notebook_id, name="N1", cells=None):
    return {
        "data": {
            "id": notebook_id,
            "type": "notebooks",
            "attributes": {
                "name": name,
                "cells": cells if cells is not None else [],
                "schema_version": 1,
            },
        }
    }


def _http_error(status):
    # CustomClientHTTPError reads response.status and response.message.
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"))


def test_get_resources_does_not_request_cells(notebooks):
    """LIST must omit include_cells=true and pass the pagination_config.

    The cells payload on the list endpoint grows with per-notebook cell count
    and dominates discovery wall-clock on populated orgs. Pinning both the
    param omission AND the pagination_config wiring catches two regressions:
    a future change that re-adds include_cells, OR a future refactor that
    drops pagination_config and silently single-pages large orgs.
    """
    mock_client = AsyncMock()
    mock_client.paginated_request = MagicMock(return_value=AsyncMock(return_value=[]))

    asyncio.run(notebooks.get_resources(mock_client))

    mock_client.paginated_request.assert_called_once_with(mock_client.get)
    inner = mock_client.paginated_request.return_value
    inner.assert_awaited_once()
    call_args = inner.await_args.args
    call_kwargs = inner.await_args.kwargs
    assert call_args == ("/api/v1/notebooks",), "LIST must call the notebooks base_path positionally; got %r" % (
        call_args,
    )
    params = call_kwargs.get("params", {})
    assert "include_cells" not in params, "LIST must not request include_cells; got params=%r" % params
    assert call_kwargs.get("pagination_config") is notebooks.pagination_config, (
        "LIST must forward the class-level pagination_config so meta.page.total_count "
        "drives termination; got kwargs=%r" % call_kwargs
    )


def test_import_resource_with_id_issues_get(notebooks):
    """Direct _id invocation must GET /api/v1/notebooks/{id}."""
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(42))

    _id, resource = asyncio.run(notebooks.import_resource(_id="42"))

    # Assert only the positional URL — the real source_client.get accepts extra
    # kwargs (params=, domain=, **kwargs) so an exact-kwargs assertion would
    # break on benign future refactors.
    notebooks.config.source_client.get.assert_awaited_once()
    assert notebooks.config.source_client.get.await_args.args[0] == "/api/v1/notebooks/42"
    # State writes round-trip through str(_id) upstream; pin the string contract
    # here so logs and the resources_handler emit consistent types.
    assert _id == "42"
    assert isinstance(_id, str)


def test_import_resource_short_circuits_when_caller_supplies_full_body(notebooks):
    """Pre-fetched body short-circuit: when the --id-file path drives
    get_resources_by_ids → import_resource(_id=id_), the returned body is
    placed in tmp_storage and the queue handler later calls
    _import_resource(resource=full_body). Without the short-circuit,
    notebooks.import_resource would do a SECOND GET per notebook, doubling
    rate-limit pressure on id-file runs. Detection by 'cells' presence —
    the lightweight LIST never returns cells, so a body with cells came
    from a prior per-id GET.
    """
    full_body = {
        "id": 88,
        "type": "notebooks",
        "attributes": {
            "name": "prefetched",
            "cells": [{"type": "notebook_cells"}],
            "schema_version": 1,
        },
    }
    notebooks.config.source_client.get = AsyncMock()

    _id, resource = asyncio.run(notebooks.import_resource(resource=full_body))

    # No GET fired — the prefetched body was used directly.
    notebooks.config.source_client.get.assert_not_awaited()
    assert _id == "88"
    assert resource["attributes"]["cells"]


def test_import_resource_with_list_item_still_issues_get(notebooks):
    """Invocation with a resource dict (as the queue dispatch path uses) must
    still GET per-notebook detail.

    Before this change, import_resource passed the LIST dict through unchanged,
    relying on the LIST having returned cells. With the lightweight LIST the
    dict no longer contains cells, so import_resource owns the detail fetch
    regardless of how it was invoked.
    """
    list_item = {"id": 99, "type": "notebooks", "attributes": {"name": "from-list"}}
    notebooks.config.source_client.get = AsyncMock(
        return_value=_detail_payload(99, name="from-detail", cells=[{"type": "notebook_cells"}])
    )

    _id, resource = asyncio.run(notebooks.import_resource(resource=list_item))

    notebooks.config.source_client.get.assert_awaited_once()
    assert notebooks.config.source_client.get.await_args.args[0] == "/api/v1/notebooks/99"
    assert _id == "99"
    assert isinstance(_id, str)
    assert (
        resource["attributes"]["name"] == "from-detail"
    ), "import_resource must return the GET payload, not the LIST item"
    assert resource["attributes"]["cells"], "the GET-returned cells must be preserved on the imported resource"


def test_import_resource_strips_ai_usage_tags(notebooks):
    """handle_special_case_attr must still run on the GET payload."""
    notebooks.config.source_client.get = AsyncMock(
        return_value={
            "data": {
                "id": 7,
                "type": "notebooks",
                "attributes": {
                    "name": "N",
                    "cells": [],
                    "schema_version": 1,
                    "tags": [
                        "env:prod",
                        "ai_generated:true",
                        "team:hamr",
                        "human_edited:false",
                    ],
                },
            }
        }
    )

    _, resource = asyncio.run(notebooks.import_resource(_id="7"))

    tags = resource["attributes"]["tags"]
    assert "env:prod" in tags
    assert "team:hamr" in tags
    assert not any(t.startswith("ai_generated:") for t in tags)
    assert not any(t.startswith("human_edited:") for t in tags)


def test_import_resource_403_raises_skip_resource(notebooks):
    """403 on the per-id GET (restricted notebook) must skip, not fail.

    Restricted notebooks can appear in the LIST result but the source identity
    may lack read access to the body. Hard-failing on these would poison every
    multi-tenant import run on any org that has even one restricted notebook.
    Mirrors dashboards.import_resource's 403 handling.
    """
    notebooks.config.source_client.get = AsyncMock(side_effect=_http_error(403))

    with pytest.raises(SkipResource) as exc_info:
        asyncio.run(notebooks.import_resource(_id="42"))
    assert "restricted" in str(exc_info.value).lower()


def test_import_resource_404_raises_skip_resource(notebooks):
    """404 on the per-id GET (deleted between list and fetch) must skip.

    The notebooks LIST/GET pair is no longer atomic: a notebook can be deleted
    in the window between LIST enumeration and per-id GET. Pre-change, the LIST
    response was self-contained so this race did not exist. Skipping (vs hard
    failing) preserves the prior behavior: a deletion that happens during the
    run should not break the run.
    """
    notebooks.config.source_client.get = AsyncMock(side_effect=_http_error(404))

    with pytest.raises(SkipResource) as exc_info:
        asyncio.run(notebooks.import_resource(_id="42"))
    assert "deleted" in str(exc_info.value).lower()


def test_import_resource_500_propagates(notebooks):
    """5xx and 429 on the per-id GET must propagate so retry budgets engage.

    SkipResource is reserved for terminal-but-benign outcomes (restricted,
    deleted). Transient server errors must propagate so the retry layer in
    custom_client and the per-type transient-failure budget in the id-file
    path can do their job. Pin the non-skip behavior here so a future
    "catch everything" refactor cannot silently mask transient failures.
    """
    notebooks.config.source_client.get = AsyncMock(side_effect=_http_error(500))

    with pytest.raises(CustomClientHTTPError):
        asyncio.run(notebooks.import_resource(_id="42"))


def test_import_resource_no_id_no_resource_raises_value_error(notebooks):
    """Defensive guard: caller must supply either _id or a resource dict with id.

    Without the guard, `resource["id"]` would KeyError or NoneType-subscript
    deep inside the worker, where the error gets logged without resource_type
    context. Surface the misuse as a clean ValueError at the call site instead.
    """
    with pytest.raises(ValueError):
        asyncio.run(notebooks.import_resource())

    with pytest.raises(ValueError):
        asyncio.run(notebooks.import_resource(resource={}))


def test_resource_config_list_omitted_attr_prefixes(notebooks):
    """Pin that notebooks declares attributes.cells as a list-omitted prefix.

    Without this, --filter expressions referencing attributes.cells.*
    silently no-op against the LIST item (missing path → False; with
    Operator=Not → True, i.e. match-everything). The prefix tells the
    handler to defer cells.* filters to the post-GET pass. Removing or
    renaming this must be a deliberate, tested choice.
    """
    assert "attributes.cells" in notebooks.resource_config.list_omitted_attr_prefixes, (
        "notebooks LIST omits cells; attributes.cells must be declared so the "
        "handler defers cells.* filters to the post-GET pass"
    )


def test_post_get_refilter_raises_filtered_resource_when_rejected(notebooks):
    """When the user's --filter rejects the GET payload, _import_resource raises
    FilteredResource (so the resources_handler buckets it as `filtered`) and
    the state write is skipped.

    Pinning the FilteredResource (vs SkipResource) raise here matters for
    accounting: filtered counts the user's --filter intent, skipped counts
    benign-terminal outcomes like 403/404. The resources_handler depends on
    this distinction to emit the right NDJSON event type.
    """
    from datadog_sync.utils.resource_utils import FilteredResource

    # Mock a filter that rejects the GET payload (looks at a cells-derived field).
    filter_mock = MagicMock(return_value=False)
    notebooks.filter = filter_mock
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(7, cells=[{"type": "notebook_cells"}]))

    with pytest.raises(FilteredResource):
        asyncio.run(notebooks._import_resource(_id="7"))

    # filter() was invoked against the GET payload (which has cells), not the
    # LIST item the caller passed in. Confirm by checking the filter call.
    filter_mock.assert_called_once()
    filtered_arg = filter_mock.call_args.args[0]
    assert filtered_arg["attributes"]["cells"], "post-GET filter must see the full body (with cells), not the LIST item"
    # State write was skipped.
    notebooks.config.state.set_source.assert_not_called()


def test_post_get_refilter_passes_through_when_filter_accepts(notebooks):
    """When the post-GET filter accepts, _import_resource writes state normally."""
    notebooks.filter = MagicMock(return_value=True)
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(11))

    _id = asyncio.run(notebooks._import_resource(_id="11"))

    assert _id == "11"
    notebooks.config.state.set_source.assert_called_once()


def test_post_get_refilter_inactive_with_no_filters_configured(notebooks):
    """When list_omitted_attr_prefixes is set but the user has no --filter
    configured, the post-GET re-filter must NOT raise FilteredResource.

    Guards against a regression where the flag turns into "always filter" and
    breaks the no-filter case (which is the common path for most users).
    Uses the real Notebooks.filter() method — not a mock — so the
    base_resource.filter() short-circuit on empty self.config.filters is
    actually exercised.
    """
    notebooks.config.filters = None  # The common case: no --filter provided.
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(13))

    _id = asyncio.run(notebooks._import_resource(_id="13"))

    assert _id == "13"
    notebooks.config.state.set_source.assert_called_once()


# Handler-level tests below exercise the full LIST-time-then-post-GET filter
# flow. These pin the contract that codex P1 flagged: with
# list_omitted_attr_prefixes is set, the handler partitions the user's
# filters: list-safe filters (e.g. attributes.name) still short-circuit at
# LIST-time on the cheap LIST response, while list-unsafe filters (those
# referencing attributes.cells.X) are deferred to the post-GET pass.
# Unit-test-only mocks of r_class.filter would mask this — these tests use
# the real Filter class so the partition + post-GET re-evaluation is
# validated end-to-end.


def _make_handler():
    """Build a minimally-wired ResourcesHandler so _import_resource(q_item) runs."""
    from datadog_sync.utils.resources_handler import ResourcesHandler

    config = MagicMock()
    config.emit_json = False
    config.filters = None
    config.logger = MagicMock()
    handler = ResourcesHandler.__new__(ResourcesHandler)
    handler.config = config
    handler.worker = MagicMock()
    handler.worker.counter = MagicMock()
    handler._emit = MagicMock()
    return handler


def _make_filter(resource_type, attr_name, value, operator="ExactMatch"):
    """Build a Filter dict the way the CLI does — via process_filters.

    Going through process_filters (not constructing Filter() directly) ensures
    the test exercises the real filter-compilation contract: ExactMatch
    operator wraps the value in ^...$, SubString operator wraps in .*...*,
    operator strings lowercase, resource_type lowercase, regex DOTALL flag.
    A previous version of this helper compiled re.compile(value) directly,
    which silently disagreed with production filter semantics for any
    test value that wasn't already-anchored.
    """
    from datadog_sync.utils.filter import process_filters

    filter_str = f"Type={resource_type};Name={attr_name};Value={value};Operator={operator}"
    parsed = process_filters([filter_str])
    return parsed[resource_type.lower()][0]


# Real notebook cells are shaped per the API contract recorded in our
# integration cassettes: each cell is
#   {"type": "notebook_cells", "id": "<short>", "attributes": {"definition": ...}}
# (cells.attributes.definition.type, NOT cells.definition.type — definition
# is nested under the cell's "attributes" envelope). Tests below use the
# real shape so a filter that works in unit tests will also work in
# production against actual API payloads.
def _cell(cell_type, cell_id="c1"):
    return {
        "type": "notebook_cells",
        "id": cell_id,
        "attributes": {"definition": {"type": cell_type}},
    }


def test_handler_metadata_filter_short_circuits_at_list_time(notebooks):
    """List-safe filters (attr_name not under any list_omitted_attr_prefix)
    must still short-circuit at LIST-time — no per-id GET for items the
    metadata filter rejects.

    Regression test against an earlier design that skipped the LIST-time
    pre-filter wholesale when list_omitted_attr_prefixes was set. That
    forced a per-id GET for every item even when the user's filter could
    have been decided cheaply on the LIST item (e.g., filter by name).
    For an 18k-notebook org with a name filter keeping 100, that turned
    1 LIST page into 18k GETs.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {"notebooks": [_make_filter("notebooks", "attributes.name", "keep-this")]}
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(77))

    # LIST item whose name does NOT match — must be filtered at LIST-time,
    # no GET fired.
    list_item = {"id": 77, "type": "notebooks", "attributes": {"name": "different"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    notebooks.config.source_client.get.assert_not_awaited()
    handler.worker.counter.increment_filtered.assert_called_once()
    handler.worker.counter.increment_success.assert_not_called()


def test_handler_metadata_filter_accept_proceeds_to_get(notebooks):
    """List-safe filter that ACCEPTS at LIST-time still proceeds to the GET
    (and the post-GET pass evaluates all filters against the full body).
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {"notebooks": [_make_filter("notebooks", "attributes.name", "keep-this")]}
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(78, name="keep-this"))

    list_item = {"id": 78, "type": "notebooks", "attributes": {"name": "keep-this"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    notebooks.config.source_client.get.assert_awaited_once()
    handler.worker.counter.increment_success.assert_called_once()


def test_handler_defers_list_unsafe_filter_to_post_get(notebooks):
    """Codex P1 regression test: a positive filter on a missing-from-LIST path
    (attributes.cells.attributes.definition.type) used to be rejected at
    LIST-time because the cell-less LIST item had no such path. With
    list_omitted_attr_prefixes including attributes.cells, the handler must
    defer this filter to the post-GET pass so the per-id GET fires and the
    post-GET filter can evaluate against the real cells.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    # A positive filter that the LIST item cannot satisfy (no cells in LIST).
    handler.config.filters = {
        "notebooks": [_make_filter("notebooks", "attributes.cells.attributes.definition.type", "markdown")]
    }
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock(return_value=_detail_payload(55, cells=[_cell("markdown")]))

    list_item = {"id": 55, "type": "notebooks", "attributes": {"name": "n"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    # The per-id GET fired — the LIST-time filter did NOT short-circuit.
    notebooks.config.source_client.get.assert_awaited_once()
    # And the resource passed the post-GET filter, so it was a success.
    handler.worker.counter.increment_success.assert_called_once()
    handler.worker.counter.increment_filtered.assert_not_called()


def test_handler_post_get_filter_rejects_when_cells_do_not_match(notebooks):
    """The post-GET filter must reject notebooks whose real cells do NOT match
    the user's --filter. Codex P1's positive-filter scenario in reverse:
    confirms FilteredResource fires and is bucketed as `filtered`, not
    `failure` or `success`.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {
        "notebooks": [_make_filter("notebooks", "attributes.cells.attributes.definition.type", "markdown")]
    }
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock(
        return_value=_detail_payload(56, cells=[_cell("timeseries")])  # doesn't match
    )

    list_item = {"id": 56, "type": "notebooks", "attributes": {"name": "n"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    notebooks.config.source_client.get.assert_awaited_once()
    handler.worker.counter.increment_filtered.assert_called_once()
    handler.worker.counter.increment_success.assert_not_called()
    # State write was skipped because FilteredResource fired before set_source.
    notebooks.config.state.set_source.assert_not_called()


def test_handler_mixed_or_filter_defers_when_list_safe_misses(notebooks):
    """Codex P1 regression test for OR semantics with mixed list-safe and
    list-unsafe filters.

    Scenario: --filter Type=notebooks;Name=attributes.name;Value=foo OR
              --filter Type=notebooks;Name=attributes.cells.X;Value=markdown
    (default OR; first is list-safe, second is list-unsafe).

    The LIST item has name="bar" (does NOT match foo). An earlier version of
    _list_time_filter_passes returned `any(list_safe)` → False and treated
    that as decisive reject, silently dropping the notebook before the
    cells filter ever ran post-GET. A deferred OR clause might still
    match — we must defer to the per-id GET.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {
        "notebooks": [
            _make_filter("notebooks", "attributes.name", "foo"),
            _make_filter("notebooks", "attributes.cells.attributes.definition.type", "markdown"),
        ]
    }
    handler.config.filter_operator = "or"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "or"
    # GET returns a body whose cells DO match the deferred filter.
    notebooks.config.source_client.get = AsyncMock(
        return_value=_detail_payload(101, name="bar", cells=[_cell("markdown")])
    )

    list_item = {"id": 101, "type": "notebooks", "attributes": {"name": "bar"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    # The per-id GET must have fired (LIST-time miss was NOT decisive because
    # of the deferred clause) and the post-GET filter must have accepted.
    notebooks.config.source_client.get.assert_awaited_once()
    handler.worker.counter.increment_success.assert_called_once()
    handler.worker.counter.increment_filtered.assert_not_called()


def test_handler_mixed_or_filter_short_circuits_when_list_safe_hits(notebooks):
    """OR with mixed filters: when the list-safe clause matches at LIST-time,
    we can short-circuit accept — the OR is already satisfied. The deferred
    clause does not need to be evaluated (post-GET still runs the full set
    but won't reject because OR already passed).

    This pins the perf-optimization side of the partition: list-safe
    accepts still avoid forcing a re-evaluation we don't strictly need.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {
        "notebooks": [
            _make_filter("notebooks", "attributes.name", "match-me"),
            _make_filter("notebooks", "attributes.cells.attributes.definition.type", "timeseries"),
        ]
    }
    handler.config.filter_operator = "or"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "or"
    notebooks.config.source_client.get = AsyncMock(
        return_value=_detail_payload(102, name="match-me", cells=[_cell("markdown")])
    )

    list_item = {"id": 102, "type": "notebooks", "attributes": {"name": "match-me"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    # Name matched at LIST-time → OR satisfied → proceed to GET → post-GET
    # also passes (OR still satisfied by the name match even though the
    # cells clause misses). Success.
    notebooks.config.source_client.get.assert_awaited_once()
    handler.worker.counter.increment_success.assert_called_once()


def test_handler_or_filter_all_list_safe_decisive_reject(notebooks):
    """OR with only list-safe filters: an all-miss IS decisive — no
    deferred clauses can rescue it. The list item is rejected at LIST-time
    without a per-id GET.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {
        "notebooks": [
            _make_filter("notebooks", "attributes.name", "foo"),
            _make_filter("notebooks", "attributes.status", "published"),
        ]
    }
    handler.config.filter_operator = "or"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "or"
    notebooks.config.source_client.get = AsyncMock()

    list_item = {
        "id": 103,
        "type": "notebooks",
        "attributes": {"name": "bar", "status": "draft"},
    }
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    notebooks.config.source_client.get.assert_not_awaited()
    handler.worker.counter.increment_filtered.assert_called_once()


def test_handler_and_filter_list_safe_miss_decisive_reject(notebooks):
    """AND with mixed filters: a list-safe miss is decisive reject. The
    deferred clause can't rescue an AND that already has one failing
    clause.
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    handler.config.filters = {
        "notebooks": [
            _make_filter("notebooks", "attributes.name", "must-match"),
            _make_filter("notebooks", "attributes.cells.attributes.definition.type", "markdown"),
        ]
    }
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock()

    # Name does NOT match — AND is already broken; no GET should fire.
    list_item = {"id": 104, "type": "notebooks", "attributes": {"name": "different"}}
    asyncio.run(handler._import_resource(["notebooks", list_item]))

    notebooks.config.source_client.get.assert_not_awaited()
    handler.worker.counter.increment_filtered.assert_called_once()


def test_force_missing_dep_bypasses_user_filter(notebooks):
    """--force-missing-dependencies must import a dep notebook/dashboard even
    when --filter would normally reject it.

    Rationale: the dep was pulled in because a kept resource references it.
    If we honored --filter for the dep, the resource would be absent from
    source state and downstream run_sorter() (in the sync/apply phase) would
    fail with unresolved ID references on the dependent resource. The
    operator's --filter intent applies to the top-level resource selection,
    not transitively to force-imported deps. _force_missing_dep_import_cb
    therefore calls _import_resource with skip_filter=True to bypass the
    post-GET filter check.

    Without this bypass, the post-GET filter check would turn
    --force-missing-dependencies into a no-op for any filtered dep, a
    regression vs the pre-PR behavior (where force-missing-deps did not
    run the filter at all because the filter check did not exist).
    """
    handler = _make_handler()
    handler.config.resources = {"notebooks": notebooks}
    # User filter would reject this notebook's cells (timeseries, not markdown).
    handler.config.filters = {
        "notebooks": [_make_filter("notebooks", "attributes.cells.attributes.definition.type", "markdown")]
    }
    handler.config.filter_operator = "and"
    notebooks.config.filters = handler.config.filters
    notebooks.config.filter_operator = "and"
    notebooks.config.source_client.get = AsyncMock(
        return_value=_detail_payload(
            99,
            cells=[
                {
                    "type": "notebook_cells",
                    "id": "abc",
                    "attributes": {"definition": {"type": "timeseries"}},
                }
            ],
        )
    )

    # Stub the post-success dep-graph bookkeeping the cb does after import.
    # q_item is a tuple in production (used as a dict key in _dependency_graph).
    handler._dependency_graph = {}
    handler._resource_connections = MagicMock(return_value=([], []))

    asyncio.run(handler._force_missing_dep_import_cb(("notebooks", "99")))

    # The dep GET fired and the import SUCCEEDED despite the user filter
    # that would have rejected it on the regular queue path.
    notebooks.config.source_client.get.assert_awaited_once()
    emit_calls = handler._emit.call_args_list
    statuses = [c.args[3] for c in emit_calls]
    assert "success" in statuses, (
        f"force-missing-dep must succeed even when --filter would reject; " f"emit calls: {emit_calls}"
    )
    assert "filtered" not in statuses, f"force-missing-dep must NOT bucket as 'filtered'; emit calls: {emit_calls}"
    # State was written (the dep is now available for ID remapping downstream).
    notebooks.config.state.set_source.assert_called_once()
