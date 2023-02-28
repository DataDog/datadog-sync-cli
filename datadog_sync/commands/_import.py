# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os

from click import command

from datadog_sync.constants import SOURCE_RESOURCES_DIR, CMD_IMPORT
from datadog_sync.commands.shared.options import common_options, source_auth_options
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resources_handler import import_resources


@command(CMD_IMPORT, short_help="Import Datadog resources.")
@source_auth_options
@common_options
def _import(**kwargs):
    """Import Datadog resources."""
    cfg = build_config(CMD_IMPORT, **kwargs)
    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
    import_resources(cfg)

    if cfg.logger.exception_logged:
        exit(1)
