import os

from click import pass_context, command

from datadog_sync.constants import RESOURCE_FILE_PATH


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""
    for resource in ctx.obj.get("resources"):
        if os.path.exists(RESOURCE_FILE_PATH.format("source", resource.resource_type)):
            # Apply resources
            resource.apply_resources()
