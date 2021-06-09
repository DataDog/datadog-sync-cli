import os

import pytest


@pytest.mark.skipif(os.getenv("DD_INTEGRATION") is None, reason="Set DD_INTEGRATION to run integration tests")
def test_cli(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "-v", "import")
        assert "error" not in ret.stderr
        assert ret.success
        #  Sync
        ret = script_runner.run("datadog-sync", "-v", "sync")
        assert "error" not in ret.stderr
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs")
        # assert no diffs are produced
        assert not ret.stderr
        assert ret.success


@pytest.mark.skipif(os.getenv("DD_INTEGRATION") is None, reason="Set DD_INTEGRATION to run integration tests")
def test_cli_diff(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "-v", "import")
        assert "error" not in ret.stderr
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs")
        # assert diffs are produced
        assert ret.stderr
        assert ret.success
