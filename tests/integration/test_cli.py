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
