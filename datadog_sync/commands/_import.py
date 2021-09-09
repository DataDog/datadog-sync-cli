# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os

from click import command, pass_context

from datadog_sync.commands.shared.utils import handle_interrupt
from datadog_sync.constants import SOURCE_RESOURCES_DIR
from datadog_sync.commands.shared.options import common_options, source_auth_options
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resources_handler import import_resources


@command("import", short_help="Import Datadog resources.")
@source_auth_options
@common_options
@pass_context
@handle_interrupt
def _import(ctx, **kwargs):
    """Import Datadog resources."""
    cfg = build_config(**kwargs)
    ctx.obj["config"] = cfg
    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
    import_resources(cfg)

    if cfg.logger.exception_logged:
        exit(1)
