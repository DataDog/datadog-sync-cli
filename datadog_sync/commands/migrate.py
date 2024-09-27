# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command

from datadog_sync.commands.shared.options import (
    common_options,
    destination_auth_options,
    diffs_common_options,
    source_auth_options,
    sync_common_options,
)
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import Command


@command(Command.MIGRATE.value, short_help="Migrate Datadog resources from one datacenter to another.")
@source_auth_options
@destination_auth_options
@common_options
@diffs_common_options
@sync_common_options
def migrate(**kwargs):
    """Migrate Datadog resources from one datqaacenter to another."""
    run_cmd(Command.MIGRATE, **kwargs)
