# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import glob
import json
import logging
import re
import shutil
import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.models import Dashboards
from tests.integration.helpers import BaseResourcesTestClass


class TestResourcePerFileDashboards(BaseResourcesTestClass):
    """Test class for verifying the Dashboards resource with the --resource-per-file flag."""

    resource_type = Dashboards.resource_type
    field_to_update = "title"
    resource_per_file = True

    def test_resource_import_per_file(self, runner, caplog):
        """Test that importing dashboards with --resource-per-file creates individual files."""
        caplog.set_level(logging.DEBUG)

        # Import with resource-per-file flag
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
            ],
        )
        assert 0 == ret.exit_code

        # Verify files were created as individual files
        combined_file = f"resources/source/{self.resource_type}.json"
        assert not os.path.exists(combined_file), f"Combined file {combined_file} should not exist"

        individual_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        assert len(individual_files) > 0, f"No individual files found for {self.resource_type}"

        # Verify each file has exactly one resource with the ID matching the filename
        for file_path in individual_files:
            with open(file_path, "r") as f:
                content = json.load(f)
                assert len(content) == 1, f"File {file_path} should contain exactly one resource"

                # Extract the resource ID from the filename
                file_id = file_path.split(".")[-2]
                assert file_id in content, f"File {file_path} should contain resource with ID {file_id}"

        # Run diffs to ensure everything is recognized properly
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--skip-failed-resource-connections=false",
            ],
        )
        assert 0 == ret.exit_code

        # Count resources that should be added
        num_resources_to_add = len(re.compile("to be created").findall(caplog.text))
        num_resources_skipped = len(re.compile("skipping resource").findall(caplog.text))

        # Total number of individual files should match number of resources to add plus skipped
        assert len(individual_files) == self.compute_import_changes(num_resources_to_add, num_resources_skipped)

    def test_resource_sync_per_file(self, runner, caplog):
        """Test that syncing dashboards with --resource-per-file creates individual files."""
        caplog.set_level(logging.DEBUG)

        # First import resources to work with
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
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
                "--create-global-downtime=False",
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
        assert len(source_files) == (len(dest_files) + num_resources_skipped), (
            f"Number of source files ({len(source_files)}) minus skipped ({num_resources_skipped}) "
            f"should equal number of destination files ({len(dest_files)})"
        )

    def test_resource_update_sync_per_file(self, runner, caplog):
        """Test updating resources stored in individual files."""
        caplog.set_level(logging.DEBUG)

        # First import resources to work with
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
            ],
        )
        assert 0 == ret.exit_code

        # Ensure initial sync so destination files exist
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
                "--create-global-downtime=False",
            ],
        )
        assert 0 == ret.exit_code

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
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
            ],
        )
        assert caplog.text
        assert 0 == ret.exit_code

        # Sync the updated resources
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
                "--create-global-downtime=False",
            ],
        )
        assert 0 == ret.exit_code

        caplog.clear()
        # Assert diff is no longer produced
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--resource-per-file",
            ],
        )

        # Check no files were created or changes needed
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text
        assert 0 == ret.exit_code
