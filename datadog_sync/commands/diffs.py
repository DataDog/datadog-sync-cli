from click import pass_context, command

from datadog_sync.shared_options.options import common_options


@command("diffs", short_help="Log resource diffs.")
@common_options
@pass_context
def diffs(ctx, **kwargs):
    """Log Datadog resources diffs."""
    cfg = ctx.obj.get("config")

    for resource in cfg.resources.values():
        resource.open_resources()
        resource.check_diffs()

    if cfg.logger.exception_logged:
        exit(1)
