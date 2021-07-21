from click import pass_context, command


@command("diffs", short_help="Log resource diffs.")
@pass_context
def diffs(ctx):
    """Log Datadog resources diffs."""
    cfg = ctx.obj.get("config")

    for resource in cfg.resources.values():
        # Skip missing deps resources when outputting diffs
        if resource.resource_type in cfg.missing_deps:
            continue

        resource.check_diffs()

    if cfg.logger.exception_logged:
        exit(1)
