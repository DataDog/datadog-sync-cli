from click import pass_context, command

from datadog_sync.utils.helpers import terraform_apply_resource


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""

    for resource in ctx.obj.get("resources"):
        terraform_apply_resource(ctx, resource)
