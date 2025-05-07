# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command

from datadog_sync.commands.shared.options import (
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
def sync(**kwargs):
    """Sync Datadog resources to destination."""
    run_cmd(Command.SYNC, **kwargs)
