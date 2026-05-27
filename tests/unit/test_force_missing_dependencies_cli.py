# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def test_import_accepts_force_missing_deps(runner):
    result = runner.invoke(cli, ["import", "--force-missing-dependencies", "--validate=false"])
    assert result.exit_code != 2


def test_sync_accepts_force_missing_deps(runner):
    result = runner.invoke(cli, ["sync", "--force-missing-dependencies", "--validate=false"])
    assert result.exit_code != 2


def test_migrate_accepts_force_missing_deps(runner):
    result = runner.invoke(cli, ["migrate", "--force-missing-dependencies", "--validate=false"])
    assert result.exit_code != 2
