import os
import time

from click import pass_context, command

from datadog_sync.constants import RESOURCE_FILE_PATH, DESTINATION_RESOURCES_DIR


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""
    logger = ctx.obj.get("logger")
    start = time.time()
    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

    for resource in ctx.obj.get("resources"):
        if os.path.exists(RESOURCE_FILE_PATH.format("source", resource.resource_type)):
            # Apply resources
            logger.info("syncing resource: {}".format(resource.resource_type))
            resource.apply_resources()
            logger.info("finished syncing resource: {}".format(resource.resource_type))

    logger.info(f"finished syncing resources: {time.time() - start}s")

    if logger.exception_logged:
        exit(1)
