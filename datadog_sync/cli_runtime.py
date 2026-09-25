# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

import click

from datadog_sync.cli_events import CommandError
from datadog_sync.constants import DD_SYNC_JSON


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
    from datadog_sync.commands.metadata import COMMAND_CAPABILITIES

    return next((arg for arg in args if arg in COMMAND_CAPABILITIES), "")


class GroupedCommand(click.Command):
    def invoke(self, ctx: click.Context) -> Any:
        from datadog_sync.utils.configuration import normalize_kwargs

        ctx.params = normalize_kwargs(ctx.params)
        return super().invoke(ctx)

    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        from datadog_sync.commands.metadata import OPTION_CATEGORIES, option_policy

        grouped: Dict[str, Any] = {}
        for parameter in self.get_params(ctx):
            if not isinstance(parameter, click.Option) or parameter.hidden:
                continue
            record = parameter.get_help_record(ctx)
            if record is not None:
                grouped.setdefault(option_policy(parameter.name).category, []).append(record)
        for category in OPTION_CATEGORIES:
            records = grouped.get(category)
            if records:
                with formatter.section(category):
                    formatter.write_dl(records)


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
                CommandError(
                    command_from_args(raw_args), "invalid_usage", error.format_message(), error.exit_code
                ).emit()
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


@dataclass(frozen=True)
class RootOptions:
    emit_json: bool = False
    read_only: bool = False
    non_interactive: bool = False
    yes: bool = False


def root_options() -> RootOptions:
    context = click.get_current_context(silent=True)
    if context is None:
        return RootOptions()
    values = context.find_root().obj
    return values if isinstance(values, RootOptions) else RootOptions()


def prepare_invocation(command: str, kwargs: Dict[str, Any], root: RootOptions) -> Dict[str, Any]:
    # Imported lazily to avoid a circular import: datadog_sync.commands.metadata
    # is reached through datadog_sync.commands, whose __init__ imports every
    # leaf command module, and those modules import this module for run_cmd.
    from datadog_sync.commands.metadata import COMMAND_CAPABILITIES

    prepared = dict(kwargs)
    prepared["emit_json"] = bool(prepared.get("emit_json") or root.emit_json)
    prepared["read_only"] = root.read_only
    prepared["non_interactive"] = bool(root.non_interactive or prepared["emit_json"])
    prepared["yes"] = root.yes
    capabilities = COMMAND_CAPABILITIES[command]
    if root.read_only and capabilities.api_writes:
        raise click.UsageError(f"{command} can perform Datadog API writes and is blocked by --read-only")
    if root.read_only:
        # sync-cli metrics POST to /api/v2/series, which is an API write.
        prepared["send_metrics"] = False
    cleanup_prompts = command in {"sync", "migrate"} and str(prepared.get("cleanup", "false")).lower() == "true"
    prune_prompts = command == "prune" and not (prepared.get("force") or prepared.get("dry_run"))
    reset_prompts = command == "reset"
    if root.yes:
        if cleanup_prompts:
            prepared["cleanup"] = "Force"
        if command == "prune":
            prepared["force"] = True
    elif prepared["non_interactive"] and (cleanup_prompts or prune_prompts or reset_prompts):
        raise click.UsageError(
            f"{command} would prompt in noninteractive mode; pass --yes or use the command's dry-run option"
        )
    return prepared
