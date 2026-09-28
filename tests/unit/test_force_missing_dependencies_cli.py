# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli

# These tests use CliRunner.invoke() to prove click recognizes the flag (exit
# code != 2 is click's "unknown option" usage error). But invoke() does not
# stop after parsing — it executes the whole command. In CI, tox.ini's
# `passenv = DD_SOURCE_*,DD_DESTINATION_*` lets the real workflow secrets
# reach the unit tests, so sync/migrate/import actually run the full pipeline
# against live Datadog orgs, retrying up to DD_HTTP_CLIENT_RETRY_TIMEOUT (300s)
# per call. Measured wall-clock per test: 10-65 min, with the whole suite
# swinging from 10 min to 134 min across identical runs. Skip until these are
# rewritten to short-circuit after parsing (e.g. CliRunner(env=...) with fake
# creds, or a parser-only assertion).
pytestmark = pytest.mark.skip(
    reason="executes the full sync/migrate/import pipeline against live orgs in CI; see module docstring"
)


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
