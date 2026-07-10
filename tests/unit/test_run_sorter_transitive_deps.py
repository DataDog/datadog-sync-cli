# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Regression tests for run_sorter transitive-dependency handling.

Under --minimize-reads, when --resources scopes sync to a subset of
resource types (e.g. --resources=dashboards), _resource_connections
lazy-loads referenced dependencies (e.g. monitors that dashboards'
alert_id widgets point at) via ensure_resource_loaded, and adds those
dependencies as edges in the graph fed to TopologicalSorter.

Those dependency-only nodes appear in the graph purely for ordering.
Applying them would require THEIR own connections to be present in
state.destination, but ensure_resource_loaded is NOT recursive — it
loads the dep itself but not the dep's own transitive references —
so their connect_resources() fails with "missing connections" even
when the referenced destination JSONs exist on disk.

run_sorter()'s skip predicate is deliberately narrow — all three
conditions must hold for the skip to fire:

  1. state._minimize_reads is True (full-load runs do not have the
     partially-populated-state problem).
  2. node[0] is not in resources_arg (the operator did not ask us to
     sync this type).
  3. node[1] is already in state.destination[node[0]] (the parent's
     ensure_resource_loaded already populated the mapping needed for
     the parent's connect_id remap; nothing more to do).

If ANY condition fails, the node is dispatched as before:

  * Full-load mode (condition 1 false): pre-existing behavior; every
    ready node gets dispatched and its own connect_resources() succeeds
    because full-load populated every type's state.
  * In-scope type (condition 2 false): the operator asked for this
    type; dispatch it normally.
  * Absent destination (condition 3 false): most importantly, this is
    the --force-missing-dependencies path — the missing dep was added
    to the graph by _force_missing_dep_import_cb precisely so
    run_sorter creates it; skipping there would leave the parent's
    connect_resources() to fail.

Tests below cover each branch of that predicate.
"""

from collections import defaultdict
from graphlib import TopologicalSorter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datadog_sync.utils import resources_handler as rh_module
from datadog_sync.utils.resources_handler import ResourcesHandler


class _FakeQueue:
    """Async work queue that records dispatched nodes and closes the loop.

    Production run_sorter dispatches nodes onto a queue consumed by
    _apply_resource_cb, which calls sorter.done(node) in its finally
    block. Without that ack, the TopologicalSorter's is_active() stays
    True forever and run_sorter never exits. This fake records the
    node AND immediately marks it done on the sorter it was given —
    matching the production dispatch/ack contract closely enough to
    let run_sorter reach its natural termination.
    """

    def __init__(self, sorter):
        self.sorter = sorter
        self.enqueued = []

    async def put(self, item):
        self.enqueued.append(item)
        self.sorter.done(item)


def _make_handler(
    dep_graph,
    source_state,
    resources_arg,
    destination_state=None,
    minimize_reads=True,
):
    """Build a minimally-wired ResourcesHandler ready to call run_sorter().

    dep_graph: {(type, id): {(dep_type, dep_id), ...}} — same shape
      get_dependency_graph() returns.
    source_state: {type: {id: resource_dict}} — populated as
      config.state.source (a defaultdict of dicts to match production
      behavior).
    resources_arg: list of resource types the operator asked for.
    destination_state: optional {type: {id: resource_dict}} — populated
      as config.state.destination. Defaults to a copy of source_state
      (matches the common lazy-load-through-ensure_resource_loaded path
      where source and destination for out-of-scope deps are both
      loaded from disk).
    minimize_reads: value for config.state._minimize_reads. The new
      run_sorter skip only fires under minimize-reads mode; full-load
      (False) preserves the pre-PR behavior of dispatching every ready
      node.
    """
    handler = ResourcesHandler.__new__(ResourcesHandler)
    handler.config = MagicMock()
    handler.config.resources_arg = resources_arg
    handler.config.logger = MagicMock()

    # state.source[type][id] and state.destination[type][id] must not
    # KeyError for arbitrary types touched by run_sorter's checks; use
    # defaultdict-of-dicts to mirror production state.
    handler.config.state = MagicMock()
    handler.config.state._minimize_reads = minimize_reads
    handler.config.state.source = defaultdict(dict)
    for rt, ids in source_state.items():
        handler.config.state.source[rt].update(ids)
    handler.config.state.destination = defaultdict(dict)
    if destination_state is None:
        destination_state = source_state
    for rt, ids in destination_state.items():
        handler.config.state.destination[rt].update(ids)

    handler.sorter = TopologicalSorter(dep_graph)
    handler.sorter.prepare()
    handler.worker = MagicMock()
    handler.worker.work_queue = _FakeQueue(handler.sorter)
    return handler


@pytest.mark.asyncio
async def test_transitive_dep_type_not_in_resources_arg_is_skipped():
    """--resources=dashboards batch: a monitor dep node (loaded via
    ensure_resource_loaded to satisfy the dashboard's alert_id widget)
    appears in the graph but must NOT reach the work queue.

    This is the HAMR-392 T1 regression: without the skip, run_sorter
    dispatched the monitor to _apply_resource_cb, which called
    monitor.connect_resources() → base connect_id() → looked up the
    monitor's own restricted_roles in state.destination["roles"] (empty,
    because no one lazy-loaded those transitive role deps) → emitted
    the misleading `[monitors - X] missing connections: {'roles': [...]}`
    error line during a --resources=dashboards batch.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-1")},
        ("monitors", "mon-1"): set(),  # leaf in the graph, loaded by ensure_resource_loaded
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        "monitors": {"mon-1": {"id": "mon-1"}},
    }
    handler = _make_handler(dep_graph, source_state, resources_arg=["dashboards"])

    await handler.run_sorter()

    enqueued_types = {node[0] for node in handler.worker.work_queue.enqueued}
    assert "dashboards" in enqueued_types, "the requested type must still be dispatched"
    assert "monitors" not in enqueued_types, (
        "a dep-only monitor node (type not in resources_arg) must NOT reach the work "
        "queue — dispatching it triggers a false missing-connections error because the "
        "monitor's own transitive role deps were never lazy-loaded"
    )


@pytest.mark.asyncio
async def test_dep_type_in_resources_arg_is_still_dispatched():
    """--resources=dashboards,monitors: monitors are top-level in resources_arg,
    so their get_dependency_graph pass populates their role deps, and their
    connect_resources() succeeds. They must reach the work queue.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-1")},
        ("monitors", "mon-1"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        "monitors": {"mon-1": {"id": "mon-1"}},
    }
    handler = _make_handler(dep_graph, source_state, resources_arg=["dashboards", "monitors"])

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    assert ("dashboards", "dash-1") in enqueued
    assert ("monitors", "mon-1") in enqueued


@pytest.mark.asyncio
async def test_missing_from_source_still_skipped():
    """Pre-existing behavior guard: a node whose id is not in state.source
    is skipped (already-attempted-and-missing dependencies). This branch
    must remain unaffected by the new resources_arg gate.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-missing")},
        ("monitors", "mon-missing"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        # No source entry for "mon-missing" — simulates a dep that
        # _force_missing_dep_import_cb could not obtain.
    }
    handler = _make_handler(dep_graph, source_state, resources_arg=["dashboards", "monitors"])

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    assert ("dashboards", "dash-1") in enqueued
    assert ("monitors", "mon-missing") not in enqueued


@pytest.mark.asyncio
async def test_resources_arg_all_types_dispatches_everything():
    """When resources_arg is the full list (no scoping), every node
    passes the new gate and reaches the work queue as before.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-1")},
        ("monitors", "mon-1"): {("roles", "role-1")},
        ("roles", "role-1"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        "monitors": {"mon-1": {"id": "mon-1"}},
        "roles": {"role-1": {"id": "role-1"}},
    }
    handler = _make_handler(
        dep_graph,
        source_state,
        resources_arg=["dashboards", "monitors", "roles"],
    )

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    assert enqueued == {
        ("dashboards", "dash-1"),
        ("monitors", "mon-1"),
        ("roles", "role-1"),
    }


@pytest.mark.asyncio
async def test_mixed_batch_skips_some_dispatches_others():
    """A single get_ready() batch containing both a skippable dep-only
    node (type not in resources_arg) AND a dispatchable in-arg node must
    handle both correctly in one for-loop iteration — no short-circuit
    on the first skip.
    """
    dep_graph = {
        ("dashboards", "dash-1"): set(),
        ("monitors", "mon-1"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        "monitors": {"mon-1": {"id": "mon-1"}},
    }
    handler = _make_handler(dep_graph, source_state, resources_arg=["dashboards"])

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    assert ("dashboards", "dash-1") in enqueued, "dispatchable node in same batch must still be dispatched"
    assert ("monitors", "mon-1") not in enqueued, "skippable node in same batch must not be dispatched"


@pytest.mark.asyncio
async def test_run_sorter_awaits_sleep_between_batches_when_all_skipped():
    """run_sorter's outer while-loop must await asyncio.sleep(0) after
    each drain of get_ready(), even when every node took the skip path.

    Directly patch resources_handler.asyncio.sleep and assert it was
    awaited at least once during a skip-only run. This is stronger than
    the earlier concurrent-competing-coroutine version, which could pass
    even if the trailing sleep were removed (the pre-existing
    run_in_executor(None, sorter.is_active) already yields to the loop
    at the top of every iteration).
    """
    dep_graph = {
        # Two nodes, one dependent on the other, so run_sorter runs the
        # outer while-loop across at least two iterations.
        ("monitors", "A"): set(),
        ("monitors", "B"): {("monitors", "A")},
    }
    source_state = {"monitors": {"A": {"id": "A"}, "B": {"id": "B"}}}
    handler = _make_handler(dep_graph, source_state, resources_arg=["dashboards"])

    sleep_mock = AsyncMock()
    with patch.object(rh_module.asyncio, "sleep", sleep_mock):
        await handler.run_sorter()

    assert sleep_mock.await_count >= 1, (
        "run_sorter must await asyncio.sleep(0) between batches — "
        "without it a large skip-only graph would starve the event loop"
    )
    # And confirm it was called with 0 (the yield-to-loop idiom), not
    # some other non-zero delay slipped in accidentally.
    for call in sleep_mock.await_args_list:
        assert call.args == (0,), f"unexpected sleep argument: {call.args!r}"

    # And nothing was dispatched — every node's type was outside resources_arg
    # and destination was pre-populated (the skip conditions).
    assert handler.worker.work_queue.enqueued == [], "no dispatch expected"


@pytest.mark.asyncio
async def test_force_missing_dep_dispatches_out_of_scope_node_missing_from_destination():
    """--force-missing-dependencies flow: a dep whose type is outside
    resources_arg but whose destination state is ABSENT must still be
    dispatched, so run_sorter creates it before its parent runs.

    _force_missing_dep_import_cb imports the missing dep into source
    (via _import_resource) and adds (type, id) as a graph key. Its
    destination state stays empty because the resource doesn't exist
    at destination yet. run_sorter must dispatch it so _apply_resource_cb
    creates it — the whole point of --force-missing-dependencies.

    Regression guard: an earlier version of this fix skipped every
    out-of-scope node unconditionally, which broke this flow and caused
    the parent dashboard to fail with ResourceConnectionError on the
    unmapped monitor.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-1")},
        ("monitors", "mon-1"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        # Monitor imported into source by _force_missing_dep_import_cb:
        "monitors": {"mon-1": {"id": "mon-1"}},
    }
    # Destination has the dashboard but NOT the monitor — the entire
    # point of --force-missing-dependencies is to create such deps.
    destination_state = {"dashboards": {"dash-1": {"id": "dash-1"}}}
    handler = _make_handler(
        dep_graph,
        source_state,
        resources_arg=["dashboards"],
        destination_state=destination_state,
    )

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    assert ("monitors", "mon-1") in enqueued, (
        "out-of-scope dep missing from destination must be dispatched — "
        "--force-missing-dependencies relies on run_sorter creating it"
    )
    assert ("dashboards", "dash-1") in enqueued


@pytest.mark.asyncio
async def test_full_load_mode_dispatches_all_ready_nodes():
    """When --minimize-reads is NOT set (state._minimize_reads=False),
    every resource type is fully loaded up-front and every ready node's
    connect_resources() can succeed. The skip must NOT fire in this
    mode — dispatching every ready node is the pre-existing behavior
    that integration tests depend on.
    """
    dep_graph = {
        ("dashboards", "dash-1"): {("monitors", "mon-1")},
        ("monitors", "mon-1"): set(),
    }
    source_state = {
        "dashboards": {"dash-1": {"id": "dash-1"}},
        "monitors": {"mon-1": {"id": "mon-1"}},
    }
    handler = _make_handler(
        dep_graph,
        source_state,
        resources_arg=["dashboards"],
        minimize_reads=False,
    )

    await handler.run_sorter()

    enqueued = set(handler.worker.work_queue.enqueued)
    # Even though "monitors" is not in resources_arg, we're in full-load
    # mode — the pre-PR behavior of dispatching every ready node stands.
    assert ("dashboards", "dash-1") in enqueued
    assert ("monitors", "mon-1") in enqueued, (
        "full-load mode must dispatch every ready node — the new skip "
        "only activates under minimize-reads to avoid the missing-connections "
        "false-positive that mode can produce"
    )
