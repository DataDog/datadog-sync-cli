import asyncio
from time import monotonic

import click

from datadog_sync.cli_events import CommandError, InvocationSummary
from datadog_sync.cli_runtime import prepare_invocation, root_options
from datadog_sync.constants import Command
from datadog_sync.utils.configuration import Configuration, build_config
from datadog_sync.utils.resources_handler import ResourcesHandler


def run_cmd(cmd: Command, **kwargs):
    started = monotonic()

    root = root_options()
    kwargs = prepare_invocation(cmd.value, kwargs, root)

    # Build config
    cfg = build_config(cmd, **kwargs)

    # Initiate resources handler
    handler = ResourcesHandler(cfg)

    exit_code = 0
    status = "success"
    try:
        asyncio.run(run_cmd_async(cfg, handler, cmd))
    except KeyboardInterrupt:
        cfg.logger.error("Process interrupted by user")
        if cmd in [Command.SYNC, Command.MIGRATE, Command.RESET]:
            cfg.logger.info("Writing synced resources to disk before exit...")
            cfg.state.dump_state()
        exit_code = 130
        status = "interrupted"
    except SystemExit as error:
        exit_code = int(error.code or 0)
        status = "failure" if exit_code else "success"
    except click.ClickException:
        raise
    except click.Abort:
        # Abort subclasses Exception, not ClickException; keep the exit 130
        # / interrupted mapping the CLI boundary would otherwise apply.
        exit_code = 130
        status = "interrupted"
        _report_error(cfg, cmd, "interrupted", "Aborted!", exit_code)
    except Exception as error:
        cfg.logger.exception("Command failed unexpectedly")
        exit_code = 1
        status = "failure"
        _report_error(cfg, cmd, "runtime_failure", str(error), exit_code, echo=False)
    else:
        if cfg.logger.exception_logged or cfg.fatal_error:
            exit_code = 1
            status = "failure"
        elif handler.outcome_counts.get("failure") or handler.outcome_counts.get("partial"):
            status = "partial_failure"

    if cfg.emit_json:
        InvocationSummary(
            command=cmd.value,
            status=status,
            counts=handler.outcome_counts,
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            exit_code=exit_code,
        ).emit()

    if exit_code:
        raise SystemExit(exit_code)


def _report_error(cfg: Configuration, cmd: Command, error_code: str, message: str, exit_code: int, echo=True):
    # Emitted here rather than at the CLI boundary so the error precedes the
    # terminal summary event.
    if cfg.emit_json:
        CommandError(cmd.value, error_code, message, exit_code).emit()
    elif echo:
        click.echo(message, err=True)


async def run_cmd_async(cfg: Configuration, handler: ResourcesHandler, cmd: Command):
    try:
        # Initiate async items
        await cfg.init_async(cmd)
        await handler.init_async()

        cfg.logger.info(f"Starting {cmd.value}...")

        # Run specific handler
        if cmd == Command.IMPORT:
            await handler.import_resources()
        elif cmd == Command.SYNC:
            await handler.apply_resources()
        elif cmd == Command.DIFFS:
            await handler.diffs()
        elif cmd == Command.MIGRATE:
            await handler.import_resources()
            await handler.apply_resources()
        elif cmd == Command.RESET:
            await handler.reset()
        elif cmd == Command.PRUNE:
            await handler.prune()
        else:
            cfg.logger.error(f"Command {cmd.value} not found")
            return

        cfg.logger.info(f"Finished {cmd.value}")
    finally:
        await cfg.exit_async()
        # Disable progress bar so it doesn't interfere with the logger
        if handler.worker and handler.worker.pbar:
            handler.worker.pbar.disable = True
