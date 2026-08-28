# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import importlib
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Command


@pytest.mark.parametrize(
    "command,module_name,expected_command",
    [
        ("sync", "datadog_sync.commands.sync", Command.SYNC),
        ("migrate", "datadog_sync.commands.migrate", Command.MIGRATE),
    ],
)
def test_cli_accepts_metric_tag_configuration_metadata_type_repair_flag(
    command, module_name, expected_command
):
    runner = CliRunner(mix_stderr=False)
    command_module = importlib.import_module(module_name)

    with patch.object(command_module, "run_cmd") as mock_run_cmd:
        result = runner.invoke(cli, [command, "--repair-metric-tag-configuration-metadata-type-conflicts"])

    assert result.exit_code == 0, result.output
    mock_run_cmd.assert_called_once()
    called_command, kwargs = mock_run_cmd.call_args.args[0], mock_run_cmd.call_args.kwargs
    assert called_command == expected_command
    assert kwargs["repair_metric_tag_configuration_metadata_type_conflicts"] is True
