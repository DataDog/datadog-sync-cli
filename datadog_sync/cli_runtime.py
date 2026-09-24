# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import os
import sys
from typing import Optional, Sequence

import click

from datadog_sync.cli_events import CommandError
from datadog_sync.constants import DD_SYNC_JSON

_COMMANDS = {"import", "sync", "diffs", "migrate", "prune", "reset", "schema", "completions"}


def reset_sigpipe() -> None:
    """Restore default SIGPIPE handling at the process boundary.

    Python installs a SIGPIPE handler that raises ``BrokenPipeError`` instead
    of letting the process die from the signal. That is helpful for library
    use but wrong for a CLI: piping output into ``head`` or ``less`` and
    closing early should silently stop the process, not raise a traceback.
    Restoring ``SIG_DFL`` is a no-op on Windows, which has no SIGPIPE.
    """
    if os.name != "nt":
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _truthy(value: Optional[str]) -> bool:
    return bool(value) and value.lower() in {"1", "true", "yes", "on"}


def structured_output_requested(args: Sequence[str]) -> bool:
    return "--json" in args or _truthy(os.getenv(DD_SYNC_JSON))


def command_from_args(args: Sequence[str]) -> str:
    return next((arg for arg in args if arg in _COMMANDS), "")


class DatadogSyncGroup(click.Group):
    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        reset_sigpipe()
        raw_args = list(args if args is not None else sys.argv[1:])
        try:
            return super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
        except click.exceptions.Exit as exit_result:
            if not standalone_mode:
                raise
            raise SystemExit(exit_result.exit_code)
        except click.Abort:
            if not standalone_mode:
                raise
            if structured_output_requested(raw_args):
                CommandError(command_from_args(raw_args), "interrupted", "Aborted!", 130).emit()
            else:
                click.echo("Aborted!", err=True)
            raise SystemExit(130)
        except click.ClickException as error:
            if not standalone_mode:
                raise
            if structured_output_requested(raw_args):
                CommandError(command_from_args(raw_args), "invalid_usage", error.format_message(), error.exit_code).emit()
            else:
                error.show(file=sys.stderr)
            raise SystemExit(error.exit_code)
        except Exception as error:
            if not standalone_mode:
                raise
            if structured_output_requested(raw_args):
                CommandError(command_from_args(raw_args), "runtime_failure", str(error), 1).emit()
            else:
                click.echo(f"Error: {error}", err=True)
            raise SystemExit(1)
