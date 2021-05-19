import os

from click import pass_context, command

from datadog_sync.utils.helpers import terraform_apply_resource, create_values_symlink
from datadog_sync.constants import RESOURCE_DIR


@command("sync", short_help="Sync Datadog resources to destination.")
@pass_context
def sync(ctx):
    """Sync Datadog resources to destination."""

    for resource in ctx.obj.get("resources"):
        if os.path.exists(RESOURCE_DIR.format(resource.resource_name)):
            # Create values symlink
            create_values_symlink(resource.resource_name)
            # Apply resources
            terraform_apply_resource(ctx, resource)
