from unittest.mock import MagicMock, patch

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


@pytest.mark.parametrize("command", ["sync", "migrate"])
def test_yes_forces_cleanup_when_cleanup_requested(command):
    prepared = prepare_invocation(command, {"cleanup": "true"}, RootOptions(yes=True))
    assert prepared["cleanup"] == "Force"


@pytest.mark.parametrize("command", ["sync", "migrate"])
def test_yes_does_not_force_cleanup_when_cleanup_not_requested(command):
    prepared = prepare_invocation(command, {"cleanup": "false"}, RootOptions(yes=True))
    assert prepared["cleanup"] == "false"


def test_yes_forces_prune():
    prepared = prepare_invocation("prune", {}, RootOptions(yes=True))
    assert prepared["force"] is True


def test_yes_allows_reset_noninteractively():
    prepared = prepare_invocation("reset", {}, RootOptions(yes=True, non_interactive=True))
    assert prepared["yes"] is True
    assert prepared["non_interactive"] is True


def test_yes_allows_prune_noninteractively_without_prior_force():
    prepared = prepare_invocation("prune", {}, RootOptions(yes=True, non_interactive=True))
    assert prepared["force"] is True
    assert prepared["non_interactive"] is True


@pytest.mark.parametrize("command", ["import", "diffs", "prune"])
def test_read_only_does_not_block_exempt_commands_via_cli(command):
    cfg = MagicMock()
    cfg.emit_json = False
    cfg.logger.exception_logged = False
    handler = MagicMock()
    handler.outcome_counts = {}
    args = ["--read-only", command]
    if command == "prune":
        args += ["--resources", "monitors", "--force"]
    with patch("datadog_sync.commands.shared.utils.build_config", return_value=cfg) as build_config, patch(
        "datadog_sync.commands.shared.utils.ResourcesHandler", return_value=handler
    ), patch("datadog_sync.commands.shared.utils.asyncio.run"):
        result = CliRunner(mix_stderr=False).invoke(cli, args)
    assert "is blocked by --read-only" not in result.stderr
    assert result.exit_code != 2
    build_config.assert_called_once()


@pytest.mark.parametrize("command", ["import", "diffs", "prune"])
def test_read_only_disables_metrics_even_when_requested(command):
    """send_metric POSTs to /api/v2/series, so --read-only must turn metrics
    off for the commands it allows, including an explicit --send-metrics true."""
    prepared = prepare_invocation(command, {"send_metrics": True, "force": True}, RootOptions(read_only=True))
    assert prepared["send_metrics"] is False


def test_metrics_setting_unchanged_without_read_only():
    prepared = prepare_invocation("import", {"send_metrics": True}, RootOptions())
    assert prepared["send_metrics"] is True


def test_read_only_import_builds_config_with_metrics_disabled():
    cfg = MagicMock()
    cfg.emit_json = False
    cfg.logger.exception_logged = False
    cfg.fatal_error = False
    handler = MagicMock()
    handler.outcome_counts = {}
    with patch("datadog_sync.commands.shared.utils.build_config", return_value=cfg) as build_config, patch(
        "datadog_sync.commands.shared.utils.ResourcesHandler", return_value=handler
    ), patch("datadog_sync.commands.shared.utils.asyncio.run"):
        result = CliRunner(mix_stderr=False).invoke(cli, ["--read-only", "import"])
    assert result.exit_code == 0
    assert build_config.call_args.kwargs["send_metrics"] is False
