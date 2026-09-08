# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import json

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.commands.metadata import COMMAND_CAPABILITIES


def schema(*args):
    result = CliRunner().invoke(cli, ["schema", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# Mirrors the capability table documented in
# docs/superpowers/specs/2026-09-07-agent-ready-cli-upgrades-design.md (Command Capability Model).
EXPECTED_CAPABILITIES = {
    "import": {
        "api_reads": True,
        "api_writes": False,
        "state_reads": True,
        "state_writes": True,
        "may_prompt": False,
        "supports_plan": False,
        "plan_command": None,
    },
    "sync": {
        "api_reads": True,
        "api_writes": True,
        "state_reads": True,
        "state_writes": True,
        "may_prompt": True,
        "supports_plan": True,
        "plan_command": "diffs",
    },
    "diffs": {
        "api_reads": True,
        "api_writes": False,
        "state_reads": True,
        "state_writes": False,
        "may_prompt": False,
        "supports_plan": True,
        "plan_command": "diffs",
    },
    "migrate": {
        "api_reads": True,
        "api_writes": True,
        "state_reads": True,
        "state_writes": True,
        "may_prompt": True,
        "supports_plan": True,
        "plan_command": "diffs",
    },
    "reset": {
        "api_reads": True,
        "api_writes": True,
        "state_reads": True,
        "state_writes": True,
        "may_prompt": True,
        "supports_plan": False,
        "plan_command": None,
    },
    "prune": {
        "api_reads": True,
        "api_writes": False,
        "state_reads": True,
        "state_writes": True,
        "may_prompt": True,
        "supports_plan": True,
        "plan_command": "prune --dry-run",
    },
    "schema": {
        "api_reads": False,
        "api_writes": False,
        "state_reads": False,
        "state_writes": False,
        "may_prompt": False,
        "supports_plan": False,
        "plan_command": None,
    },
    "completions": {
        "api_reads": False,
        "api_writes": False,
        "state_reads": False,
        "state_writes": False,
        "may_prompt": False,
        "supports_plan": False,
        "plan_command": None,
    },
}


def test_expected_capabilities_cover_every_cli_command():
    assert set(EXPECTED_CAPABILITIES) == set(cli.commands)


def test_command_capabilities_mapping_covers_every_cli_command():
    assert set(COMMAND_CAPABILITIES) == set(cli.commands)


@pytest.mark.parametrize("command_name", sorted(EXPECTED_CAPABILITIES))
def test_command_capabilities_match_documented_plan(command_name):
    assert COMMAND_CAPABILITIES[command_name].to_dict() == EXPECTED_CAPABILITIES[command_name]


@pytest.mark.parametrize("command_name", sorted(EXPECTED_CAPABILITIES))
def test_schema_capabilities_match_documented_plan(command_name):
    command = schema(command_name)["commands"][command_name]
    assert command["capabilities"] == EXPECTED_CAPABILITIES[command_name]


def test_schema_is_offline_and_lists_every_workflow():
    document = schema()
    assert document["schema_version"] == "1.0"
    assert set(document["commands"]) >= {"import", "sync", "diffs", "migrate", "prune", "reset"}
    assert {option["name"] for option in document["root_options"]} >= {
        "root_emit_json",
        "read_only",
        "non_interactive",
        "yes",
    }


def test_sync_schema_includes_capabilities_and_option_contract():
    command = schema("sync")["commands"]["sync"]
    assert command["capabilities"]["api_writes"] is True
    validate = next(option for option in command["options"] if option["name"] == "validate")
    assert validate["type"] == "boolean"
    assert validate["default"] is True
    assert validate["envvar"] == "DD_VALIDATE"


def test_schema_marks_secrets_without_values(monkeypatch):
    monkeypatch.setenv("DD_SOURCE_API_KEY", "must-not-appear")
    text = json.dumps(schema("import"))
    assert "must-not-appear" not in text
    option = next(o for o in json.loads(text)["commands"]["import"]["options"] if o["name"] == "source_api_key")
    assert option["sensitive"] is True
    assert "default" not in option


ALLOWED_CATEGORIES = {
    "Credentials",
    "Resource selection",
    "Storage",
    "Execution",
    "Output",
    "Safety",
    "Advanced",
}


def test_every_schema_option_has_an_approved_category():
    document = schema()
    options = document["root_options"] + [
        option for command in document["commands"].values() for option in command["options"]
    ]
    assert options
    assert {option["category"] for option in options} <= ALLOWED_CATEGORIES


def test_compact_schema_keeps_construction_fields():
    document = schema("sync", "--compact")
    command = document["commands"]["sync"]
    assert "help" not in command
    assert command["capabilities"]["api_writes"] is True
    for option in command["options"]:
        assert {"flags", "type", "required", "multiple"} <= option.keys()
        assert "help" not in option
