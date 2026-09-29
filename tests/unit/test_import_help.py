# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from click.testing import CliRunner

from datadog_sync.cli import cli


def _normalized(text):
    return " ".join(text.split()).lower()


def test_import_help_states_direction():
    result = CliRunner().invoke(cli, ["import", "--help"])
    assert result.exit_code == 0
    output = _normalized(result.output)
    assert "into local state" in output
    assert "does not create, update, or delete resources in any datadog organization" in output
    assert "--no-send-metrics" in output


def test_root_help_summarizes_import_direction():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "changes no org resources" in _normalized(result.output)
