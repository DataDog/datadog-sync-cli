import os
import time

from click import pass_context, command
from datadog_sync.constants import SOURCE_RESOURCES_DIR


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Import Datadog resources."""
    logger = ctx.obj.get("logger")
    start = time.time()

    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)

    for resource in ctx.obj.get("resources"):
        logger.info("importing %s", resource.resource_type)
        resource.import_resources()
        logger.info("finished importing %s", resource.resource_type)

    logger.info(f"finished importing resources: {time.time() - start}s")

    if logger.exception_logged:
        exit(1)
