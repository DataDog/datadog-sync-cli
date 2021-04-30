from click import pass_context, command

from datadog_sync.utils import (
    get_resources,
    terraformer_import,
    terraform_apply_resources,
)


@command("sync", short_help="Sync Datadog resources.")
@pass_context
def sync(ctx):
    """Sync Datadog resources."""

    # Initialize resources
    resources = get_resources(ctx)
    # Import resources using terraformer
    terraformer_import(ctx, resources)
    for resource in resources:
        resource.post_import_processing()

    # Apply resources
    terraform_apply_resources(ctx, resources)
