import os
import re
import json

import pytest

from datadog_sync.constants import RESOURCE_FILE_PATH


RESOURCE_TO_ADD_RE = re.compile("Resource to be added")
RESOURCE_SKIPPED_RE = re.compile("Skipping resource")


@pytest.mark.integration
class BaseResourcesTestClass:
    resource_type = None
    field_to_update = None

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        my_tmpdir = tmpdir_factory.mktemp("tmp")
        os.chdir(my_tmpdir)

    def test_resource_import(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "import")
        assert ret.success

        # Assert at lease one resource is imported
        source_resources, _ = open_resources(self.resource_type)
        assert len(source_resources) > 0

        # Disable skipping on resource connection failure
        # From stdout, count the  number of resources to be added and ensure they match the import len()
        ret = script_runner.run(
            "datadog-sync", f"--resources={self.resource_type}", "--skip-failed-resource-connections=false", "diffs"
        )
        assert ret.success

        num_resources_to_add = len(RESOURCE_TO_ADD_RE.findall(ret.stdout))
        assert num_resources_to_add == len(source_resources)

    def test_resource_sync(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "sync")
        assert ret.success

        # By default, resources  with failed connections are skipped. Hence count number of skipped + success
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(ret.stderr))
        source_resources, destination_resources = open_resources(self.resource_type)

        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)

    def test_resource_update_sync(self, script_runner):
        source_resources, _ = open_resources(self.resource_type)

        # update fields and save the file.
        for resource in source_resources.values():
            resource[self.field_to_update] = str(resource[self.field_to_update]) + "+ updated"
        save_source_resources(self.resource_type, source_resources)

        # assert diff is produced
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "diffs")
        assert ret.stdout
        assert ret.success

        # sync the updated resources
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "sync")
        assert ret.success

        # assert diff is no longer produced
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "diffs")
        assert ret.success
        assert not ret.stdout

        # Assert number of synced and imported resources match
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(ret.stderr))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)

    def test_no_resource_diffs(self, script_runner):
        ret = script_runner.run("datadog-sync", f"--resources={self.resource_type}", "diffs")
        assert not ret.stdout
        assert ret.success

        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(ret.stderr))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)


def save_source_resources(resource_type, resources):
    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    with open(source_path, "w") as f:
        json.dump(resources, f, indent=2)


def open_resources(resource_type):
    source_resources = dict()
    destination_resources = dict()

    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    destination_path = RESOURCE_FILE_PATH.format("destination", resource_type)

    if os.path.exists(source_path):
        with open(source_path, "r") as f:
            source_resources = json.load(f)

    if os.path.exists(destination_path):
        with open(destination_path, "r") as f:
            destination_resources = json.load(f)

    return source_resources, destination_resources
