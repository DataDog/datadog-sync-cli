import asyncio
from sys import exit

from datadog_sync.constants import Command
from datadog_sync.utils.configuration import Configuration, build_config
from datadog_sync.utils.resources_handler import ResourcesHandler


def run_cmd(cmd: Command, **kwargs):
    # Build config
    cfg = build_config(cmd, **kwargs)

    # Initiate resources handler
    handler = ResourcesHandler(cfg)

    try:
        asyncio.run(run_cmd_async(cfg, handler, cmd))
    except KeyboardInterrupt:
        cfg.logger.error("Process interrupted by user")
        if cmd in [Command.SYNC, Command.MIGRATE, Command.RESET]:
            cfg.logger.info("Writing synced resources to disk before exit...")
            cfg.state.dump_state()
            exit(0)

    if cfg.logger.exception_logged:
        exit(1)


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
        else:
            cfg.logger.error(f"Command {cmd.value} not found")
            return

        cfg.logger.info(f"Finished {cmd.value}")
    finally:
        await cfg.exit_async()
        # Disable progress bar so it doesn't interfere with the logger
        if handler.worker and handler.worker.pbar:
            handler.worker.pbar.disable = True
