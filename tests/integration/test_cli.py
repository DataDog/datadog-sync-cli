import pytest


@pytest.mark.integration
def test_cli(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "import", "-v")
        assert ret.success
        #  Sync
        ret = script_runner.run("datadog-sync", "sync", "-v")
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs")
        # assert no diffs are produced
        assert not ret.stdout
        assert ret.success


@pytest.mark.integration
def test_cli_diff(tmpdir, script_runner):
    with tmpdir.as_cwd():
        # Import
        ret = script_runner.run("datadog-sync", "import", "-v")
        assert ret.success
        # Check diff
        ret = script_runner.run("datadog-sync", "diffs")
        # assert diffs are produced
        assert ret.stdout
        assert ret.success
