from click import pass_context, command

import logging

log = logging.getLogger(__name__)


@command("diffs", short_help="Log resource diffs.")
@pass_context
def diffs(ctx):
    """Log Datadog resources diffs."""
    cfg = ctx.obj.get("config")

    for resource in cfg.resources:
        resource.check_diffs()

    if cfg.logger.exception_logged:
        exit(1)
