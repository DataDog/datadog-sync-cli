import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, wait

from click import pass_context, command


@command("import", short_help="Import Datadog resources.")
@pass_context
def _import(ctx):
    """Import Datadog resources."""
    now = time.time()
    for resource in ctx.obj.get("resources"):
        resource.import_resources()
    print(f"importing took {now - time.time()}")
