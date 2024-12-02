# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import logging
import shutil
import os
from unittest import mock

import pytest
from tempfile import TemporaryDirectory

from datadog_sync.cli import cli


@pytest.mark.vcr
@pytest.mark.integration
class TestCli:
    # We test resources with resource dependencies only. The rest of the resources are
    # tested in the individual resource test files
    resources = ",".join(
        [
            "roles",
            "users",
            "dashboards",
            "dashboard_lists",
            "synthetics_global_variables",
            "synthetics_private_locations",
            "synthetics_tests",
            "monitors",
            "downtime_schedules",
            "powerpacks",
            "service_level_objectives",
            "slo_corrections",
        ]
    )

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        my_tmpdir = tmpdir_factory.mktemp("tmp")
        os.chdir(my_tmpdir)

    def test_import(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        # Import
        ret = runner.invoke(cli, ["import", "--validate=false", f"--resources={self.resources}"])
        assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            ["diffs", "--validate=false", "--skip-failed-resource-connections=False"],
        )
        # assert diffs are produced
        assert caplog.text
        assert 0 == ret.exit_code

    def test_sync(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        #  Sync
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=False",
                "--create-global-downtime=False",
            ],
        )
        assert 0 == ret.exit_code
        assert caplog.text
        assert "No match for the request" not in caplog.text

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=False",
            ],
        )
        # assert no diffs are produced
        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text

        assert 0 == ret.exit_code

    def test_import_verify_ddr_status_failure(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        with mock.patch.dict(os.environ, {"DD_SOURCE_API_KEY": "fake"}):
            for command in ["import", "sync", "migrate", "diffs"]:
                ret = runner.invoke(cli, [command, "--validate=false", f"--resources={self.resources}"])
                # The above should fail
                assert "No match for the request" not in caplog.text
                assert 1 == ret.exit_code
                assert "verification failed" in caplog.text

    def test_import_without_verify_ddr_status(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Import
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                f"--resources={self.resources}",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--skip-failed-resource-connections=False",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        # assert diffs are produced
        assert caplog.text

    def test_sync_without_verify_ddr_status(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        #  Sync
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=False",
                "--verify-ddr-status=False",
                "--send-metrics=False",
                "--create-global-downtime=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=False",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        ## assert diffs are produced
        assert caplog.text

    def test_migrate_without_verify_ddr_status(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Migrate
        ret = runner.invoke(
            cli,
            [
                "migrate",
                "--validate=false",
                f"--resources={self.resources}",
                "--verify-ddr-status=False",
                "--send-metrics=False",
                "--create-global-downtime=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--skip-failed-resource-connections=False",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        assert caplog.text
        # assert diffs are produced
        assert "No match for the request" not in caplog.text

    def test_cleanup(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # Remove current source resources
        shutil.rmtree("resources/source", ignore_errors=True)

        # Re-import resources that should not be cleaned up
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                "--resources=roles,users",
                "--filter=Type=roles;Name=attributes.user_count;Value=[^0]+;Operator=SubString",
                "--filter=Type=users;Name=attributes.status;Value=Active",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        # Sync with cleanup
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--cleanup=force",
                "--skip-failed-resource-connections=False",
                "--send-metrics=False",
                "--create-global-downtime=False",
            ],
        )
        if ret.exit_code != 0:
            # Retry cleanup if there was an error on the previous run.
            # We currently do not build a graph for cleanup hence we run into situations where
            # we attemp to delete resources before its usage areas are cleanup.
            # E.g. synthetics global variable is attempted to be deleted before the
            # synthetics test using it.
            ret = runner.invoke(
                cli,
                [
                    "sync",
                    "--validate=false",
                    f"--resources={self.resources}",
                    "--cleanup=force",
                    "--skip-failed-resource-connections=False",
                    "--send-metrics=False",
                    "--create-global-downtime=False",
                ],
            )

        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        # assert no diffs are produced
        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text

    def test_migrate(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        # Migrate

        with TemporaryDirectory() as source_path, TemporaryDirectory() as destination_path:
            ret = runner.invoke(
                cli,
                [
                    "migrate",
                    "--validate=false",
                    f"--resources={self.resources}",
                    "--send-metrics=False",
                    "--create-global-downtime=False",
                    f"--source-resources-path={source_path}",
                    f"--destination-resources-path={destination_path}",
                ],
            )
            assert "No match for the request" not in caplog.text
            assert 0 == ret.exit_code

        caplog.clear()
        # Check diff
        ret = runner.invoke(
            cli,
            ["diffs", "--validate=false", "--skip-failed-resource-connections=False", "--send-metrics=False"],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        # assert diffs are produced
        assert caplog.text
