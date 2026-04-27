# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from datadog_sync.constants import Origin
from datadog_sync.utils.resources_handler import ResourcesHandler
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource
from datadog_sync.utils.log import Log


@pytest.fixture
def import_test(config):
    """Provides (handler, config) with full state isolation.

    Saves config.filters, config.filter_operator, config.resources_arg,
    config.logger, and all source state before each test; restores after.

    Replaces config.logger with a Log instance so that logger calls with
    custom kwargs (resource_type=, _id=) don't raise TypeError — the
    session-level conftest fixture uses a bare logging.Logger which rejects
    these keyword args (they're custom to the Log class).
    """
    saved_filters = config.filters
    saved_operator = config.filter_operator
    saved_resources_arg = config.resources_arg[:]
    saved_logger = config.logger
    saved_source = {}
    for rt in config.resources:
        saved_source[rt] = dict(config.state.source[rt])

    config.logger = Log(verbose=False)
    handler = ResourcesHandler(config)

    yield handler, config

    config.filters = saved_filters
    config.filter_operator = saved_operator
    config.resources_arg = saved_resources_arg
    config.logger = saved_logger
    for rt in config.resources:
        config.state.source[rt].clear()
        config.state.source[rt].update(saved_source.get(rt, {}))


def setup_state(config, source_resources, resources_arg=None):
    """Populate state.source and optionally set resources_arg.

    Clears ALL resource types in source state first.
    """
    for rt in config.resources:
        config.state.source[rt].clear()
    for rt, resources in source_resources.items():
        for _id, resource in resources.items():
            config.state.source[rt][_id] = resource
    if resources_arg is not None:
        config.resources_arg = resources_arg
    else:
        config.resources_arg = list(source_resources.keys())


# ---------------------------------------------------------------------------
# Cycle A — RED: _discover_missing_dependencies() (method not yet implemented)
# ---------------------------------------------------------------------------


def test_discover_finds_missing_deps(import_test):
    """dashboard_list referencing a dashboard not in source state → detected as missing."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )

    result = handler._discover_missing_dependencies()

    assert ("dashboards", "dash-1") in result


def test_discover_ignores_present_deps(import_test):
    """dashboard_list referencing a dashboard already in source state → not reported as missing."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
            "dashboards": {
                "dash-1": {"id": "dash-1", "title": "Already imported"},
            },
        },
        resources_arg=["dashboard_lists"],
    )

    result = handler._discover_missing_dependencies()

    assert ("dashboards", "dash-1") not in result
    assert result == set()


def test_discover_empty_attr_paths_skipped(import_test):
    """resource_connections with an empty attr-path list produces no missing deps."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )

    r_class = config.resources["dashboard_lists"]
    original_connections = r_class.resource_config.resource_connections
    r_class.resource_config.resource_connections = {"dashboards": []}
    try:
        result = handler._discover_missing_dependencies()
    finally:
        r_class.resource_config.resource_connections = original_connections

    assert result == set()


def test_discover_no_connections(import_test):
    """Resource type with no resource_connections (monitors) → empty missing set."""
    handler, config = import_test
    setup_state(
        config,
        {
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "My Monitor"},
                "mon-2": {"id": "mon-2", "name": "Another Monitor"},
            },
        },
        resources_arg=["monitors"],
    )

    result = handler._discover_missing_dependencies()

    assert result == set()


def test_discover_multiple_resource_types(import_test):
    """Both dashboard_lists→dashboards and dashboards→monitors missing deps are discovered."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
            "dashboards": {
                "dash-2": {
                    "id": "dash-2",
                    "widgets": [{"definition": {"alert_id": "mon-99"}}],
                },
            },
        },
        resources_arg=["dashboard_lists", "dashboards"],
    )

    result = handler._discover_missing_dependencies()

    assert ("dashboards", "dash-1") in result
    assert ("monitors", "mon-99") in result


def test_discover_finds_deps_outside_resources_arg(import_test):
    """Deps on types NOT in resources_arg (but in config.resources) still appear in missing set."""
    handler, config = import_test
    # dashboards is NOT in resources_arg, but dashboard_lists references it
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )
    # dash-1 is absent from source state; dashboards type not in resources_arg

    result = handler._discover_missing_dependencies()

    assert ("dashboards", "dash-1") in result


def test_discover_unknown_dep_type_not_in_resources(import_test):
    """Unknown dep types surfaced by _source_dependencies_for_resource appear in missing set.

    The guard for unknown types lives in _import_missing_dep_cb (import-time callback),
    not in _discover_missing_dependencies itself. This test verifies that discovery
    passes through whatever _source_dependencies_for_resource returns without filtering.
    """
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )
    unknown_deps = {("completely_unknown_type", "some-id-abc")}

    with patch.object(handler, "_source_dependencies_for_resource", return_value=unknown_deps):
        result = handler._discover_missing_dependencies()

    assert ("completely_unknown_type", "some-id-abc") in result


# ---------------------------------------------------------------------------
# Cycle B — RED: _import_missing_dep_cb() (method not yet implemented)
# ---------------------------------------------------------------------------


def _make_http_error(status=404, message="Not Found"):
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.message = message
    return CustomClientHTTPError(mock_response)


def test_import_missing_dep_cb_skips_already_imported(import_test):
    """Callback is a no-op when the dep is already in source state."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "title": "Already here"},
            },
        },
        resources_arg=["dashboards"],
    )

    with patch.object(config.resources["dashboards"], "_import_resource", new=AsyncMock()) as mock_import:
        asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_import.assert_not_called()


def test_import_missing_dep_cb_happy_path(import_test):
    """Successful import emits success and enqueues transitive missing deps."""
    handler, config = import_test
    setup_state(config, {}, resources_arg=["dashboards"])

    mock_worker = MagicMock()
    handler.worker = mock_worker

    async def fake_import_resource(_id):
        config.state.source["dashboards"][_id] = {"id": _id}
        return _id

    transitive_deps = {("monitors", "mon-99")}

    with patch.object(config.resources["dashboards"], "_import_resource", side_effect=fake_import_resource):
        with patch.object(handler, "_source_dependencies_for_resource", return_value=transitive_deps):
            with patch.object(handler, "_emit") as mock_emit:
                asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_emit.assert_called_once_with("dashboards", "dash-1", "import", "success")
    mock_worker.work_queue.put_nowait.assert_called_once_with(("monitors", "mon-99"))


def test_import_missing_dep_cb_skip_resource(import_test):
    """SkipResource → emits skipped and does not proceed to transitive discovery."""
    handler, config = import_test
    setup_state(config, {}, resources_arg=["dashboards"])

    skip_err = SkipResource("dash-1", "dashboards", "not applicable")

    with patch.object(
        config.resources["dashboards"],
        "_import_resource",
        new=AsyncMock(side_effect=skip_err),
    ):
        with patch.object(handler, "_source_dependencies_for_resource") as mock_sdpr:
            with patch.object(handler, "_emit") as mock_emit:
                asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_emit.assert_called_once_with("dashboards", "dash-1", "import", "skipped", reason="SkipResource")
    mock_sdpr.assert_not_called()


def test_import_missing_dep_cb_http_error(import_test):
    """CustomClientHTTPError → emits failure with HTTP status reason."""
    handler, config = import_test
    setup_state(config, {}, resources_arg=["dashboards"])

    http_err = _make_http_error(status=404)

    with patch.object(
        config.resources["dashboards"],
        "_import_resource",
        new=AsyncMock(side_effect=http_err),
    ):
        with patch.object(handler, "_emit") as mock_emit:
            asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_emit.assert_called_once_with("dashboards", "dash-1", "import", "failure", reason="HTTP 404")


def test_import_missing_dep_cb_generic_error(import_test):
    """Unexpected exception → emits failure with exception class name as reason."""
    handler, config = import_test
    setup_state(config, {}, resources_arg=["dashboards"])

    with patch.object(
        config.resources["dashboards"],
        "_import_resource",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with patch.object(handler, "_emit") as mock_emit:
            asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_emit.assert_called_once_with("dashboards", "dash-1", "import", "failure", reason="RuntimeError")


def test_import_missing_dep_cb_unknown_resource_type(import_test):
    """Unknown resource type → logs warning and returns without error."""
    handler, config = import_test

    # "completely_unknown_type" is not registered in config.resources
    assert "completely_unknown_type" not in config.resources

    with patch.object(handler, "_emit") as mock_emit:
        asyncio.run(handler._import_missing_dep_cb(("completely_unknown_type", "some-id")))

    mock_emit.assert_not_called()


def test_import_missing_dep_cb_does_not_apply_filters(import_test):
    """_import_missing_dep_cb bypasses user filters — filter() is never called."""
    handler, config = import_test
    setup_state(config, {}, resources_arg=["dashboards"])

    mock_worker = MagicMock()
    handler.worker = mock_worker

    async def fake_import(_id):
        config.state.source["dashboards"][_id] = {"id": _id}
        return _id

    with patch.object(config.resources["dashboards"], "_import_resource", side_effect=fake_import):
        with patch.object(handler, "_source_dependencies_for_resource", return_value=set()):
            with patch.object(config.resources["dashboards"], "filter") as mock_filter:
                asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_filter.assert_not_called()


def test_circular_dep_does_not_infinite_loop(import_test):
    """Already-imported dep is not re-enqueued, preventing circular import loops.

    Scenario: dash-1 is in source state. When importing dash-2, _resource_connections
    discovers dash-1 as a transitive dep. Because dash-1 is already present, it must
    NOT be enqueued again.
    """
    handler, config = import_test
    # dash-1 already imported
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "title": "Already imported"},
            },
        },
        resources_arg=["dashboards"],
    )

    mock_worker = MagicMock()
    handler.worker = mock_worker

    async def fake_import(_id):
        config.state.source["dashboards"][_id] = {"id": _id}
        return _id

    # Transitive discovery of dash-2 finds dash-1 as a dep — already present
    transitive_deps = {("dashboards", "dash-1")}

    with patch.object(config.resources["dashboards"], "_import_resource", side_effect=fake_import):
        with patch.object(handler, "_source_dependencies_for_resource", return_value=transitive_deps):
            asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-2")))

    # dash-1 must NOT be re-enqueued
    for call in mock_worker.work_queue.put_nowait.call_args_list:
        assert call.args[0] != ("dashboards", "dash-1"), "circular dep was re-enqueued"


# ---------------------------------------------------------------------------
# Cycle C — RED: import_resources() wiring (not yet modified)
# ---------------------------------------------------------------------------


def _make_mock_worker():
    """Return an AsyncMock worker suitable for import_resources flow tests."""
    mock_worker = AsyncMock()
    mock_worker.work_queue = MagicMock()
    return mock_worker


def test_import_resources_calls_discover_when_enabled(import_test):
    """force_missing_dependencies=True → discovery runs and workers are scheduled."""
    handler, config = import_test
    config.force_missing_dependencies = True
    handler.worker = _make_mock_worker()

    missing = {("dashboards", "dash-1")}

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()):
        with patch.object(handler, "_discover_missing_dependencies", return_value=missing) as mock_discover:
            with patch.object(config.state, "dump_state"):
                asyncio.run(handler.import_resources())

    mock_discover.assert_called_once()
    handler.worker.init_workers.assert_called_once()
    handler.worker.work_queue.put_nowait.assert_called_once_with(("dashboards", "dash-1"))
    handler.worker.schedule_workers.assert_called_once()


def test_import_resources_skips_discover_when_disabled(import_test):
    """force_missing_dependencies=False → discovery is never called."""
    handler, config = import_test
    config.force_missing_dependencies = False
    handler.worker = _make_mock_worker()

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()):
        with patch.object(handler, "_discover_missing_dependencies") as mock_discover:
            with patch.object(config.state, "dump_state"):
                asyncio.run(handler.import_resources())

    mock_discover.assert_not_called()


def test_import_resources_empty_missing_set_skips_workers(import_test):
    """When discovery returns empty set, no workers are initialised."""
    handler, config = import_test
    config.force_missing_dependencies = True
    handler.worker = _make_mock_worker()

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()):
        with patch.object(handler, "_discover_missing_dependencies", return_value=set()):
            with patch.object(config.state, "dump_state"):
                asyncio.run(handler.import_resources())

    handler.worker.init_workers.assert_not_called()
    handler.worker.work_queue.put_nowait.assert_not_called()
    handler.worker.schedule_workers.assert_not_called()


def test_import_flag_is_noop_before_wiring(import_test):
    """Baseline: force_missing_dependencies=False does not change import_resources flow.

    Verifies that dump_state is still called exactly once and
    import_resources_without_saving runs as normal.
    """
    handler, config = import_test
    config.force_missing_dependencies = False
    handler.worker = _make_mock_worker()

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()) as mock_iwos:
        with patch.object(config.state, "dump_state") as mock_dump:
            asyncio.run(handler.import_resources())

    mock_iwos.assert_called_once()
    mock_dump.assert_called_once_with(Origin.SOURCE)


def test_import_resources_empty_resources_arg(import_test):
    """force_missing_dependencies=True with empty resources_arg → no crash, discovery still called."""
    handler, config = import_test
    config.force_missing_dependencies = True
    config.resources_arg = []
    handler.worker = _make_mock_worker()

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()):
        with patch.object(handler, "_discover_missing_dependencies", return_value=set()) as mock_discover:
            with patch.object(config.state, "dump_state"):
                asyncio.run(handler.import_resources())

    mock_discover.assert_called_once()
    handler.worker.init_workers.assert_not_called()


def test_import_resources_filter_plus_force(import_test):
    """filter + force_missing_dependencies: discovery uses whatever source state was populated.

    import_resources_without_saving is responsible for applying filters before
    writing to source state. _discover_missing_dependencies then operates only
    on whatever is present in source state — so filtered-out resources are
    naturally excluded from dependency discovery.
    """
    handler, config = import_test
    config.force_missing_dependencies = True
    handler.worker = _make_mock_worker()

    # Simulate import_resources_without_saving populating only the filtered-in resource
    def fake_iwos():
        config.state.source["dashboard_lists"]["dl-1"] = {
            "id": "dl-1",
            "dashboards": [{"id": "dash-1"}],
        }

    missing_from_filtered = {("dashboards", "dash-1")}

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock(side_effect=fake_iwos)):
        with patch.object(
            handler, "_discover_missing_dependencies", return_value=missing_from_filtered
        ) as mock_discover:
            with patch.object(config.state, "dump_state"):
                asyncio.run(handler.import_resources())

    mock_discover.assert_called_once()
    handler.worker.work_queue.put_nowait.assert_called_once_with(("dashboards", "dash-1"))


def test_import_resources_persists_transitive_dep_types(import_test):
    """dump_state is called exactly once, after all discovery (including transitive dep types).

    Even when _import_missing_dep_cb imports dep types outside resources_arg,
    dump_state captures everything in a single atomic call.
    """
    handler, config = import_test
    config.force_missing_dependencies = True
    handler.worker = _make_mock_worker()

    # Simulate _import_missing_dep_cb writing a dep type outside resources_arg
    async def fake_schedule_workers():
        config.state.source["dashboards"]["dash-99"] = {"id": "dash-99"}

    handler.worker.schedule_workers = fake_schedule_workers
    missing = {("dashboards", "dash-99")}

    with patch.object(handler, "import_resources_without_saving", new=AsyncMock()):
        with patch.object(handler, "_discover_missing_dependencies", return_value=missing):
            with patch.object(config.state, "dump_state") as mock_dump:
                asyncio.run(handler.import_resources())

    # dump_state called exactly once, after workers finish
    mock_dump.assert_called_once_with(Origin.SOURCE)
    # dep type outside resources_arg is in source state when dump runs
    assert "dash-99" in config.state.source["dashboards"]


# ---------------------------------------------------------------------------
# Cycle D — RED: integration helper import_resources() wiring
# ---------------------------------------------------------------------------


def test_integration_import_passes_force_missing_deps_flag():
    """When force_missing_deps=True, import_resources() appends --force-missing-dependencies."""
    from tests.integration.helpers import BaseResourcesTestClass

    class TestHelper(BaseResourcesTestClass):
        resource_type = "dashboard_lists"
        force_missing_deps = True

    helper = TestHelper()
    mock_runner = MagicMock()
    mock_ret = MagicMock()
    mock_ret.exit_code = 0
    mock_runner.invoke.return_value = mock_ret
    mock_caplog = MagicMock()
    mock_caplog.set_level = MagicMock()

    helper.import_resources(mock_runner, mock_caplog)

    call_args = mock_runner.invoke.call_args
    cmd = call_args[0][1]  # second positional arg is the cmd list
    assert "--force-missing-dependencies" in cmd


def test_integration_import_omits_flag_when_disabled():
    """When force_missing_deps=False, import_resources() does NOT append --force-missing-dependencies."""
    from tests.integration.helpers import BaseResourcesTestClass

    class TestHelper(BaseResourcesTestClass):
        resource_type = "dashboard_lists"
        force_missing_deps = False

    helper = TestHelper()
    mock_runner = MagicMock()
    mock_ret = MagicMock()
    mock_ret.exit_code = 0
    mock_runner.invoke.return_value = mock_ret
    mock_caplog = MagicMock()
    mock_caplog.set_level = MagicMock()

    helper.import_resources(mock_runner, mock_caplog)

    call_args = mock_runner.invoke.call_args
    cmd = call_args[0][1]
    assert "--force-missing-dependencies" not in cmd


# ---------------------------------------------------------------------------
# Cycle E — RED: Bug #1 — destination state must not leak into discovery
# ---------------------------------------------------------------------------


def test_discover_not_affected_by_destination_state(import_test):
    """Bug #1: dep in destination but NOT source must still be reported missing.

    Root cause: connect_id checks destination first. If dep found there,
    it's not returned as 'failed', so _resource_connections never checks source.
    The new _discover_missing_dependencies must use source-only logic.
    """
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )
    # Put dash-1 in destination (stale) but NOT in source
    config.state.destination["dashboards"]["dash-1"] = {"id": "dest-dash-1"}

    result = handler._discover_missing_dependencies()

    assert ("dashboards", "dash-1") in result


# ---------------------------------------------------------------------------
# Cycle F — RED: Bug #2 — BFS closure walk through already-present source deps
# ---------------------------------------------------------------------------


def test_discover_closure_through_present_deps(import_test):
    """Bug #2: dep already in source state must have ITS own deps discovered.

    dashboard_lists → dashboards (dash-1 present) → monitors (mon-1 missing).
    resources_arg only contains dashboard_lists, but mon-1 must appear in result
    because the BFS walk follows edges through already-present source resources.
    """
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
            "dashboards": {
                "dash-1": {
                    "id": "dash-1",
                    "widgets": [{"definition": {"alert_id": "mon-1"}}],
                },
            },
        },
        resources_arg=["dashboard_lists"],
    )
    # mon-1 is NOT in source state

    result = handler._discover_missing_dependencies()

    assert ("monitors", "mon-1") in result


# ---------------------------------------------------------------------------
# Cycle G — RED: extract_source_ids overrides
# ---------------------------------------------------------------------------


def test_base_extract_source_ids_none_value(import_test):
    """Base: key with None/falsy value → returns None (no deps)."""
    _, config = import_test
    r_class = config.resources["dashboard_lists"]
    result = r_class.extract_source_ids("dashboards", {"dashboards": None}, "dashboards")
    assert result is None


def test_base_extract_source_ids_list(import_test):
    """Base: list value → each element stringified."""
    _, config = import_test
    r_class = config.resources["dashboard_lists"]
    result = r_class.extract_source_ids("dashboards", {"dashboards": [1, 2, 3]}, "dashboards")
    assert result == ["1", "2", "3"]


def test_base_extract_source_ids_scalar(import_test):
    """Base: scalar value → single-element list."""
    _, config = import_test
    r_class = config.resources["dashboard_lists"]
    result = r_class.extract_source_ids("dashboards", {"dashboards": "abc"}, "dashboards")
    assert result == ["abc"]


def test_monitors_extract_source_ids_composite_query(import_test):
    """Monitors: composite monitor query → all referenced monitor IDs extracted."""
    _, config = import_test
    r_class = config.resources["monitors"]
    r_obj = {"type": "composite", "query": "123 && 456"}
    result = r_class.extract_source_ids("query", r_obj, "monitors")
    assert sorted(result) == ["123", "456"]


def test_monitors_extract_source_ids_slo_alert(import_test):
    """Monitors: slo alert query → SLO ID extracted."""
    _, config = import_test
    r_class = config.resources["monitors"]
    r_obj = {"type": "slo alert", "query": 'error_budget("slo-abc-123").over("7d") > 10'}
    result = r_class.extract_source_ids("query", r_obj, "service_level_objectives")
    assert result == ["slo-abc-123"]


def test_monitors_extract_source_ids_regular_query_returns_empty(import_test):
    """Monitors: non-composite query → empty list (not None)."""
    _, config = import_test
    r_class = config.resources["monitors"]
    r_obj = {"type": "metric alert", "query": "avg:system.cpu.user{*} > 80"}
    result = r_class.extract_source_ids("query", r_obj, "monitors")
    assert result == []


def test_monitors_extract_source_ids_principals(import_test):
    """Monitors: principals list → IDs filtered by resource_to_connect type."""
    _, config = import_test
    r_class = config.resources["monitors"]
    r_obj = {"principals": ["user:u-111", "role:r-222", "team:t-333"]}

    assert r_class.extract_source_ids("principals", r_obj, "users") == ["u-111"]
    assert r_class.extract_source_ids("principals", r_obj, "roles") == ["r-222"]
    assert r_class.extract_source_ids("principals", r_obj, "teams") == ["t-333"]


def test_restriction_policies_extract_source_ids_prefixed_id(import_test):
    """RestrictionPolicies: prefixed ID → correct dep ID for matching type, [] for others."""
    _, config = import_test
    r_class = config.resources["restriction_policies"]
    r_obj = {"id": "dashboard:abc-123"}

    assert r_class.extract_source_ids("id", r_obj, "dashboards") == ["abc-123"]
    assert r_class.extract_source_ids("id", r_obj, "service_level_objectives") == []
    assert r_class.extract_source_ids("id", r_obj, "notebooks") == []


def test_restriction_policies_extract_source_ids_principals(import_test):
    """RestrictionPolicies: principals → IDs filtered by resource_to_connect type."""
    _, config = import_test
    r_class = config.resources["restriction_policies"]
    r_obj = {"principals": ["user:u-1", "role:r-2", "team:t-3"]}

    assert r_class.extract_source_ids("principals", r_obj, "users") == ["u-1"]
    assert r_class.extract_source_ids("principals", r_obj, "roles") == ["r-2"]
    assert r_class.extract_source_ids("principals", r_obj, "teams") == ["t-3"]


def test_synthetics_tests_extract_source_ids_pl_filter(import_test):
    """SyntheticsTests: locations list → only private location IDs (pl:xxx) returned."""
    _, config = import_test
    r_class = config.resources["synthetics_tests"]
    # Mix of region strings and private location IDs
    r_obj = {"locations": ["aws:us-east-1", "pl:my-loc-abc123", "azure:eastus", "pl:another-loc-xyz"]}
    result = r_class.extract_source_ids("locations", r_obj, "synthetics_private_locations")
    assert sorted(result) == sorted(["pl:my-loc-abc123", "pl:another-loc-xyz"])


def test_synthetics_tests_extract_source_ids_mobile_latest(import_test):
    """SyntheticsTests: referenceType=latest → extract against synthetics_mobile_applications."""
    _, config = import_test
    r_class = config.resources["synthetics_tests"]
    r_obj = {"referenceId": "app-uuid-xyz", "referenceType": "latest"}
    result = r_class.extract_source_ids("referenceId", r_obj, "synthetics_mobile_applications_versions")
    # For referenceType=latest, should return the ID (delegates to mobile_applications path)
    assert result == ["app-uuid-xyz"]


# ---------------------------------------------------------------------------
# Cycle H — GREEN/GREEN: regression tests for unchanged sync-time path
# ---------------------------------------------------------------------------


def test_dep_in_source_state_exact_match(import_test):
    """_dep_in_source_state: exact key match returns True."""
    handler, config = import_test
    config.state.source["dashboards"]["dash-1"] = {"id": "dash-1"}
    assert handler._dep_in_source_state("dashboards", "dash-1") is True


def test_dep_in_source_state_miss(import_test):
    """_dep_in_source_state: key absent from source → False."""
    handler, config = import_test
    assert handler._dep_in_source_state("dashboards", "nonexistent") is False


def test_dep_in_source_state_synthetics_prefix_match(import_test):
    """_dep_in_source_state: synthetics_tests composite key matched by prefix."""
    handler, config = import_test
    config.state.source["synthetics_tests"]["abc-public#999"] = {"public_id": "abc-public", "monitor_id": 999}
    assert handler._dep_in_source_state("synthetics_tests", "abc-public") is True


def test_dep_in_source_state_synthetics_no_false_positive(import_test):
    """_dep_in_source_state: 'abc' must NOT match 'abcdef#123' — prefix only."""
    handler, config = import_test
    config.state.source["synthetics_tests"]["abcdef#123"] = {"public_id": "abcdef", "monitor_id": 123}
    assert handler._dep_in_source_state("synthetics_tests", "abc") is False


def test_discover_no_connections_empty_result(import_test):
    """Monitors has no resource_connections → discover returns empty set."""
    handler, config = import_test
    setup_state(
        config,
        {
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "My Monitor"},
            },
        },
        resources_arg=["monitors"],
    )
    result = handler._discover_missing_dependencies()
    assert result == set()


def test_discover_present_dep_excluded(import_test):
    """Dep already in source state is excluded from missing set."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
            "dashboards": {
                "dash-1": {"id": "dash-1", "title": "Present"},
            },
        },
        resources_arg=["dashboard_lists"],
    )
    result = handler._discover_missing_dependencies()
    assert ("dashboards", "dash-1") not in result


def test_empty_attr_path_produces_no_deps(import_test):
    """resource_connections with empty attr-path list produces no spurious deps."""
    handler, config = import_test
    setup_state(
        config,
        {
            "dashboard_lists": {
                "dl-1": {"id": "dl-1", "dashboards": [{"id": "dash-1"}]},
            },
        },
        resources_arg=["dashboard_lists"],
    )
    r_class = config.resources["dashboard_lists"]
    original_connections = r_class.resource_config.resource_connections
    r_class.resource_config.resource_connections = {"dashboards": []}
    try:
        result = handler._discover_missing_dependencies()
    finally:
        r_class.resource_config.resource_connections = original_connections
    assert result == set()


# ---------------------------------------------------------------------------
# Cycle I — Enforcement: every custom connect_id must have extract_source_ids
# ---------------------------------------------------------------------------


def test_extract_source_ids_overrides_complete(config):
    """Every resource with non-trivial connect_id also has extract_source_ids override.

    Classes where base extract_source_ids is verified sufficient are allowlisted.
    These include:
    - Classes whose connect_id just calls super() (delegate-to-super)
    - Classes with custom connect_id logic where base extraction still works
    """
    from datadog_sync.utils.base_resource import BaseResource

    # Classes where base extract_source_ids is verified sufficient.
    # Delegate-to-super: their connect_id just calls super().connect_id().
    # Custom-but-safe: custom connect_id logic, but base extract_source_ids works.
    safe_without_override = {
        # Delegate-to-super (connect_id just calls super().connect_id)
        "dashboards",
        "dashboard_lists",
        "powerpacks",
        "downtimes",
        "downtime_schedules",
        "users",
        "authn_mappings",
        "slo_corrections",
        "logs_restriction_queries",
        "logs_archives_order",
        "sensitive_data_scanner_groups_order",
        "sensitive_data_scanner_rules",
        "synthetics_private_locations",
        "teams",
        # Custom connect_id but base extract is sufficient
        "logs_indexes_order",  # Custom remapping; base returns correct raw names
        "synthetics_global_variables",  # Composite key handled by _dep_in_source_state
        "synthetics_test_suites",  # Same as synthetics_global_variables
        "service_level_objectives",  # Empty synthetics_tests paths; base works for monitors
        "logs_pipelines_order",  # Invalid-entry filtering is a sync concern only
    }
    for rt_name, r_class in config.resources.items():
        has_custom_connect = type(r_class).connect_id is not BaseResource.connect_id
        has_custom_extract = type(r_class).extract_source_ids is not BaseResource.extract_source_ids
        if has_custom_connect and not has_custom_extract:
            assert rt_name in safe_without_override, (
                f"{rt_name} has custom connect_id but no extract_source_ids override "
                f"and is not in the verified-safe allowlist"
            )
