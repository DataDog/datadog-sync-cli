import os
import time

from click import pass_context, command, option
from datadog_sync.constants import SOURCE_RESOURCES_DIR, DESTINATION_RESOURCES_DIR, SOURCE_ORIGIN


@command("import", short_help="Import Datadog resources.")
@option(
    "--import-existing",
    required=False,
    is_flag=True,
    help="Import and map existing resources from the destination organization. Supported on Import only.",
    default=False
)
@pass_context
def _import(ctx, **kwargs):
    """Import Datadog resources."""
    cfg = ctx.obj.get("config")
    cfg.import_existing = kwargs.get("import_existing"),

    start = time.time()

    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

    for resource_type, resource in cfg.resources.items():
        cfg.logger.info("importing %s", resource_type)
        resource.import_resources()
        resource.write_resources_file(SOURCE_ORIGIN, resource.source_resources)

        if cfg.import_existing:
            resource.open_resources()
            resource.write_resources_file("destination", resource.destination_resources)

        cfg.logger.info("finished importing %s", resource_type)

    cfg.logger.info(f"finished importing resources: {time.time() - start}s")

    if cfg.logger.exception_logged:
        exit(1)
