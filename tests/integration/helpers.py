import os

import pytest


class BaseResourceTestClass:
    resource_type = None
    resource_filter = None

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        my_tmpdir = tmpdir_factory.mktemp("tmp")
        os.chdir(my_tmpdir)

    def test_resource_import(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "import")
        assert ret.success

    def test_resource_sync(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "sync")
        assert ret.success

    def test_resource_diffs(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "sync")
        assert not ret.stdout
        assert ret.success
