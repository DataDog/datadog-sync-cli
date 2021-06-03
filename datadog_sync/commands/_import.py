import time
import logging

from click import pass_context, command


log = logging.getLogger("__name__")


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Import Datadog resources."""
    now = time.time()
    for resource in ctx.obj.get("resources"):
        log.info("importing %s", resource.resource_type)
        resource.import_resources()
        log.info("finished importing %s", resource.resource_type)

    log.info(f"finished importing resources: {now - time.time()}")
