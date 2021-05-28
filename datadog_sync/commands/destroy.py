import os

from click import pass_context, command

from datadog_sync.utils.helpers import terraform_destroy_resource
from datadog_sync.constants import RESOURCE_DIR


@command("destroy", short_help="Destroy synced resources in Destination organization.")
@pass_context
def destroy(ctx):
    """Sync Datadog resources to destination."""
    for resource in ctx.obj.get("resources"):
        if os.path.exists(RESOURCE_DIR.format(resource.resource_type)):
            # Apply resources
            terraform_destroy_resource(ctx, resource)
