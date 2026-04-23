# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command, option

from datadog_sync.commands.shared.options import (
    CustomOptionClass,
    common_options,
    destination_auth_options,
    diffs_options,
    source_auth_options,
    storage_options,
    sync_options,
)
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import Command


@command(Command.SYNC.value, short_help="Sync Datadog resources to destination.")
@source_auth_options
@destination_auth_options
@common_options
@diffs_options
@sync_options
@storage_options
@option(
    "--minimize-reads",
    required=False,
    is_flag=True,
    default=False,
    show_default=True,
    help="Minimize cloud storage reads by loading only needed resources. "
         "Uses ID-targeted fetching when filters specify exact IDs, "
         "otherwise falls back to type-scoped loading. "
         "Requires --resource-per-file and --resources. "
         "Must not be combined with --cleanup. "
         "Only available on the sync command.",
    cls=CustomOptionClass,
)
def sync(**kwargs):
    """Sync Datadog resources to destination."""
    run_cmd(Command.SYNC, **kwargs)
