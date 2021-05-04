from click import pass_context, command

from datadog_sync.utils import (
    terraformer_import,
)


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Sync Datadog resources to destination."""
    # Import resources using terraformer
    terraformer_import(ctx)
    for resource in ctx.obj["resources"]:
        resource.post_import_processing()
