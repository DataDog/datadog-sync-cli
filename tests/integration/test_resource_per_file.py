# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import logging
import os
import glob
import json
import shutil
import pytest

from datadog_sync.cli import cli
from datadog_sync.models import Monitors, Dashboards


@pytest.mark.vcr
@pytest.mark.integration
class TestResourcePerFile:
    """Test class for verifying the --resource-per-file flag functionality.

    This class tests that the flag causes individual files to be created for
    each resource, rather than one file per resource type.
    """

    # Test with multiple resource types that commonly have multiple instances
    resources = ",".join([Monitors.resource_type, Dashboards.resource_type])

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        """Set up a temporary directory for testing."""
        my_tmpdir = tmpdir_factory.mktemp("resource_per_file_test")
        os.chdir(my_tmpdir)

    def test_import_with_resource_per_file(self, runner, caplog):
        """Test that import with --resource-per-file creates individual files."""
        caplog.set_level(logging.DEBUG)

        # Run import with resource-per-file flag
        ret = runner.invoke(cli, ["import", "--validate=false", f"--resources={self.resources}", "--resource-per-file"])
        assert ret.exit_code == 0

        # Verify that individual files were created for each resource
        for resource_type in self.resources.split(","):
            # Check that no combined file exists
            combined_file = f"resources/source/{resource_type}.json"
            assert not os.path.exists(combined_file), f"Combined file {combined_file} should not exist"

            # Check that individual files exist
            individual_files = glob.glob(f"resources/source/{resource_type}.*.json")
            assert len(individual_files) > 0, f"No individual files found for {resource_type}"

            # Verify each file contains only one resource
            for file_path in individual_files:
                with open(file_path, "r") as f:
                    content = json.load(f)
                    assert len(content) == 1, f"File {file_path} should contain exactly one resource"

                    # Extract the resource ID from the filename
                    file_id = file_path.split(".")[-2]
                    assert file_id in content, f"File {file_path} should contain resource with ID {file_id}"

    def test_sync_with_resource_per_file(self, runner, caplog):
        """Test that sync with --resource-per-file maintains individual files."""
        caplog.set_level(logging.DEBUG)

        # Run sync with resource-per-file flag
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--resource-per-file",
                "--create-global-downtime=False",
            ],
        )
        assert ret.exit_code == 0

        # Verify that individual files were created for each resource in destination
        for resource_type in self.resources.split(","):
            # Check that no combined file exists in destination
            combined_file = f"resources/destination/{resource_type}.json"
            assert not os.path.exists(combined_file), f"Combined file {combined_file} should not exist"

            # Check that individual files exist in destination
            individual_files = glob.glob(f"resources/destination/{resource_type}.*.json")
            assert len(individual_files) > 0, f"No individual files found for {resource_type} in destination"

            # Verify each destination file contains only one resource
            for file_path in individual_files:
                with open(file_path, "r") as f:
                    content = json.load(f)
                    assert len(content) == 1, f"File {file_path} should contain exactly one resource"

                    # Extract the resource ID from the filename
                    file_id = file_path.split(".")[-2]
                    assert file_id in content, f"File {file_path} should contain resource with ID {file_id}"

            # Verify source and destination have same number of files
            source_files = glob.glob(f"resources/source/{resource_type}.*.json")
            assert len(source_files) == len(
                individual_files
            ), f"Source and destination should have same number of files for {resource_type}"

    def test_migrate_with_resource_per_file(self, runner, caplog):
        """Test that migrate with --resource-per-file creates individual files in both source and destination."""
        caplog.set_level(logging.DEBUG)

        # Clean up existing files before migrate test
        for path in ["resources/source", "resources/destination"]:
            if os.path.exists(path):
                shutil.rmtree(path)

        # Run migrate with resource-per-file flag
        ret = runner.invoke(
            cli,
            [
                "migrate",
                "--validate=false",
                f"--resources={self.resources}",
                "--resource-per-file",
                "--create-global-downtime=False",
            ],
        )
        assert ret.exit_code == 0

        # Verify that individual files were created for each resource in both source and destination
        for resource_type in self.resources.split(","):
            for location in ["source", "destination"]:
                # Check that no combined file exists
                combined_file = f"resources/{location}/{resource_type}.json"
                assert not os.path.exists(combined_file), f"Combined file {combined_file} should not exist"

                # Check that individual files exist
                individual_files = glob.glob(f"resources/{location}/{resource_type}.*.json")
                assert len(individual_files) > 0, f"No individual files found for {resource_type} in {location}"

                # Verify each file contains only one resource
                for file_path in individual_files:
                    with open(file_path, "r") as f:
                        content = json.load(f)
                        assert len(content) == 1, f"File {file_path} should contain exactly one resource"

                        # Extract the resource ID from the filename
                        file_id = file_path.split(".")[-2]
                        assert file_id in content, f"File {file_path} should contain resource with ID {file_id}"

            # Verify source and destination have same files
            source_files = glob.glob(f"resources/source/{resource_type}.*.json")
            dest_files = glob.glob(f"resources/destination/{resource_type}.*.json")
            assert len(source_files) == len(
                dest_files
            ), f"Source and destination should have same number of files for {resource_type}"

    def test_count_consistency(self, runner, caplog):
        """Test that the number of resources is consistent regardless of storage format."""
        caplog.set_level(logging.DEBUG)

        # Clean up existing files before test
        for path in ["resources/source", "resources/destination"]:
            if os.path.exists(path):
                shutil.rmtree(path)

        # First import without resource-per-file
        ret = runner.invoke(cli, ["import", "--validate=false", f"--resources={self.resources}"])
        assert ret.exit_code == 0

        # Count resources in combined files
        combined_resource_counts = {}
        for resource_type in self.resources.split(","):
            file_path = f"resources/source/{resource_type}.json"
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    resources = json.load(f)
                    combined_resource_counts[resource_type] = len(resources)

        # Clean up for the next test
        for path in ["resources/source", "resources/destination"]:
            if os.path.exists(path):
                shutil.rmtree(path)

        # Now import with resource-per-file
        ret = runner.invoke(cli, ["import", "--validate=false", f"--resources={self.resources}", "--resource-per-file"])
        assert ret.exit_code == 0

        # Count individual resource files
        individual_resource_counts = {}
        for resource_type in self.resources.split(","):
            individual_files = glob.glob(f"resources/source/{resource_type}.*.json")
            individual_resource_counts[resource_type] = len(individual_files)

        # Verify counts match between formats
        for resource_type in self.resources.split(","):
            assert combined_resource_counts[resource_type] == individual_resource_counts[resource_type], (
                f"Resource count mismatch for {resource_type}: "
                f"combined={combined_resource_counts[resource_type]}, "
                f"individual={individual_resource_counts[resource_type]}"
            )
