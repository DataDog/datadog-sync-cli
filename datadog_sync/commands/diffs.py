from click import pass_context, command


@command("diffs", short_help="Log resource diffs.")
@pass_context
def diffs(ctx):
    """Log Datadog resources diffs."""
    for resource in ctx.obj.get("resources"):
        resource.check_diffs()
