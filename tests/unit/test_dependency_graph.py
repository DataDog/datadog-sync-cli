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

    graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    _, missing, _ = handler.get_dependency_graph()

    assert ("monitors", "mon-99") in missing


def test_empty_state_returns_empty_graph(graph_test):
    handler, config = graph_test
    config.filters = {}
    setup_state(config, {}, resources_arg=["dashboards"])

    graph, missing, _ = handler.get_dependency_graph()

    assert graph == {}
    assert missing == set()


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

    graph, _, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("monitors", "mon-1") in graph
    assert ("monitors", "mon-1") in graph[("dashboards", "dash-1")]


def test_cross_type_phantom_deps_preserved(graph_test):
    """Deps on out-of-scope resource types are preserved as phantom nodes
    so TopologicalSorter yields them first, ensuring correct ordering."""
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

    graph, _, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("monitors", "mon-1") in graph[("dashboards", "dash-1")]


# ---------------------------------------------------------------------------
# RED tests — fail before the change, pass after
# ---------------------------------------------------------------------------
# case_management_attributes <-> case_management_types ordering
# (added with the case_management_attributes resource)
# ---------------------------------------------------------------------------


def _seed_case_management_state(config, seed_destination=True):
    """Seed source + destination state with realistic identity domains.

    Source state is keyed by source ids; destination state is keyed by SOURCE
    ids too (state files are source-ID-keyed) but the stored bodies hold
    DESTINATION API ids — the exact shape production state has after a sync.

    seed_destination=False models a FIRST sync (parent type not yet synced),
    which is the state where the creation graph must carry the dependency
    edge (edges are failed connections — deps not yet in destination state).
    """
    src_type = {
        "id": "type-src-0000",
        "type": "case_type",
        "attributes": {"name": "case_type_test_src"},
    }
    src_attr = {
        "id": "attr-src-0000",
        "type": "custom_attribute",
        "attributes": {
            "case_type_id": "type-src-0000",
            "key": "attr_key_test_src",
            "display_name": "Test Attribute",
            "is_multi": False,
            "type": "TEXT",
        },
    }
    # Destination bodies: destination API ids under source-keyed state entries.
    dst_type_entry = {
        "id": "type-dst-0000",
        "type": "case_type",
        "attributes": {"name": "case_type_test_src"},
    }
    dst_attr_entry = {
        "id": "attr-dst-0000",
        "type": "custom_attribute",
        "attributes": {
            "case_type_id": "type-dst-0000",
            "key": "attr_key_test_src",
            "display_name": "Test Attribute",
            "is_multi": False,
            "type": "TEXT",
        },
    }

    setup_state(
        config,
        {
            "case_management_types": {"type-src-0000": src_type},
            "case_management_attributes": {"attr-src-0000": src_attr},
        },
        resources_arg=["case_management_attributes", "case_management_types"],
    )
    if seed_destination:
        config.state.destination["case_management_types"]["type-src-0000"] = dst_type_entry
        config.state.destination["case_management_attributes"]["attr-src-0000"] = dst_attr_entry


def test_case_management_attributes_depend_on_types_in_creation_graph(graph_test):
    """The creation graph must yield the parent case type before the
    attribute that references it (drives run_sorter tiering). Seeds a FIRST
    sync (parent not yet in destination) — creation edges are failed
    connections, i.e. dependencies not yet synced."""
    handler, config = graph_test

    _seed_case_management_state(config, seed_destination=False)

    graph, _, _ = handler.get_dependency_graph()

    assert ("case_management_types", "type-src-0000") in graph[("case_management_attributes", "attr-src-0000")]


def test_case_management_cleanup_graph_deletes_attributes_before_types(graph_test):
    """The REVERSE cleanup graph must record that the parent case type can
    only be deleted after the attribute that references it.

    Identity domains are realistic: destination state is keyed by source ids
    while the stored attribute body references the DESTINATION type id, so
    the reverse edge only appears when get_cleanup_dependency_graph
    translates the payload id back to the source-state key.
    """
    handler, config = graph_test

    _seed_case_management_state(config)

    cleanup_resources = {
        ("case_management_types", "type-src-0000"): None,
        ("case_management_attributes", "attr-src-0000"): None,
    }
    reverse_graph = handler.get_cleanup_dependency_graph(cleanup_resources)

    assert reverse_graph[("case_management_types", "type-src-0000")] == {
        ("case_management_attributes", "attr-src-0000")
    }
    assert reverse_graph[("case_management_attributes", "attr-src-0000")] == set()


def test_cleanup_sorter_execution_orders_deletions(graph_test):
    """Execution-level proof: drive a real TopologicalSorter over the cleanup
    graph (the same graph run_cleanup_sorter consumes) and assert the
    attribute's delete is dispatched before the type's."""
    from graphlib import TopologicalSorter

    handler, config = graph_test

    _seed_case_management_state(config)

    cleanup_resources = {
        ("case_management_types", "type-src-0000"): None,
        ("case_management_attributes", "attr-src-0000"): None,
    }
    reverse_graph = handler.get_cleanup_dependency_graph(cleanup_resources)

    sorter = TopologicalSorter(reverse_graph)
    sorter.prepare()
    deletion_order = []
    while sorter.is_active():
        for node in sorter.get_ready():
            deletion_order.append(node)
            sorter.done(node)

    assert deletion_order == [
        ("case_management_attributes", "attr-src-0000"),
        ("case_management_types", "type-src-0000"),
    ]


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

    graph, _, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph
    assert ("dashboards", "dash-3") not in graph


def test_filtered_out_deps_stripped_from_graph_values(graph_test):
    """Deps pointing to resources excluded by --filter are stripped,
    preventing phantom nodes for resources we don't want to process."""
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

    graph, _, _ = handler.get_dependency_graph()

    assert ("dashboards", "dash-1") in graph
    assert ("dashboards", "dash-2") not in graph
    # dash-2 was filtered out, so any dep referencing it should be stripped
    for deps in graph.values():
        assert ("dashboards", "dash-2") not in deps


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
    ) as mock_rc:
        graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    graph, _, _ = handler.get_dependency_graph()

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

    _, missing, _ = handler.get_dependency_graph()

    assert ("monitors", "mon-99") in missing
    assert ("monitors", "mon-88") not in missing


def _seed_dashboard_monitor_state(config, dashboard_body, monitor_entries):
    setup_state(
        config,
        {
            "dashboards": {"dash-src": dashboard_body},
            "monitors": {"mon-src": {"id": "mon-src", "name": "m"}},
        },
        resources_arg=["dashboards", "monitors"],
    )
    config.state.destination["dashboards"]["dash-src"] = dashboard_body
    for key, entry in monitor_entries.items():
        if entry is None:
            config.state.destination["monitors"].pop(key, None)
        else:
            config.state.destination["monitors"][key] = entry


def test_cleanup_graph_preserves_direct_source_key_references(graph_test):
    """Pre-fix behavior preserved: a body referencing the dependency by the
    SAME id as the cleanup key (source-key style) keeps its edge without any
    translation."""
    handler, config = graph_test

    # Dashboard body references monitor id "mon-src" directly.
    dashboard = {"id": "dash-src", "widgets": [{"definition": {"alert_id": "mon-src"}}]}
    _seed_dashboard_monitor_state(
        config,
        dashboard,
        {"mon-src": {"id": "mon-src", "name": "m"}},
    )

    cleanup_resources = {
        ("dashboards", "dash-src"): None,
        ("monitors", "mon-src"): None,
    }
    reverse_graph = handler.get_cleanup_dependency_graph(cleanup_resources)

    assert reverse_graph[("monitors", "mon-src")] == {("dashboards", "dash-src")}


def test_cleanup_graph_translates_remapped_destination_ids_generic_pair(graph_test):
    """Genericity proof on an established connected pair (dashboards ->
    monitors): the dashboard body references the monitor by its DESTINATION
    id while the monitor's cleanup key is its source id; the translation
    index must bridge the domains."""
    handler, config = graph_test

    # Dashboard body references the monitor's DESTINATION id.
    dashboard = {"id": "dash-src", "widgets": [{"definition": {"alert_id": "mon-dst"}}]}
    # Monitor destination state: keyed by source id, body holds destination id.
    _seed_dashboard_monitor_state(
        config,
        dashboard,
        {"mon-src": {"id": "mon-dst", "name": "m"}},
    )

    cleanup_resources = {
        ("dashboards", "dash-src"): None,
        ("monitors", "mon-src"): None,
    }
    reverse_graph = handler.get_cleanup_dependency_graph(cleanup_resources)

    assert reverse_graph[("monitors", "mon-src")] == {("dashboards", "dash-src")}


def test_cleanup_graph_missing_and_duplicate_payload_ids_no_crash(graph_test, caplog):
    """Destination entries with a missing id are skipped; duplicate payload
    ids are excluded from the index with a warning — never a crash, never an
    arbitrary pick. The dependent's edge is omitted when translation is
    ambiguous or impossible."""
    handler, config = graph_test

    dashboard = {"id": "dash-src", "widgets": [{"definition": {"alert_id": "mon-dst"}}]}
    _seed_dashboard_monitor_state(
        config,
        dashboard,
        {
            "mon-src": {"id": "mon-dst", "name": "m"},  # same payload id ...
            "mon-other": {"id": "mon-dst", "name": "m2"},  # ... claimed twice
            "mon-noid": {"name": "no id"},  # missing id: skipped
        },
    )

    cleanup_resources = {
        ("dashboards", "dash-src"): None,
        ("monitors", "mon-src"): None,
        ("monitors", "mon-other"): None,
        ("monitors", "mon-noid"): None,
    }
    reverse_graph = handler.get_cleanup_dependency_graph(cleanup_resources)

    # Ambiguous payload id: no edge, no crash.
    assert reverse_graph.get(("monitors", "mon-src"), set()) == set()
    assert reverse_graph.get(("monitors", "mon-other"), set()) == set()
    # The duplicate payload id produced a warning.
    import logging as _logging

    duplicate_warnings = [
        r for r in caplog.records if r.levelno == _logging.WARNING and "duplicate destination id" in r.message
    ]
    assert duplicate_warnings, "duplicate payload ids must be warned, not silently resolved"
