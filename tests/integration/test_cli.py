import os

from click.testing import CliRunner
from datadog_sync.cli import cli
import pytest


@pytest.mark.skipif(os.getenv("DD_INTEGRATION") is None, reason="Set DD_INTEGRATION to run integration tests")
def test_cli(tmpdir, script_runner):
    with tmpdir.as_cwd():
        ret = script_runner.run("datadog-sync", "-v", "import")
        assert ret.success == False
