import os
import time
import logging

from click import pass_context, command
from datadog_sync.constants import SOURCE_RESOURCES_DIR


log = logging.getLogger(__name__)


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Import Datadog resources."""
    start = time.time()

    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)

    for resource in ctx.obj.get("resources"):
        log.info("importing %s", resource.resource_type)
        resource.import_resources()
        log.info("finished importing %s", resource.resource_type)

    log.info(f"finished importing resources: {time.time() - start}s")
