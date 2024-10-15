# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import command, option

from datadog_sync.commands.shared.options import (
    CustomOptionClass,
    common_options,
    destination_auth_options,
)
from datadog_sync.commands.shared.utils import run_cmd
from datadog_sync.constants import Command


@command(Command.RESET.value, short_help="WARNING: Reset Datadog resources by deleting them.")
@destination_auth_options
@common_options
@option(
    "--do-not-backup",
    required=False,
    is_flag=True,
    default=False,
    help="Skip backing up the destination you are about to reset. Not recommended.",
    cls=CustomOptionClass,
)
def reset(**kwargs):
    """WARNING: Reset Datadog resources by deleting them."""
    run_cmd(Command.RESET, **kwargs)
