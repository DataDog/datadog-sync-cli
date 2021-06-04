from click import pass_context, command


@command("diff", short_help="Log resource diffs.")
@pass_context
def diff(ctx):
    """Log Datadog resources diffs."""
    for resource in ctx.obj.get("resources"):
        resource.check_diffs()
