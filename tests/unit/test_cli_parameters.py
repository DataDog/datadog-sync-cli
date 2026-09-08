# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import importlib
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from datadog_sync.cli import cli

# datadog_sync.commands.__init__ does `from datadog_sync.commands._import import
# _import`, which rebinds the `commands` package's `_import` attribute to the
# Command object, shadowing the submodule. unittest.mock resolves dotted patch
# targets via getattr traversal (not sys.modules), so patching the string
# "datadog_sync.commands._import.run_cmd" resolves to that Command object
# instead of the module. Importing the submodule explicitly via importlib
# sidesteps the shadowed attribute and gets the real module.
_import_module = importlib.import_module("datadog_sync.commands._import")
# Same shadowing issue applies to datadog_sync.commands.sync (commands/__init__.py
# does `from datadog_sync.commands.sync import sync`).
_sync_module = importlib.import_module("datadog_sync.commands.sync")


def invoke_import(args, input=None):
    with patch.object(_import_module, "run_cmd") as run_cmd:
        result = CliRunner(mix_stderr=False).invoke(cli, ["import", *args], input=input)
    return result, run_cmd


def invoke_sync(args, input=None):
    with patch.object(_sync_module, "run_cmd") as run_cmd:
        result = CliRunner(mix_stderr=False).invoke(cli, ["sync", *args], input=input)
    return result, run_cmd


def test_repeatable_resource_merges_with_legacy_resources():
    result, run_cmd = invoke_import(["--resources", "users,roles", "--resource", "users", "--resource", "dashboards"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["resources"] == "users,roles,dashboards"


def test_negative_validate_flag_wins_over_legacy_default():
    result, run_cmd = invoke_import(["--no-validate"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["validate"] is False


@pytest.mark.parametrize(
    "flag,target_kwarg",
    [
        ("--no-validate", "validate"),
        ("--no-show-progress-bar", "show_progress_bar"),
        ("--no-verify-ssl-certificates", "verify_ssl_certificates"),
        ("--no-verify-ddr-status", "verify_ddr_status"),
        ("--no-send-metrics", "send_metrics"),
    ],
)
def test_negative_boolean_flags_win_over_common_defaults(flag, target_kwarg):
    result, run_cmd = invoke_import([flag])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs[target_kwarg] is False

    result, run_cmd = invoke_import([])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs[target_kwarg] is True


def test_no_create_global_downtime_wins_over_sync_default():
    # --create-global-downtime/--no-create-global-downtime are sync-only options.
    result, run_cmd = invoke_sync(["--no-create-global-downtime"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["create_global_downtime"] is False

    result, run_cmd = invoke_sync([])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["create_global_downtime"] is True


def test_repeatable_worker_limit_reaches_run_cmd_kwargs():
    result, run_cmd = invoke_import(["--worker-limit", "monitors=5", "--worker-limit", "dashboards=10"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["worker_limit"] == ("monitors=5", "dashboards=10")


def test_filter_file_accepts_stdin_json():
    payload = json.dumps([{"type": "monitors", "name": "name", "value": "^prod", "operator": "Not"}])
    result, run_cmd = invoke_import(["--filter-file", "-"], input=payload)
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["filter_file_entries"][0]["type"] == "monitors"


def test_filter_file_and_id_file_cannot_share_stdin():
    result, run_cmd = invoke_import(["--filter-file", "-", "--id-file", "-"], input="[]")
    assert result.exit_code == 2
    assert "cannot both read stdin" in result.stderr
    run_cmd.assert_not_called()
