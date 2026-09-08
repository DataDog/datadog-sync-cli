import importlib
import json
import logging
from collections import Counter
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.cli_events import InvocationSummary
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import LOGGER_NAME, Command

# datadog_sync.commands.__init__ does `from datadog_sync.commands._import import
# _import`, which rebinds the `commands` package's `_import` attribute to the
# Command object, shadowing the submodule. unittest.mock resolves dotted patch
# targets via getattr traversal (not sys.modules), so patching the string
# "datadog_sync.commands._import.run_cmd" resolves to that Command object
# instead of the module. Importing the submodule explicitly via importlib
# sidesteps the shadowed attribute and gets the real module.
_import_module = importlib.import_module("datadog_sync.commands._import")


@pytest.fixture(autouse=True)
def _restore_shared_logger_state():
    """Log(emit_json=True) mutates the process-wide LOGGER_NAME logger
    (propagate=False, custom handler) with no teardown. --json invocations
    here would otherwise leak that state into unrelated tests that run
    afterward and rely on caplog's propagation-based capture."""
    logger = logging.getLogger(LOGGER_NAME)
    propagate, handlers, level = logger.propagate, list(logger.handlers), logger.level
    yield
    logger.propagate = propagate
    logger.handlers = handlers
    logger.level = level


def test_help_keeps_success_exit():
    result = CliRunner(mix_stderr=False).invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert result.stderr == ""


def test_invalid_bool_is_human_usage_error():
    result = CliRunner(mix_stderr=False).invoke(cli, ["import", "--validate", "maybe"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Invalid value" in result.stderr


def test_invalid_bool_is_structured_usage_error():
    result = CliRunner(mix_stderr=False).invoke(cli, ["import", "--json", "--validate", "maybe"])
    assert result.exit_code == 2
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "type": "error",
        "command": "import",
        "status": "error",
        "error_code": "invalid_usage",
        "message": "Invalid value for '--validate': 'maybe' is not a valid boolean.",
        "exit_code": 2,
    }


def test_unexpected_callback_failure_is_structured_runtime_error():
    with patch.object(_import_module, "run_cmd", side_effect=RuntimeError("boom")):
        result = CliRunner(mix_stderr=False).invoke(cli, ["import", "--json"])
    assert result.exit_code == 1
    assert result.stderr == ""
    event = json.loads(result.stdout)
    assert event["type"] == "error"
    assert event["error_code"] == "runtime_failure"
    assert event["exit_code"] == 1


def test_invalid_configuration_is_structured_usage_error():
    result = CliRunner(mix_stderr=False).invoke(
        cli,
        ["import", "--json", "--resources", "monitors", "--id-file", "-"],
        input="not-json",
    )
    assert result.exit_code == 2
    assert result.stderr == ""
    event = json.loads(result.stdout)
    assert event["type"] == "error"
    assert event["error_code"] == "invalid_usage"
    assert event["exit_code"] == 2


def test_keyboard_interrupt_exits_130_after_sync_state_dump():
    cfg = MagicMock()
    cfg.emit_json = False
    cfg.logger.exception_logged = False
    handler = MagicMock()
    with patch("datadog_sync.commands.shared.utils.build_config", return_value=cfg), patch(
        "datadog_sync.commands.shared.utils.ResourcesHandler", return_value=handler
    ), patch("datadog_sync.commands.shared.utils.asyncio.run", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc:
            run_cmd(Command.SYNC)
    assert exc.value.code == 130
    cfg.state.dump_state.assert_called_once_with()


def test_summary_serializes_nonzero_counts_only():
    event = InvocationSummary("sync", "partial_failure", Counter(success=2, failure=1, skipped=0), 15, 1)
    assert event.to_dict() == {
        "type": "summary",
        "command": "sync",
        "status": "partial_failure",
        "counts": {"success": 2, "failure": 1},
        "duration_ms": 15,
        "exit_code": 1,
    }


def test_prune_without_resource_per_file_is_structured_usage_error():
    """Regression for the run_cmd refactor bug: a click.UsageError raised
    during async execution (e.g. prune's --resource-per-file precondition,
    surfaced here via a mocked run_cmd_async to avoid real network calls
    from Configuration's connectivity/validation step) must propagate out
    of run_cmd untouched, exit 2, and emit exactly the ClickException
    "error" event from cli_runtime.py -- never an InvocationSummary
    "summary" event."""

    async def _raise_usage_error(*_args, **_kwargs):
        raise click.UsageError("prune requires --resource-per-file")

    with patch(
        "datadog_sync.commands.shared.utils.run_cmd_async",
        new=_raise_usage_error,
    ):
        result = CliRunner(mix_stderr=False).invoke(
            cli, ["prune", "--json", "--validate=false", "--resources", "monitors", "--force"]
        )
    assert result.exit_code == 2
    assert result.stderr == ""
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    # build_config emits its own "log" events (auth/state setup) before
    # run_cmd_async is ever invoked; the assertion that matters is that no
    # "summary" event was emitted, and that exactly one "error" event was.
    assert not any(event["type"] == "summary" for event in events)
    error_events = [event for event in events if event["type"] == "error"]
    assert len(error_events) == 1
    event = error_events[0]
    assert event["error_code"] == "invalid_usage"
    assert event["exit_code"] == 2
    assert "--resource-per-file" in event["message"]


def test_run_cmd_propagates_click_usage_error_without_summary():
    """Directly exercise run_cmd with a mocked run_cmd_async raising
    click.UsageError -- verifies run_cmd itself re-raises rather than
    converting to a failure summary, and does not emit an InvocationSummary."""
    cfg = MagicMock()
    cfg.emit_json = True
    cfg.fatal_error = False
    cfg.logger.exception_logged = False
    handler = MagicMock()
    handler.outcome_counts = Counter()
    with patch("datadog_sync.commands.shared.utils.build_config", return_value=cfg), patch(
        "datadog_sync.commands.shared.utils.ResourcesHandler", return_value=handler
    ), patch(
        "datadog_sync.commands.shared.utils.asyncio.run",
        side_effect=click.UsageError("prune requires --resource-per-file"),
    ), patch(
        "datadog_sync.cli_events.write_ndjson_line"
    ) as write_line:
        with pytest.raises(click.UsageError):
            run_cmd(Command.PRUNE, emit_json=True)
    write_line.assert_not_called()


def test_json_runtime_emits_exactly_one_terminal_summary():
    cfg = MagicMock()
    cfg.emit_json = True
    cfg.fatal_error = False
    cfg.logger.exception_logged = False
    handler = MagicMock()
    handler.outcome_counts = Counter(success=2)
    with patch("datadog_sync.commands.shared.utils.build_config", return_value=cfg), patch(
        "datadog_sync.commands.shared.utils.ResourcesHandler", return_value=handler
    ), patch("datadog_sync.commands.shared.utils.run_cmd_async", return_value=object()), patch(
        "datadog_sync.commands.shared.utils.asyncio.run"
    ), patch(
        "datadog_sync.cli_events.write_ndjson_line"
    ) as write_line:
        run_cmd(Command.DIFFS, emit_json=True)
    assert write_line.call_count == 1
    assert write_line.call_args.args[0]["type"] == "summary"
    assert write_line.call_args.args[0]["counts"] == {"success": 2}
