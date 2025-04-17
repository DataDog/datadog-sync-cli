# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import logging
import os
import re
import json
import shutil
from time import sleep
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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

        # Import resources
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
        # something is going on server side that requires a little delay before running this
        if self.resource_type == "metric_tag_configurations":
            sleep(5)

        # Import resources
        self.import_resources(runner, caplog)
        caplog.clear()

        # Ensure destination resources exist by syncing
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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
            "--send-metrics=False",
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
                "--send-metrics=False",
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
            "--send-metrics=False",
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

    def test_resource_import_per_file(self, runner, caplog):
        """Test that importing resources with --resource-per-file creates individual files."""
        caplog.set_level(logging.DEBUG)
        self.resource_per_file = True

        # Import with resource-per-file flag
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
                "--send-metrics=False",
            ],
        )
        assert 0 == ret.exit_code

        # Verify files were created as individual files
        combined_file = f"resources/source/{self.resource_type}.json"
        assert not os.path.exists(combined_file), f"Combined file {combined_file} should not exist"

        individual_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        assert len(individual_files) > 0, f"No individual files found for {self.resource_type}"

        # Verify each file has exactly one resource with the ID matching the filename
        for file_name in individual_files:
            with open(file_name, "r") as f:
                content = json.load(f)
                assert len(content) == 1, f"File {file_name} should contain exactly one resource"

                # Extract the resource ID from the filename
                prefix = f"{self.resource_type}."
                prefix_index = file_name.find(prefix)
                suffix = ".json"
                file_id = file_name[prefix_index+len(prefix):-len(suffix)]
                resource_id = list(content.keys())[0]
                assert file_id == resource_id.replace(":","."), f"Resource with ID {resource_id} should have a file with {file_id}"

        # Run diffs to ensure everything is recognized properly
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--skip-failed-resource-connections=false",
                "--send-metrics=False",
            ],
        )
        assert 0 == ret.exit_code

        # Count resources that should be added
        num_resources_to_add = len(re.compile("to be created").findall(caplog.text))
        num_resources_skipped = len(re.compile("skipping resource").findall(caplog.text))

        # Total number of individual files should match number of resources to add plus skipped
        assert len(individual_files) == self.compute_import_changes(num_resources_to_add, num_resources_skipped)

    def test_resource_sync_per_file(self, runner, caplog):
        """Test that syncing resources with --resource-per-file creates individual files."""
        caplog.set_level(logging.DEBUG)
        self.resource_per_file = True

        # First import resources to work with
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
                "--send-metrics=False",
            ],
        )
        assert 0 == ret.exit_code

        # Sync with resource-per-file flag
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
                "--force-missing-dependencies",
                "--create-global-downtime=False",
                "--send-metrics=False",
            ],
        )
        assert 0 == ret.exit_code

        # Verify source files stay as individual files
        source_combined_file = f"resources/source/{self.resource_type}.json"
        assert not os.path.exists(source_combined_file), f"Combined file {source_combined_file} should not exist"

        source_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        assert len(source_files) > 0, f"No individual files found for {self.resource_type} in source"

        # Verify destination files were created as individual files
        dest_combined_file = f"resources/destination/{self.resource_type}.json"
        assert not os.path.exists(dest_combined_file), f"Combined file {dest_combined_file} should not exist"

        dest_files = glob.glob(f"resources/destination/{self.resource_type}.*.json")
        assert len(dest_files) > 0, f"No individual files found for {self.resource_type} in destination"

        # Count skipped resources
        num_resources_skipped = len(re.compile("skipping resource").findall(caplog.text))

        # Verify destination has the right number of files (source count minus skipped)
        # This is a generic assertion that should work for all resource types
        assert len(dest_files) + num_resources_skipped >= len(source_files), (
            f"Number of destination files ({len(dest_files)}) plus skipped ({num_resources_skipped}) "
            f"should be at least equal to the number of source files ({len(source_files)})"
        )

    def test_resource_update_sync_per_file(self, runner, caplog):
        """Test updating resources stored in individual files."""
        caplog.set_level(logging.DEBUG)
        self.resource_per_file = True
        # something is going on server side that requires a little delay before running this
        if self.resource_type == "metric_tag_configurations":
            sleep(5)

        # First import resources to work with
        import_cmd = [
            "import",
            "--validate=false",
            f"--resources={self.resource_type}",
            "--resource-per-file",
            "--send-metrics=False",
        ]
        if self.filter:
            import_cmd.append(f"--filter={self.filter}")
        ret = runner.invoke(
            cli,
            import_cmd,
        )
        assert 0 == ret.exit_code

        # Ensure initial sync so destination files exist
        sync_cmd = [
            "sync",
            "--validate=false",
            f"--resources={self.resource_type}",
            "--resource-per-file",
            "--force-missing-dependencies",
            "--create-global-downtime=False",
            "--send-metrics=False",
        ]
        if self.filter:
            sync_cmd.append(f"--filter={self.filter}")
        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code
        caplog.clear()

        # First, load all resources from individual files
        source_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        source_resources = {}

        for file_path in source_files:
            with open(file_path, "r") as f:
                resource_data = json.load(f)
                source_resources.update(resource_data)

        # Similar for destination resources
        dest_files = glob.glob(f"resources/destination/{self.resource_type}.*.json")
        destination_resources = {}

        for file_path in dest_files:
            with open(file_path, "r") as f:
                resource_data = json.load(f)
                destination_resources.update(resource_data)

        # Update fields in source resources
        for resource_id, resource in source_resources.items():
            try:
                path_parts = self.field_to_update.split(".")
                target = resource

                # Navigate to the nested field
                for i, part in enumerate(path_parts):
                    if i == len(path_parts) - 1:
                        if isinstance(target[part], list):
                            target[part].append("updated")
                        elif isinstance(target[part], str):
                            target[part] = target[part] + "updated"
                        elif isinstance(target[part], bool):
                            target[part] = not target[part]
                    else:
                        target = target[part]
            except Exception as e:
                pytest.fail(str(e))

            # Save the updated resource back to its individual file
            with open(f"resources/source/{self.resource_type}.{resource_id}.json", "w") as f:
                json.dump({resource_id: resource}, f)

        # Assert diff is produced
        diffs_cmd = [
            "diffs",
            "--validate=false",
            f"--resources={self.resource_type}",
            "--resource-per-file",
            "--send-metrics=False",
        ]
        if self.filter:
            diffs_cmd.append(f"--filter={self.filter}")
        ret = runner.invoke(
            cli,
            diffs_cmd,
        )
        assert caplog.text
        assert 0 == ret.exit_code

        # Sync the updated resources
        ret = runner.invoke(
            cli,
            sync_cmd,
        )
        assert 0 == ret.exit_code

        caplog.clear()
        # Assert diff is no longer produced
        ret = runner.invoke(
            cli,
            diffs_cmd,
        )

        # Check no files were created or changes needed
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text
        assert 0 == ret.exit_code


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
