# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command, option, UsageError

from datadog_sync.commands.shared.options import (
    CustomOptionClass,
    common_options,
    source_auth_options,
    storage_options,
)
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import Command


@command(Command.PRUNE.value, short_help="Delete state files for resources no longer in source.")
@source_auth_options
@common_options
@storage_options
@option(
    "--force",
    required=False,
    is_flag=True,
    default=False,
    show_default=True,
    help="Skip the interactive confirmation prompt.",
    cls=CustomOptionClass,
)
@option(
    "--dry-run",
    required=False,
    is_flag=True,
    default=False,
    show_default=True,
    help="Show stale files without deleting them.",
    cls=CustomOptionClass,
)
def prune(**kwargs):
    """Delete per-resource state files for resources no longer present in source.

    Fetches authoritative source IDs from the API, compares against on-disk
    files, and deletes the difference. Requires --resource-per-file. Refuses
    to run with --filters set (would over-prune the filtered-out resources).
    """
    # Hard-require --resources at the CLI layer. The shared common_options
    # --resources is not required=True there because import/sync default to
    # "all types"; for a destructive command, defaulting to all is unsafe.
    if not kwargs.get("resources"):
        raise UsageError("prune requires --resources to be specified explicitly")
    run_cmd(Command.PRUNE, **kwargs)
