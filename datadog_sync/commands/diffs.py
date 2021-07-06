from click import command

from datadog_sync.shared.options import common_options, auth_options
from datadog_sync.utils.configuration import build_config


@command("diffs", short_help="Log resource diffs.")
@auth_options
@common_options
def diffs(**kwargs):
    """Log Datadog resources diffs."""
    cfg = build_config(**kwargs)

    for resource in cfg.resources.values():
        resource.open_resources()
        resource.check_diffs()

    if cfg.logger.exception_logged:
        exit(1)
