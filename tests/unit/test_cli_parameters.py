# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import importlib
import json
from unittest.mock import patch

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


def invoke_import(args, input=None):
    with patch.object(_import_module, "run_cmd") as run_cmd:
        result = CliRunner(mix_stderr=False).invoke(cli, ["import", *args], input=input)
    return result, run_cmd


def test_repeatable_resource_merges_with_legacy_resources():
    result, run_cmd = invoke_import(["--resources", "users,roles", "--resource", "users", "--resource", "dashboards"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["resources"] == "users,roles,dashboards"


def test_negative_validate_flag_wins_over_legacy_default():
    result, run_cmd = invoke_import(["--no-validate"])
    assert result.exit_code == 0
    assert run_cmd.call_args.kwargs["validate"] is False


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
