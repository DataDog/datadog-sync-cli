from unittest.mock import patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.cli_runtime import RootOptions, prepare_invocation
from datadog_sync.commands.metadata import COMMAND_CAPABILITIES


def test_all_registered_workflows_have_capabilities():
    assert set(COMMAND_CAPABILITIES) >= {"import", "sync", "diffs", "migrate", "reset", "prune"}
    assert COMMAND_CAPABILITIES["sync"].api_writes is True
    assert COMMAND_CAPABILITIES["import"].state_writes is True
    assert COMMAND_CAPABILITIES["import"].api_writes is False


@pytest.mark.parametrize("command", ["sync", "migrate", "reset"])
def test_read_only_rejects_api_write_before_configuration(command):
    with patch("datadog_sync.commands.shared.utils.build_config") as build_config:
        result = CliRunner(mix_stderr=False).invoke(cli, ["--read-only", command])
    assert result.exit_code == 2
    assert "Datadog API writes" in result.stderr
    build_config.assert_not_called()


def test_read_only_allows_import_state_writes():
    prepared = prepare_invocation("import", {}, RootOptions(read_only=True))
    assert prepared["read_only"] is True
    assert COMMAND_CAPABILITIES["import"].state_writes is True


def test_noninteractive_reset_requires_yes_before_configuration():
    with patch("datadog_sync.commands.shared.utils.build_config") as build_config:
        result = CliRunner(mix_stderr=False).invoke(cli, ["--non-interactive", "reset"])
    assert result.exit_code == 2
    assert "--yes" in result.stderr
    build_config.assert_not_called()
