# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
import importlib
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Command
from datadog_sync.model.logs_indexes import LogsIndexes


@pytest.mark.parametrize(
    "command,module_name,expected_command",
    [
        ("sync", "datadog_sync.commands.sync", Command.SYNC),
        ("migrate", "datadog_sync.commands.migrate", Command.MIGRATE),
    ],
)
def test_cli_accepts_alter_flex_logs_retention_days(command, module_name, expected_command):
    runner = CliRunner(mix_stderr=False)
    command_module = importlib.import_module(module_name)

    with patch.object(command_module, "run_cmd") as mock_run_cmd:
        result = runner.invoke(cli, [command, "--alter-flex-logs-retention-days=90"])

    assert result.exit_code == 0, result.output
    mock_run_cmd.assert_called_once()
    called_command, kwargs = mock_run_cmd.call_args.args[0], mock_run_cmd.call_args.kwargs
    assert called_command == expected_command
    assert kwargs["alter_flex_logs_retention_days"] == 90


def test_cli_rejects_non_integer_flex_logs_retention_days():
    runner = CliRunner(mix_stderr=False)
    sync_module = importlib.import_module("datadog_sync.commands.sync")

    with patch.object(sync_module, "run_cmd") as mock_run_cmd:
        result = runner.invoke(cli, ["sync", "--alter-flex-logs-retention-days=invalid"])

    assert result.exit_code != 0
    mock_run_cmd.assert_not_called()


def test_alter_flex_logs_retention_days_overrides_existing_field(mock_config):
    mock_config.alter_flex_logs_retention_days = 90
    resource = {"name": "index-test", "num_flex_logs_retention_days": 30}

    asyncio.run(LogsIndexes(mock_config).pre_resource_action_hook("index-test", resource))

    assert resource["num_flex_logs_retention_days"] == 90


def test_alter_flex_logs_retention_days_does_not_add_missing_field(mock_config):
    mock_config.alter_flex_logs_retention_days = 90
    resource = {"name": "index-test"}

    asyncio.run(LogsIndexes(mock_config).pre_resource_action_hook("index-test", resource))

    assert "num_flex_logs_retention_days" not in resource


def test_unset_alter_flex_logs_retention_days_preserves_existing_field(mock_config):
    mock_config.alter_flex_logs_retention_days = None
    resource = {"name": "index-test", "num_flex_logs_retention_days": 30}

    asyncio.run(LogsIndexes(mock_config).pre_resource_action_hook("index-test", resource))

    assert resource["num_flex_logs_retention_days"] == 30
