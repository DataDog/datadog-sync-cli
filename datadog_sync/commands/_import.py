import os
import json
from concurrent.futures import ThreadPoolExecutor, wait

from click import pass_context, command

from datadog_sync.utils.helpers import terraformer_import
from datadog_sync.utils.resource_utils import process_resources
from datadog_sync.constants import DEFAULT_STATE_PATH, VALUES_FILE

@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Import Datadog resources."""
    # Create necessary files before import
    if not os.path.exists(DEFAULT_STATE_PATH):
        os.mkdir(DEFAULT_STATE_PATH)
    if not os.path.exists(VALUES_FILE):
        with open(VALUES_FILE, "a+") as f:
            v = {}
            json.dump(v, f, indent=2)

    # Import resources using terraformer
    terraformer_import(ctx)

    # Run post import processing
    with ThreadPoolExecutor() as executor:
        wait([executor.submit(resource.post_import_processing) for resource in ctx.obj["resources"]])

    # Handle resource connections and values generation
    process_resources(ctx)
