# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os

from click import command, option

from datadog_sync.constants import DESTINATION_RESOURCES_DIR, CMD_SYNC
from datadog_sync.commands.shared.options import (
    common_options,
    source_auth_options,
    destination_auth_options,
    non_import_common_options,
)
from datadog_sync.utils.resources_handler import ResourceHandler
from datadog_sync.utils.configuration import build_config


@command(CMD_SYNC, short_help="Sync Datadog resources to destination.")
@source_auth_options
@destination_auth_options
@common_options
@non_import_common_options
@option(
    "--force-missing-dependencies",
    required=False,
    is_flag=True,
    default=False,
    help="Force importing and syncing resources that could be potential dependencies to the requested resources.",
)
def sync(**kwargs):
    """Sync Datadog resources to destination."""
    cfg = build_config(CMD_SYNC, **kwargs)
    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

    handler = ResourceHandler(cfg)
    handler.apply_resources()

    if cfg.logger.exception_logged:
        exit(1)
