# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
from sys import exit

from click import command

from datadog_sync.constants import SOURCE_RESOURCES_DIR, CMD_IMPORT
from datadog_sync.commands.shared.options import common_options, source_auth_options
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resources_handler import ResourcesHandler


@command(CMD_IMPORT, short_help="Import Datadog resources.")
@source_auth_options
@common_options
def _import(**kwargs):
    """Import Datadog resources."""
    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
    cfg = build_config(CMD_IMPORT, **kwargs)

    handler = ResourcesHandler(cfg, False)

    cfg.logger.info(f"Starting import...")

    handler.import_resources()

    cfg.logger.info(f"Finished import")

    if cfg.logger.exception_logged:
        exit(1)
