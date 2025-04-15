# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import logging
import os
import re
import json
import shutil
import glob

from click.testing import CliRunner
import pytest

from datadog_sync.cli import cli


RESOURCE_TO_ADD_RE = re.compile("to be created")
RESOURCE_SKIPPED_RE = re.compile("skipping resource")
RESOURCE_FILE_PATH = "resources/{}/{}.json"


@pytest.mark.vcr
@pytest.mark.integration
class BaseResourcesTestClass:
    resource_type = None
    field_to_update = None
    resources_to_preserve_filter = None
    filter = ""
    force_missing_deps = False
    resource_per_file = False  # Flag to indicate resource-per-file mode

    @staticmethod
    def compute_cleanup_changes(resource_count, num_of_skips):
        """By default we just return the resource count"""
        return resource_count

    @staticmethod
    def compute_import_changes(resource_count, num_of_skips):
        """By default we just return the resource count"""
        return resource_count

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        my_tmpdir = tmpdir_factory.mktemp("tmp")
        os.chdir(my_tmpdir)

    @pytest.fixture(autouse=True, scope="function")
    def setup_and_teardown(self, request, caplog: pytest.LogCaptureFixture):
        """Set up before each test and clean up after each test."""
        # Clean up resources from any previous tests
        self.clean_resource_files()

        # Run the test
        yield

        # Clean up resources respecting the resources_to_preserve_filter
        runner = CliRunner()
        self.test_resource_cleanup(runner, caplog)

        # Clean up local files
        self.clean_resource_files()

    def clean_resource_files(self):
        """Clean up local resource files."""
        for path in ["resources/source", "resources/destination"]:
            if os.path.exists(path):
                shutil.rmtree(path)

    def import_resources(self, runner, caplog):
        """Import resources from Datadog.

        Can be used by test methods to ensure resources exist before testing.
        """
        caplog.set_level(logging.DEBUG)

        if self.resources_to_preserve_filter:
            self.filter = f"{self.resources_to_preserve_filter}"

        cmd = [
            "import",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            f"--filter={self.filter}",
        ]

        if self.resource_per_file:
            cmd.append("--resource-per-file")

        ret = runner.invoke(cli, cmd)
        assert 0 == ret.exit_code
        return ret

    def sync_resources(self, runner, caplog):
        """Sync resources to Datadog.

        Can be used by test methods to ensure resources are synced before testing updates.
        """
        caplog.set_level(logging.DEBUG)

        cmd = [
            "sync",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            "--create-global-downtime=False",
        ]

        if self.filter:
            cmd.append(f"--filter={self.filter}")

        if self.resource_per_file:
            cmd.append("--resource-per-file")

        if self.force_missing_deps:
            cmd.append("--force-missing-dependencies")

        ret = runner.invoke(cli, cmd)
        assert 0 == ret.exit_code
        return ret

    def test_resource_import(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Import the resources
        self.import_resources(runner, caplog)

        # Assert at least one resource is imported
        source_resources, _ = open_resources(self.resource_type)
        assert len(source_resources) > 0

        # Disable skipping on resource connection failure
        # From stdout, count the  number of resources to be added and ensure they match the import len()
        diff_cmd = [
            "diffs",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            "--skip-failed-resource-connections=false",
        ]

        if self.filter:
            diff_cmd.append(f"--filter={self.filter}")

        if self.resource_per_file:
            diff_cmd.append("--resource-per-file")

        ret = runner.invoke(cli, diff_cmd)
        assert 0 == ret.exit_code

        num_resources_to_add = len(RESOURCE_TO_ADD_RE.findall(caplog.text))
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        assert len(source_resources) == self.compute_import_changes(
            num_resources_to_add, num_resources_skipped
        )

    def test_resource_sync(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Import resources if needed
        source_resources, _ = open_resources(self.resource_type)
        if not source_resources:
            self.import_resources(runner, caplog)
        caplog.clear()

        # Perform the sync
        self.sync_resources(runner, caplog)

        # By default, resources with failed connections are skipped. Hence, count number of skipped + success
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (
            len(destination_resources) + num_resources_skipped
        )
        caplog.clear()

    def test_resource_update_sync(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Import resources if needed
        source_resources, destination_resources = open_resources(self.resource_type)
        if not source_resources:
            self.import_resources(runner, caplog)
            caplog.clear()

        # Ensure destination resources exist by syncing if needed
        if not destination_resources:
            self.sync_resources(runner, caplog)
            caplog.clear()

        # Get the updated resources
        source_resources, destination_resources = open_resources(self.resource_type)

        # update fields and save the file.
        for resource in source_resources.values():
            try:
                value = path_lookup(resource, self.field_to_update)
                if isinstance(value, list):
                    value.append("updated")
                if isinstance(value, str):
                    value = value + "updated"
                if isinstance(value, bool):
                    value = not value

                path_update(resource, self.field_to_update, value)
            except Exception as err:
                pytest.fail(err)

        save_source_resources(self.resource_type, source_resources)

        caplog.clear()
        # assert diff is produced
        diff_cmd = [
            "diffs",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
        ]

        if self.filter:
            diff_cmd.append(f"--filter={self.filter}")

        if self.resource_per_file:
            diff_cmd.append("--resource-per-file")

        ret = runner.invoke(cli, diff_cmd)
        assert caplog.text
        assert 0 == ret.exit_code
        caplog.clear()

        # sync the updated resources
        sync_cmd = [
            "sync",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            "--create-global-downtime=False",
        ]

        if self.filter:
            diff_cmd.append(f"--filter={self.filter}")

        if self.resource_per_file:
            sync_cmd.append("--resource-per-file")

        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code
        caplog.clear()

        # assert diff is no longer produced
        ret = runner.invoke(cli, diff_cmd)
        assert 0 == ret.exit_code
        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text

        # Assert number of synced and imported resources match
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)

        assert len(source_resources) == (
            len(destination_resources) + num_resources_skipped
        )
        caplog.clear()

    def test_no_resource_diffs(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Build diffs command
        diff_cmd = [
            "diffs",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
        ]

        if self.filter:
            diff_cmd.append(f"--filter={self.filter}")

        if self.resource_per_file:
            diff_cmd.append("--resource-per-file")

        ret = runner.invoke(cli, diff_cmd)

        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text
        assert 0 == ret.exit_code

        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (
            len(destination_resources) + num_resources_skipped
        )
        caplog.clear()

    def test_resource_cleanup(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        # Remove current source resources
        shutil.rmtree("resources/source", ignore_errors=True)
        os.mkdir("resources/source")

        # create empty source files for resource and dependencies
        file_list = [self.resource_type]
        if self.force_missing_deps:
            file_list += self.dependencies
        for file_name in set(file_list):
            with open(f"resources/source/{file_name}.json", "x", encoding="utf-8") as file_handle:
                file_handle.write("{}")

        # preserve users
        import_cmd = [
            "import",
            "--resources=users",
            "--validate=false",
            "--verify-ddr-status=False",
            "--filter=Type=users;Name=attributes.status;Value=Active",
        ]
        ret = runner.invoke(cli, import_cmd)
        assert 0 == ret.exit_code

        # preserve roles
        import_cmd = [
            "import",
            "--resources=roles",
            "--validate=false",
            "--verify-ddr-status=False",
            "--filter=Type=roles;Name=attributes.user_count;Value=[^0]+;Operator=SubString",
        ]
        ret = runner.invoke(cli, import_cmd)
        assert 0 == ret.exit_code

        # preserve anything else
        if (
            self.resource_type not in ["users", "roles"]
            and self.resources_to_preserve_filter
        ):
            import_cmd = [
                "import",
                f"--resources={self.resource_type}",
                "--validate=false",
                "--verify-ddr-status=False",
                f"--filter={self.resources_to_preserve_filter}",
            ]
            ret = runner.invoke(cli, import_cmd)
            assert 0 == ret.exit_code

        caplog.clear()
        # Sync with cleanup
        sync_cmd = [
            "sync",
            "--validate=false",
            "--verify-ddr-status=False",
            "--cleanup=force",
            "--create-global-downtime=False",
        ]

        if self.filter:
            sync_cmd.append(f"--filter={self.filter}")

        # caution: cleaning up dependencies too!
        if self.force_missing_deps:
            sync_cmd.append("--force-missing-dependencies")
            sync_cmd.append(
                f"--resources={self.resource_type},{','.join(self.dependencies)}"
            )
        else:
            sync_cmd.append(f"--resources={self.resource_type}")

        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code
        caplog.clear()


def save_source_resources(resource_type, resources):
    # Check for individual files first
    individual_files = glob.glob(f"resources/source/{resource_type}.*.json")
    if individual_files:
        # Individual file mode - save each resource to its own file
        for resource_id, resource in resources.items():
            file_path = f"resources/source/{resource_type}.{resource_id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({resource_id: resource}, f, indent=2)
    else:
        # Combined file mode - save all resources to a single file
        source_path = RESOURCE_FILE_PATH.format("source", resource_type)
        with open(source_path, "w", encoding="utf-8") as f:
            json.dump(resources, f, indent=2)


def open_resources(resource_type):
    source_resources = {}
    destination_resources = {}

    # Try to open combined files first
    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    destination_path = RESOURCE_FILE_PATH.format("destination", resource_type)

    # Check for individual source files
    source_files = glob.glob(f"resources/source/{resource_type}.*.json")
    if source_files:
        # Process individual source files
        for file_path in source_files:
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    resource_data = json.load(f)
                    source_resources.update(resource_data)
                except json.decoder.JSONDecodeError as e:
                    pytest.fail(e)
    elif os.path.exists(source_path):
        # Process combined source file
        with open(source_path, "r", encoding="utf-8") as f:
            try:
                source_resources = json.load(f)
            except json.decoder.JSONDecodeError as e:
                pytest.fail(e)

    # Check for individual destination files
    dest_files = glob.glob(f"resources/destination/{resource_type}.*.json")
    if dest_files:
        # Process individual destination files
        for file_path in dest_files:
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    resource_data = json.load(f)
                    destination_resources.update(resource_data)
                except json.decoder.JSONDecodeError as e:
                    pytest.fail(e)
    elif os.path.exists(destination_path):
        # Process combined destination file
        with open(destination_path, "r", encoding="utf-8") as f:
            try:
                destination_resources = json.load(f)
            except json.decoder.JSONDecodeError as e:
                pytest.fail(e)

    return source_resources, destination_resources


def path_lookup(obj, path):
    tmp = obj
    for p in path.split("."):
        if p not in tmp:
            raise ValueError(f"path_lookup error: invalid key {path}")
        tmp = tmp[p]

    return tmp


def path_update(obj, path, new_value):
    path = path.split(".")
    for p in path:
        if p == path[-1]:
            obj[p] = new_value
            break
        if p not in obj:
            raise Exception(f"path_update error: invalid key {path}")

        obj = obj[p]
