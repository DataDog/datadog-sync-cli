# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import glob
import json
import logging

import pytest

from tests.integration.helpers import (
    RESOURCE_SKIPPED_RE,
    RESOURCE_TO_ADD_RE,
    BaseResourcesTestClass,
    open_resources,
    path_lookup,
    path_update,
    save_source_resources,
)
from datadog_sync.cli import cli
from datadog_sync.models import MetricPercentiles


class TestMetricPercentilesResources(BaseResourcesTestClass):
    resource_type = MetricPercentiles.resource_type
    field_to_update = "include_percentiles"

    def test_resource_update_sync(self, runner, caplog):
        # The destination toggle endpoint permanently declines some metrics
        # (ineligible summary_aggr source) - it always reports them
        # "unsuccessful", so they never make it into the destination state and
        # can never be reconciled away. BaseResourcesTestClass.test_resource_update_sync
        # asserts a full create/update/resync round trip with no tolerance for
        # that permanent skip, so this override tolerates it the same way
        # test_resource_sync already does (by counting skips/creates instead of
        # asserting their absence). The skip itself is covered directly by unit
        # tests for update_resource().
        caplog.set_level(logging.DEBUG)

        self.import_resources(runner, caplog)
        caplog.clear()

        self.sync_resources(runner, caplog)
        caplog.clear()

        source_resources, destination_resources = open_resources(self.resource_type)

        for resource in source_resources.values():
            try:
                value = path_lookup(resource, self.field_to_update)
                if isinstance(value, bool):
                    value = not value

                path_update(resource, self.field_to_update, value)
            except Exception as err:
                pytest.fail(err)

        save_source_resources(self.resource_type, source_resources)

        caplog.clear()
        diff_cmd = [
            "diffs",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            "--send-metrics=False",
        ]

        ret = runner.invoke(cli, diff_cmd)
        assert caplog.text
        assert "diff:" in caplog.text or "to be created" in caplog.text
        assert 0 == ret.exit_code
        caplog.clear()

        sync_cmd = [
            "sync",
            "--validate=false",
            "--verify-ddr-status=False",
            f"--resources={self.resource_type}",
            "--create-global-downtime=False",
            "--send-metrics=False",
        ]

        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code
        caplog.clear()

        # Assert no diffs remain except for permanently non-configurable metrics,
        # which will forever show as "to be created" since they never land in
        # the destination state.
        ret = runner.invoke(cli, diff_cmd)
        assert 0 == ret.exit_code
        assert "to be deleted" not in caplog.text

        num_resources_to_add = len(RESOURCE_TO_ADD_RE.findall(caplog.text))
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == len(destination_resources) + num_resources_to_add + num_resources_skipped
        caplog.clear()

    def test_resource_sync_per_file(self, runner, caplog):
        # Same permanent-skip tolerance as test_resource_update_sync above,
        # applied to the resource-per-file variant: BaseResourcesTestClass
        # asserts destination files always exist, which doesn't hold when every
        # metric in the org is non-configurable.
        caplog.set_level(logging.DEBUG)
        self.resource_per_file = True

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

        source_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        assert len(source_files) > 0, f"No individual files found for {self.resource_type} in source"

        dest_files = glob.glob(f"resources/destination/{self.resource_type}.*.json")

        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        assert len(dest_files) + num_resources_skipped >= len(source_files), (
            f"Number of destination files ({len(dest_files)}) plus skipped ({num_resources_skipped}) "
            f"should be at least equal to the number of source files ({len(source_files)})"
        )

    def test_resource_update_sync_per_file(self, runner, caplog):
        # Same permanent-skip tolerance as test_resource_update_sync above,
        # applied to the resource-per-file variant.
        caplog.set_level(logging.DEBUG)
        self.resource_per_file = True
        if self.resource_type == "metric_tag_configurations":
            from time import sleep

            sleep(5)

        import_cmd = [
            "import",
            "--validate=false",
            f"--resources={self.resource_type}",
            "--resource-per-file",
            "--send-metrics=False",
        ]
        if self.filter:
            import_cmd.append(f"--filter={self.filter}")
        ret = runner.invoke(cli, import_cmd)
        assert 0 == ret.exit_code

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

        source_files = glob.glob(f"resources/source/{self.resource_type}.*.json")
        source_resources = {}
        for file_path in source_files:
            with open(file_path, "r") as f:
                source_resources.update(json.load(f))

        for resource_id, resource in source_resources.items():
            try:
                value = path_lookup(resource, self.field_to_update)
                if isinstance(value, bool):
                    value = not value
                path_update(resource, self.field_to_update, value)
            except Exception as e:
                pytest.fail(str(e))

            with open(f"resources/source/{self.resource_type}.{resource_id.replace(':', '.')}.json", "w") as f:
                json.dump({resource_id: resource}, f)

        diffs_cmd = [
            "diffs",
            "--validate=false",
            f"--resources={self.resource_type}",
            "--resource-per-file",
            "--send-metrics=False",
        ]
        if self.filter:
            diffs_cmd.append(f"--filter={self.filter}")
        ret = runner.invoke(cli, diffs_cmd)
        assert caplog.text
        assert 0 == ret.exit_code

        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code

        caplog.clear()
        ret = runner.invoke(cli, diffs_cmd)
        assert 0 == ret.exit_code
        assert "to be deleted" not in caplog.text
        assert "diff:" not in caplog.text
