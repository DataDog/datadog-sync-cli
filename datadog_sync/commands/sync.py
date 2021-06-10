import os
import time
import logging

from click import pass_context, command

from datadog_sync.constants import RESOURCE_FILE_PATH, DESTINATION_RESOURCES_DIR


log = logging.getLogger(__name__)


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""
    start = time.time()

    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

    for resource in ctx.obj.get("resources"):
        if os.path.exists(RESOURCE_FILE_PATH.format("source", resource.resource_type)):
            # Apply resources
            log.info("syncing resource: %s", resource.resource_type)
            resource.apply_resources()
            log.info("finished syncing resource: %s", resource.resource_type)

    log.info(f"finished syncing resources: {time.time() - start}s")
