# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from unittest.mock import patch

from datadog_sync.utils.resources_handler import ResourcesHandler
from datadog_sync.utils.filter import process_filters


@pytest.fixture
def graph_test(config):
    """Provides (handler, config) with full state isolation.

    Saves config.filters, config.filter_operator, config.resources_arg,
    and all source state before each test; restores after.
    """
    saved_filters = config.filters
    saved_operator = config.filter_operator
    saved_resources_arg = config.resources_arg[:]
    saved_source = {}
    for rt in config.resources:
        saved_source[rt] = dict(config.state.source[rt])

    handler = ResourcesHandler(config)

    yield handler, config

    config.filters = saved_filters
    config.filter_operator = saved_operator
    config.resources_arg = saved_resources_arg
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


def setup_filters(config, filter_strings, operator="OR"):
    """Set filters on config from filter string list."""
    config.filters = process_filters(filter_strings)
    config.filter_operator = operator


# ---------------------------------------------------------------------------
# GREEN tests — must pass before AND after the change
# ---------------------------------------------------------------------------


def test_no_filters_all_resources_in_graph(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
                "dash-3": {"id": "dash-3"},
            },
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "Monitor 1"},
                "mon-2": {"id": "mon-2", "name": "Monitor 2"},
            },
        },
        resources_arg=["dashboards", "monitors"],
    )

    graph, _ = handler.get_dependency_graph()

    assert len(graph) == 5
    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") in graph
    assert ("dashboards", "dash-3") in graph
    assert ("monitors", "mon-1") in graph
    assert ("monitors", "mon-2") in graph


def test_filter_matching_all_resources_same_as_no_filter(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=.*"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
                "dash-3": {"id": "dash-3"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert len(graph) == 3


def test_resources_with_no_connections_have_empty_deps(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(
        config,
        {
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "Monitor 1"},
                "mon-2": {"id": "mon-2", "name": "Monitor 2"},
            },
        },
        resources_arg=["monitors"],
    )

    graph, _ = handler.get_dependency_graph()

    assert len(graph) == 2
    assert graph[("monitors", "mon-1")] == set()
    assert graph[("monitors", "mon-2")] == set()


def test_missing_dependencies_detected(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "widgets": [{"definition": {"alert_id": "mon-99"}}]},
            },
        },
        resources_arg=["dashboards", "monitors"],
    )

    _, missing = handler.get_dependency_graph()

    assert ("monitors", "mon-99") in missing


def test_empty_state_returns_empty_graph(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(config, {}, resources_arg=["dashboards"])

    graph, missing = handler.get_dependency_graph()

    assert graph == {}
    assert missing == set()


def test_single_resource_no_deps(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(
        config,
        {"monitors": {"mon-1": {"id": "mon-1", "name": "Monitor 1"}}},
        resources_arg=["monitors"],
    )

    graph, _ = handler.get_dependency_graph()

    assert len(graph) == 1
    assert graph[("monitors", "mon-1")] == set()


def test_cross_type_dep_preserved_when_both_sides_in_graph(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^dash-1$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "widgets": [{"definition": {"alert_id": "mon-1"}}]},
            },
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "Monitor 1"},
            },
        },
        resources_arg=["dashboards", "monitors"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("monitors", "mon-1") in graph
    assert ("monitors", "mon-1") in graph[("dashboards", "dash-1")]


# ---------------------------------------------------------------------------
# RED tests — fail before the change, pass after
# ---------------------------------------------------------------------------


def test_filter_excludes_resources_from_graph(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^dash-1$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
                "dash-3": {"id": "dash-3"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph
    assert ("dashboards", "dash-3") not in graph


def test_graph_size_matches_filtered_count(graph_test):
    handler, config = graph_test
    setup_filters(
        config,
        [
            "Type=dashboards;Name=id;Value=^dash-1$",
            "Type=dashboards;Name=id;Value=^dash-5$",
        ],
    )
    setup_state(
        config,
        {
            "dashboards": {f"dash-{i}": {"id": f"dash-{i}"} for i in range(1, 11)},
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert len(graph) == 2


def test_phantom_deps_stripped_from_graph_values(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "widgets": [{"definition": {"alert_id": "mon-1"}}]},
            },
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "Monitor 1"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("monitors", "mon-1") not in graph[("dashboards", "dash-1")]


def test_filtered_resources_deps_not_computed(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^dash-1$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
            },
        },
        resources_arg=["dashboards"],
    )

    with patch.object(
        ResourcesHandler,
        "_resource_connections",
        wraps=handler._resource_connections,
        return_value=(set(), set()),
    ) as mock_rc:
        graph, _ = handler.get_dependency_graph()

    call_args = [c[0] for c in mock_rc.call_args_list]
    assert ("dashboards", "dash-1") in call_args
    assert ("dashboards", "dash-2") not in call_args


def test_filter_on_one_type_doesnt_affect_other_types(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^dash-1$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
            },
            "monitors": {
                "mon-1": {"id": "mon-1", "name": "Monitor 1"},
                "mon-2": {"id": "mon-2", "name": "Monitor 2"},
            },
        },
        resources_arg=["dashboards", "monitors"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph
    assert ("monitors", "mon-1") in graph
    assert ("monitors", "mon-2") in graph


def test_or_filter_operator_includes_any_match(graph_test):
    handler, config = graph_test
    setup_filters(
        config,
        [
            "Type=dashboards;Name=id;Value=^dash-1$",
            "Type=dashboards;Name=id;Value=^dash-3$",
        ],
        operator="OR",
    )
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
                "dash-3": {"id": "dash-3"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph
    assert ("dashboards", "dash-3") in graph


def test_and_filter_operator_requires_all_match(graph_test):
    handler, config = graph_test
    setup_filters(
        config,
        [
            "Type=dashboards;Name=id;Value=^dash-1$",
            "Type=dashboards;Name=title;Value=^My Dashboard$",
        ],
        operator="AND",
    )
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "title": "My Dashboard"},
                "dash-2": {"id": "dash-2", "title": "My Dashboard"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph


def test_all_resources_filtered_returns_empty_graph(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^nonexistent$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1"},
                "dash-2": {"id": "dash-2"},
            },
        },
        resources_arg=["dashboards"],
    )

    graph, _ = handler.get_dependency_graph()

    assert graph == {}


def test_missing_deps_only_from_filtered_resources(graph_test):
    handler, config = graph_test
    setup_filters(config, ["Type=dashboards;Name=id;Value=^dash-1$"])
    setup_state(
        config,
        {
            "dashboards": {
                "dash-1": {"id": "dash-1", "widgets": [{"definition": {"alert_id": "mon-99"}}]},
                "dash-2": {"id": "dash-2", "widgets": [{"definition": {"alert_id": "mon-88"}}]},
            },
        },
        resources_arg=["dashboards", "monitors"],
    )

    _, missing = handler.get_dependency_graph()

    assert ("monitors", "mon-99") in missing
    assert ("monitors", "mon-88") not in missing
