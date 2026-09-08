# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.version import __version__


ALL_HELP_CATEGORIES = [
    "Credentials",
    "Resource selection",
    "Storage",
    "Execution",
    "Output",
    "Safety",
    "Advanced",
]


@pytest.mark.parametrize("command", ["import", "sync", "diffs", "migrate", "prune", "reset"])
def test_help_groups_options_without_hiding_them(command):
    result = CliRunner().invoke(cli, [command, "--help"])
    assert result.exit_code == 0
    for category in ALL_HELP_CATEGORIES:
        assert f"{category}:" in result.output
    click_command = cli.commands[command]
    for parameter in click_command.params:
        if getattr(parameter, "opts", None) and not parameter.hidden:
            assert parameter.opts[0] in result.output


@pytest.mark.parametrize("command", ["import", "sync", "diffs", "migrate", "prune", "reset"])
def test_help_options_are_all_assigned_to_a_category(command):
    from datadog_sync.commands.metadata import option_policy

    result = CliRunner().invoke(cli, [command, "--help"])
    assert result.exit_code == 0
    click_command = cli.commands[command]
    for parameter in click_command.params:
        if not getattr(parameter, "opts", None) or parameter.hidden:
            continue
        category = option_policy(parameter.name).category
        assert category in ALL_HELP_CATEGORIES
        section_start = result.output.index(f"{category}:")
        next_sections = [
            result.output.index(f"{other}:", section_start + 1)
            for other in ALL_HELP_CATEGORIES
            if other != category and f"{other}:" in result.output[section_start + 1 :]
        ]
        section_end = min(next_sections) if next_sections else len(result.output)
        assert parameter.opts[0] in result.output[section_start:section_end]


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
