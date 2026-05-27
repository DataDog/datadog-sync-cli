# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command, option

from datadog_sync.commands.shared.options import (
    CustomOptionClass,
    common_options,
    destination_auth_options,
    force_missing_dependencies_options,
    source_auth_options,
    storage_options,
)
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import Command


@command(Command.IMPORT.value, short_help="Import Datadog resources.")
@source_auth_options
@destination_auth_options
@common_options
@force_missing_dependencies_options
@storage_options
@option(
    "--skip-state-load",
    required=False,
    is_flag=True,
    default=False,
    show_default=True,
    help="Skip loading prior state from storage. Import discards prior source "
    "state per type and writes fresh API results, so the boot-time read is "
    "dead weight; this flag lets you reclaim that cost on populated buckets. "
    "Requires --resource-per-file and --resources. Not available on other "
    "commands.",
    cls=CustomOptionClass,
)
def _import(**kwargs):
    """Import Datadog resources."""
    run_cmd(Command.IMPORT, **kwargs)
