# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import os
import random
import shutil
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest

from datadog_sync.cli import cli


@pytest.mark.vcr
@pytest.mark.integration
class TestCli:
    # We test resources with resource dependencies only. To do that we have to also test those dependencies.
    # The rest of the resources are tested in the individual resource test files
    resources = ",".join(
        [
            "roles",
            "users",
            "teams",
            "dashboards",
            "dashboard_lists",
            "synthetics_global_variables",
            "synthetics_private_locations",
            #            "synthetics_tests",
            #            "synthetics_mobile_applications",
            #            "synthetics_mobile_applications_versions",
            "monitors",
            "downtime_schedules",
            "powerpacks",
            "service_level_objectives",
            "slo_corrections",
            "team_memberships",
        ]
    )

    def _wait_for_no_destination_monitors(self, max_retries=3):
        """Ensure destination org has no monitors before syncing.

        Resource tests (e.g. test_monitors) create and delete monitors, but Datadog's
        deduplication window may reject re-creation as 'Duplicate' for a short period
        after deletion. This polls before each sync/migrate test, deletes any lingering
        monitors, and retries with exponential backoff + jitter.
        """
        base_url = os.environ.get("DD_DESTINATION_API_URL", "").rstrip("/")
        api_key = os.environ.get("DD_DESTINATION_API_KEY", "")
        app_key = os.environ.get("DD_DESTINATION_APP_KEY", "")

        if not all([base_url, api_key, app_key]):
            return

        headers = {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}

        def get_monitors():
            req = urllib.request.Request(f"{base_url}/api/v1/monitor?page_size=100", headers=headers)
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        def delete_monitor(monitor_id):
            req = urllib.request.Request(
                f"{base_url}/api/v1/monitor/{monitor_id}?force=true",
                headers=headers,
                method="DELETE",
            )
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError:
                pass

        for attempt in range(max_retries):
            monitors = get_monitors()
            if not monitors:
                return

            for monitor in monitors:
                delete_monitor(monitor["id"])

            # Exponential backoff with jitter — only sleep when hitting the live API
            if os.environ.get("RECORD", "false").lower() == "true":
                delay = (2**attempt) * 5 + random.uniform(0, 3)
                time.sleep(delay)

        monitors = get_monitors()
        if monitors:
            pytest.fail(
                f"Destination still has {len(monitors)} monitors after {max_retries} retries: "
                f"{[m['name'] for m in monitors]}"
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
            ["diffs", "--validate=false", "--skip-failed-resource-connections=true"],
        )
        # assert diffs are produced
        assert caplog.text
        assert 0 == ret.exit_code

    def test_sync(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        self._wait_for_no_destination_monitors()

        #  Sync
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=true",
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
                "--skip-failed-resource-connections=true",
            ],
        )
        # assert no diffs are produced
        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text

        assert 0 == ret.exit_code
        # cleanup after ourselves
        self.test_cleanup(runner, caplog)

    def test_verify_ddr_status_failure(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        # source ddr fails
        with mock.patch.dict(os.environ, {"DD_SOURCE_API_KEY": "fake"}):
            for command in ["import", "migrate", "diffs"]:
                ret = runner.invoke(cli, [command, "--validate=false", f"--resources={self.resources}"])
                # The above should fail
                assert "No match for the request" not in caplog.text
                assert 1 == ret.exit_code
                assert "verification failed" in caplog.text

        # destination ddr fails
        with mock.patch.dict(os.environ, {"DD_DESTINATION_API_KEY": "fake"}):
            for command in ["sync", "migrate", "diffs", "reset"]:
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
                "--skip-failed-resource-connections=true",
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
        self._wait_for_no_destination_monitors()

        #  Sync
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resources}",
                "--skip-failed-resource-connections=true",
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
                "--skip-failed-resource-connections=true",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        ## assert diffs are produced
        assert caplog.text
        # cleanup after ourselves
        self.test_cleanup(runner, caplog)

    def test_migrate_without_verify_ddr_status(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        self._wait_for_no_destination_monitors()

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
                "--skip-failed-resource-connections=true",
                "--verify-ddr-status=False",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        assert caplog.text
        # assert diffs are produced
        assert "No match for the request" not in caplog.text
        # cleanup after ourselves
        self.test_cleanup(runner, caplog)

    def test_migrate(self, runner, caplog):
        caplog.set_level(logging.DEBUG)
        self._wait_for_no_destination_monitors()
        # Migrate

        ret = runner.invoke(
            cli,
            [
                "migrate",
                "--validate=false",
                f"--resources={self.resources}",
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
            ["diffs", "--validate=false", "--skip-failed-resource-connections=true", "--send-metrics=False"],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code
        # assert diffs are produced
        assert caplog.text
        # cleanup after ourselves
        self.test_cleanup(runner, caplog)

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
                "--skip-failed-resource-connections=true",
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
                    "--skip-failed-resource-connections=true",
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
                "--skip-failed-resource-connections=true",
                "--send-metrics=False",
            ],
        )
        assert "No match for the request" not in caplog.text
        assert 0 == ret.exit_code

        # assert no diffs are produced
        assert "to be deleted" not in caplog.text
        assert "to be created" not in caplog.text
        assert "diff:" not in caplog.text
