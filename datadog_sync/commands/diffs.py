from click import pass_context, command


@command("diffs", short_help="Log resource diffs.")
@pass_context
def diffs(ctx):
    """Log Datadog resources diffs."""
    logger = ctx.obj.get("logger")

    for resource in ctx.obj.get("resources"):
        resource.check_diffs()

    if logger.exception_logged:
        exit(1)
