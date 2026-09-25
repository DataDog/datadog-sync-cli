# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import click
from click.shell_completion import get_completion_class

from datadog_sync.cli_runtime import GroupedCommand


@click.command("completions", cls=GroupedCommand, short_help="Generate shell completion source.")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completions(ctx, shell):
    completion_class = get_completion_class(shell)
    completion = completion_class(ctx.find_root().command, {}, "datadog-sync", "_DATADOG_SYNC_COMPLETE")
    click.echo(completion.source())
