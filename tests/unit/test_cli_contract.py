import importlib
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
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
