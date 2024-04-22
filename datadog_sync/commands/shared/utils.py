import asyncio
import os

from datadog_sync.constants import DESTINATION_RESOURCES_DIR, SOURCE_RESOURCES_DIR, Command
from datadog_sync.utils.configuration import Configuration, build_config
from datadog_sync.utils.resources_handler import ResourcesHandler


def run_cmd(cmd: Command, **kwargs):
    # Build config
    cfg = build_config(cmd, **kwargs)

    # Initiate resources handler
    handler = ResourcesHandler(cfg)

    asyncio.run(run_cmd_async(cfg, handler, cmd))

    if cfg.logger.exception_logged:
        exit(1)


async def run_cmd_async(cfg: Configuration, handler: ResourcesHandler, cmd: Command):
    # Initiate async items
    await cfg.init_async(cmd)
    await handler.init_async()

    cfg.logger.info(f"Starting {cmd.value}...")

    # Run specific handler
    if cmd == Command.IMPORT:
        os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
        await handler.import_resources()
    elif cmd == Command.SYNC:
        os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)
        await handler.apply_resources()
    elif cmd == Command.DIFFS:
        await handler.diffs()
    else:
        cfg.logger.error(f"Command {cmd.value} not found")
        exit(1)

    cfg.logger.info(f"Finished {cmd.value}")

    # Cleanup session before exit
    await cfg.exit_async_cleanup()

    if cfg.logger.exception_logged:
        exit(1)
