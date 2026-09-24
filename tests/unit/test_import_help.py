# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from click.testing import CliRunner

from datadog_sync.cli import cli


def test_import_help_states_direction():
    result = CliRunner().invoke(cli, ["import", "--help"])
    assert result.exit_code == 0
    assert "into local state" in result.output
    assert "writes to no" in result.output.lower()
