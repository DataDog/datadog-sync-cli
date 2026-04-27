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
    """Unknown dep types returned by _resource_connections appear in the result set.

    The guard for unknown types lives in _import_missing_dep_cb (import-time callback),
    not in _discover_missing_dependencies itself. This test verifies that discovery
    passes through whatever _resource_connections returns without filtering.
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
    unknown_missing = {("completely_unknown_type", "some-id-abc")}

    with patch.object(handler, "_resource_connections", return_value=(set(), unknown_missing)):
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

    transitive_missing = {("monitors", "mon-99")}

    with patch.object(config.resources["dashboards"], "_import_resource", side_effect=fake_import_resource):
        with patch.object(handler, "_resource_connections", return_value=(set(), transitive_missing)):
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
        with patch.object(handler, "_resource_connections") as mock_rc:
            with patch.object(handler, "_emit") as mock_emit:
                asyncio.run(handler._import_missing_dep_cb(("dashboards", "dash-1")))

    mock_emit.assert_called_once_with("dashboards", "dash-1", "import", "skipped", reason="SkipResource")
    mock_rc.assert_not_called()


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
        with patch.object(handler, "_resource_connections", return_value=(set(), set())):
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
    transitive_missing = {("dashboards", "dash-1")}

    with patch.object(config.resources["dashboards"], "_import_resource", side_effect=fake_import):
        with patch.object(handler, "_resource_connections", return_value=(set(), transitive_missing)):
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
