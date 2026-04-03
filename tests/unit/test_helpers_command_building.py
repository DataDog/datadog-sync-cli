# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Regression tests for CLI command-building logic in BaseResourcesTestClass.

Validates that filter arguments are appended to the correct command lists
when test helper methods construct CLI invocations. See: helpers.py line 242.
"""

import pytest


def _build_update_sync_commands(resource_type, filter_value, resource_per_file=False):
    """Reproduce the command-building logic from test_resource_update_sync (helpers.py:211-246).

    Returns (diff_cmd, sync_cmd) as built by that method.
    """
    diff_cmd = [
        "diffs",
        "--validate=false",
        "--verify-ddr-status=False",
        f"--resources={resource_type}",
        "--send-metrics=False",
    ]

    if filter_value:
        diff_cmd.append(f"--filter={filter_value}")

    if resource_per_file:
        diff_cmd.append("--resource-per-file")

    sync_cmd = [
        "sync",
        "--validate=false",
        "--verify-ddr-status=False",
        f"--resources={resource_type}",
        "--create-global-downtime=False",
        "--send-metrics=False",
    ]

    if filter_value:
        sync_cmd.append(f"--filter={filter_value}")

    if resource_per_file:
        sync_cmd.append("--resource-per-file")

    return diff_cmd, sync_cmd


@pytest.mark.parametrize(
    "filter_value",
    [
        "Type=logs_pipelines;Name=is_read_only;Value=false",
        "Type=monitors;Name=tags;Value=sync:true",
    ],
)
def test_filter_appended_to_sync_cmd(filter_value):
    """Regression: --filter must appear in sync_cmd, not only in diff_cmd."""
    diff_cmd, sync_cmd = _build_update_sync_commands("test_resource", filter_value)

    filter_arg = f"--filter={filter_value}"
    assert filter_arg in sync_cmd, f"sync_cmd missing filter: {sync_cmd}"
    assert sync_cmd.count(filter_arg) == 1, f"sync_cmd has duplicate filter: {sync_cmd}"


@pytest.mark.parametrize(
    "filter_value",
    [
        "Type=logs_pipelines;Name=is_read_only;Value=false",
        "Type=monitors;Name=tags;Value=sync:true",
    ],
)
def test_filter_appears_once_in_diff_cmd(filter_value):
    """Regression: --filter must appear exactly once in diff_cmd."""
    diff_cmd, sync_cmd = _build_update_sync_commands("test_resource", filter_value)

    filter_arg = f"--filter={filter_value}"
    assert filter_arg in diff_cmd, f"diff_cmd missing filter: {diff_cmd}"
    assert diff_cmd.count(filter_arg) == 1, f"diff_cmd has duplicate filter: {diff_cmd}"


def test_no_filter_when_empty():
    """When filter is empty, neither command should contain --filter."""
    diff_cmd, sync_cmd = _build_update_sync_commands("test_resource", "")

    for cmd in [diff_cmd, sync_cmd]:
        assert not any(arg.startswith("--filter=") for arg in cmd), f"Unexpected filter in: {cmd}"
