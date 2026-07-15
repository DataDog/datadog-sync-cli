# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""CLI round-trip test for --drop-unresolvable-principals.

Mirrors test_force_missing_dependencies_cli.py. The flag lives in _diffs_options,
which is attached to sync, diffs, and migrate (the three commands that run
connect_resources). exit_code == 2 is click's "no such option" usage error, so
exit_code != 2 proves the option is recognized by each command.
"""

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def test_sync_accepts_drop_unresolvable_principals(runner):
    result = runner.invoke(cli, ["sync", "--drop-unresolvable-principals", "--validate=false"])
    assert result.exit_code != 2


def test_diffs_accepts_drop_unresolvable_principals(runner):
    result = runner.invoke(cli, ["diffs", "--drop-unresolvable-principals"])
    assert result.exit_code != 2


def test_migrate_accepts_drop_unresolvable_principals(runner):
    result = runner.invoke(cli, ["migrate", "--drop-unresolvable-principals", "--validate=false"])
    assert result.exit_code != 2


def test_import_rejects_drop_unresolvable_principals(runner):
    # The flag is NOT attached to import (import does not run connect_resources).
    result = runner.invoke(cli, ["import", "--drop-unresolvable-principals"])
    assert result.exit_code == 2
