from click import pass_context, command

from datadog_sync.utils import (
    terraform_apply_resources,
)


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""
    terraform_apply_resources(ctx)
