# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.cli import cli


@pytest.mark.vcr
@pytest.mark.integration
def test_cli(tmpdir, runner):
    with tmpdir.as_cwd():
        # Import
        ret = runner.invoke(cli, ["import"])
        assert 0 == ret.exit_code
        #  Sync
        ret = runner.invoke(cli, ["sync", "--skip-failed-resource-connections=False"])
        assert 0 == ret.exit_code
        # Check diff
        ret = runner.invoke(cli, ["diffs", "--skip-failed-resource-connections=False"])
        # assert no diffs are produced
        assert not ret.output
        assert 0 == ret.exit_code


@pytest.mark.vcr
@pytest.mark.integration
def test_cli_diff(tmpdir, runner):
    with tmpdir.as_cwd():
        # Import
        ret = runner.invoke(cli, ["import"])
        assert 0 == ret.exit_code
        # Check diff
        ret = runner.invoke(cli, ["diffs", "--skip-failed-resource-connections=False"])
        # assert diffs are produced
        assert ret.output
        assert 0 == ret.exit_code
