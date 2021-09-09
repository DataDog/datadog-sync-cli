# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import group, pass_context

from datadog_sync.commands import ALL_COMMANDS


@group()
@pass_context
def cli(ctx):
    """Initialize cli"""
    ctx.obj = dict()


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
