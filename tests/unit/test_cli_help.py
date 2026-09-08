# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.version import __version__


@pytest.mark.parametrize("command", ["import", "sync", "diffs", "migrate", "prune", "reset"])
def test_help_groups_options_without_hiding_them(command):
    result = CliRunner().invoke(cli, [command, "--help"])
    assert result.exit_code == 0
    assert "Credentials:" in result.output
    assert "Resource selection:" in result.output
    assert "Storage:" in result.output
    click_command = cli.commands[command]
    for parameter in click_command.params:
        if getattr(parameter, "opts", None) and not parameter.hidden:
            assert parameter.opts[0] in result.output


def test_root_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("shell, marker", [("bash", "complete"), ("zsh", "compdef"), ("fish", "complete")])
def test_completion_source(shell, marker):
    result = CliRunner().invoke(cli, ["completions", shell])
    assert result.exit_code == 0
    assert marker in result.output
    assert "Starting" not in result.output
