# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import sys

import click

from datadog_sync.cli_runtime import DatadogSyncGroup, RootOptions
from datadog_sync.commands import ALL_COMMANDS
from datadog_sync.version import __version__


@click.group(cls=DatadogSyncGroup)
@click.version_option(version=__version__)
@click.option("--json", "root_emit_json", is_flag=True, help="Emit an NDJSON event stream.")
@click.option(
    "--read-only",
    is_flag=True,
    help="Reject commands that can write to Datadog APIs and disable sync-cli metrics.",
)
@click.option("--non-interactive", is_flag=True, help="Reject invocations that would prompt.")
@click.option("--yes", is_flag=True, help="Approve destructive confirmations noninteractively.")
@click.pass_context
def cli(ctx, root_emit_json, read_only, non_interactive, yes):
    """Initialize cli"""
    ctx.obj = RootOptions(root_emit_json, read_only, non_interactive, yes)


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)


# Invoke cli manually if using executable
if getattr(sys, "frozen", False):
    import multiprocessing

    multiprocessing.freeze_support()

    cli(sys.argv[1:])
