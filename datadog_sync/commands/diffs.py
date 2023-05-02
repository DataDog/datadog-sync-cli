# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from sys import exit

from click import command

from datadog_sync.commands.shared.options import (
    common_options,
    source_auth_options,
    destination_auth_options,
    non_import_common_options,
)
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resources_handler import ResourcesHandler
from datadog_sync.constants import CMD_DIFFS


@command(CMD_DIFFS, short_help="Log resource diffs.")
@source_auth_options
@destination_auth_options
@common_options
@non_import_common_options
def diffs(**kwargs):
    """Log Datadog resources diffs."""
    cfg = build_config(CMD_DIFFS, **kwargs)
    handler = ResourcesHandler(cfg)

    cfg.logger.info(f"Starting diffs...")

    handler.diffs()

    cfg.logger.info(f"Finished diffs ")

    if cfg.logger.exception_logged:
        exit(1)
