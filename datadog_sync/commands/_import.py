from concurrent.futures import ThreadPoolExecutor, wait

from click import pass_context, command

from datadog_sync.utils.helpers import terraformer_import
from datadog_sync.utils.connect_resources import connect_resources


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Sync Datadog resources to destination."""
    # Import resources using terraformer
    terraformer_import(ctx)

    # Run post import processing
    with ThreadPoolExecutor() as executor:
        wait([executor.submit(resource.post_import_processing) for resource in ctx.obj["resources"]])

    # Connect resources
    connect_resources(ctx)
