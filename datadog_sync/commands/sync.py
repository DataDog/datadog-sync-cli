import os
import time

from click import pass_context, command, confirm

from datadog_sync.constants import RESOURCE_FILE_PATH, DESTINATION_RESOURCES_DIR


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""
    cfg = ctx.obj.get("config")
    start = time.time()
    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

    force_missing_deps = ctx.obj.get("force_missing_dependencies") or not cfg.missing_deps

    if not force_missing_deps and cfg.missing_deps:
        pretty_missing_deps = "\n".join(["- " + resource for resource in cfg.missing_deps])

        cfg.logger.warning(
            f"Ensure following dependencies are up to date as well:\n{pretty_missing_deps}\nTo auto import and sync dependent resources, you may use the --force-missing-dependencies flag.",
        )

    for resource_type, resource in cfg.resources.items():
        # import missing dependencies if force_missing_deps flag is passed
        if resource_type in cfg.missing_deps and force_missing_deps:
            cfg.logger.info("importing %s", resource.resource_type)
            resource.import_resources()
            resource.write_resources_file("source", resource.source_resources)
            cfg.logger.info("finished importing %s", resource.resource_type)

        # sync resource
        if (
            force_missing_deps
            or resource_type not in cfg.missing_deps
            and os.path.exists(RESOURCE_FILE_PATH.format("source", resource_type))
        ):
            cfg.logger.info("syncing resource: {}".format(resource_type))
            resource.apply_resources()
            resource.write_resources_file("destination", resource.destination_resources)
            cfg.logger.info("finished syncing resource: {}".format(resource_type))

    cfg.logger.info(f"finished syncing resources: {time.time() - start}s")

    if cfg.logger.exception_logged:
        exit(1)
