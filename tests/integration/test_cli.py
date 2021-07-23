# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest


@pytest.mark.vcr
@pytest.mark.integration
def test_cli(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "import")
        assert ret.success
        #  Sync
        ret = script_runner.run("datadog-sync", "sync", "--skip-failed-resource-connections=False")
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs", "--skip-failed-resource-connections=False")
        # assert no diffs are produced
        assert not ret.stdout
        assert ret.success


@pytest.mark.vcr
@pytest.mark.integration
def test_cli_diff(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "import")
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs", "--skip-failed-resource-connections=False")
        # assert diffs are produced
        assert ret.stdout
        assert ret.success
