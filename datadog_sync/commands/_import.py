import os
import time

from click import command, option

from datadog_sync.constants import SOURCE_RESOURCES_DIR
from datadog_sync.commands.shared.options import common_options, source_auth_options
from datadog_sync.utils.configuration import build_config


@command("import", short_help="Import Datadog resources.")
@source_auth_options
@common_options
@option("--filter", required=False, help="Filter imported resources.", multiple=True)
def _import(**kwargs):
    """Import Datadog resources."""
    cfg = build_config(**kwargs)
    start = time.time()

    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)

    for resource_type, resource in cfg.resources.items():
        if resource_type in cfg.missing_deps:
            continue

        cfg.logger.info("importing %s", resource_type)
        resource.source_resources = {}  # Reset source resources on import
        resource.import_resources()
        resource.write_resources_file("source", resource.source_resources)
        cfg.logger.info("finished importing %s", resource_type)

    cfg.logger.info(f"finished importing resources: {time.time() - start}s")

    if cfg.logger.exception_logged:
        exit(1)
