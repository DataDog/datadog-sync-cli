# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import json
from typing import Dict, Optional

import click

from datadog_sync.cli_runtime import GroupedCommand
from datadog_sync.commands.metadata import COMMAND_CAPABILITIES, option_policy


def _option_schema(option: click.Option, compact: bool) -> Dict[str, object]:
    policy = option_policy(option.name)
    item = {
        "name": option.name,
        "flags": list(option.opts) + list(option.secondary_opts),
        "type": option.type.name,
        "required": option.required,
        "multiple": option.multiple,
        "is_flag": option.is_flag,
        "envvar": option.envvar,
        "category": policy.category,
        "sensitive": policy.sensitive,
        "requires": list(policy.requires),
        "conflicts_with": list(policy.conflicts_with),
        "stdin_supported": policy.stdin_supported,
    }
    if not policy.sensitive:
        item["default"] = option.default
    if not compact:
        item["help"] = option.help or ""
    if isinstance(option.type, click.Choice):
        item["choices"] = list(option.type.choices)
    return item


def build_cli_schema(root: click.Group, command_name: Optional[str], compact: bool) -> Dict[str, object]:
    names = [command_name] if command_name else sorted(root.commands)
    unknown = [name for name in names if name not in root.commands]
    if unknown:
        raise click.UsageError(f"unknown command for schema: {unknown[0]}")
    commands = {}
    for name in names:
        command = root.commands[name]
        command_schema = {
            "capabilities": COMMAND_CAPABILITIES[name].to_dict(),
            "options": [_option_schema(p, compact) for p in command.params if isinstance(p, click.Option)],
        }
        if not compact:
            command_schema["help"] = command.help or command.short_help or ""
        commands[name] = command_schema
    return {
        "schema_version": "1.0",
        "root_options": [
            _option_schema(parameter, compact) for parameter in root.params if isinstance(parameter, click.Option)
        ],
        "commands": commands,
    }


@click.command("schema", short_help="Print the machine-readable CLI schema.", cls=GroupedCommand)
@click.argument("command_name", required=False)
@click.option("--compact", is_flag=True)
@click.pass_context
def schema(ctx, command_name, compact):
    document = build_cli_schema(ctx.find_root().command, command_name, compact)
    click.echo(json.dumps(document, indent=None if compact else 2))
