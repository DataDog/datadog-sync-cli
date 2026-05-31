# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for ImportState (write-only state) and the --skip-state-load flag."""

import json

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Origin
from datadog_sync.utils.import_state import ImportState
from datadog_sync.utils.source_state_writer import SourceStateWriter
from datadog_sync.utils.state import State
from datadog_sync.utils.storage._base_storage import StorageData
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=True)


class TestImportStateInvariant:
    """ImportState exposes no source/destination read surface; reads fail at the language level."""

    def test_import_state_has_no_source_attribute(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert not hasattr(state, "source")
        with pytest.raises(AttributeError):
            _ = state.source  # noqa: F841

    def test_import_state_has_no_destination_attribute(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert not hasattr(state, "destination")
        with pytest.raises(AttributeError):
            _ = state.destination  # noqa: F841

    def test_import_state_has_no_load_state_method(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert not hasattr(state, "load_state")

    def test_import_state_has_no_compute_stale_files(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert not hasattr(state, "compute_stale_files")


class TestImportStateWriteAPI:
    """ImportState write methods accumulate in memory and flush via dump_state."""

    def test_set_source_and_dump_writes_to_storage(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        state.set_source("roles", "role-1", {"id": "role-1", "name": "Admin"})
        state.set_source("roles", "role-2", {"id": "role-2", "name": "Viewer"})
        state.dump_state(Origin.SOURCE)

        # Read back via a fresh State to verify the writes are on disk.
        fresh = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        assert fresh.source["roles"]["role-1"]["name"] == "Admin"
        assert fresh.source["roles"]["role-2"]["name"] == "Viewer"

    def test_clear_source_type_is_idempotent_on_empty(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        # No exception on a never-set type
        state.clear_source_type("monitors")
        state.clear_source_type("monitors")

    def test_dump_state_rejects_destination(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        with pytest.raises(ValueError, match="only supports Origin.SOURCE"):
            state.dump_state(Origin.DESTINATION)

    def test_dump_state_rejects_all(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        with pytest.raises(ValueError, match="only supports Origin.SOURCE"):
            state.dump_state(Origin.ALL)

    def test_authoritative_marks_track_state(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        state.mark_source_authoritative(["users", "roles"])
        assert state._authoritative_source_types == {"users", "roles"}
        state.clear_source_authoritative(["users"])
        assert state._authoritative_source_types == {"roles"}


class TestSourceStateWriterProtocol:
    """Both State and ImportState satisfy SourceStateWriter."""

    def test_state_satisfies_protocol(self, tmp_path):
        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert isinstance(state, SourceStateWriter)

    def test_import_state_satisfies_protocol(self, tmp_path):
        state = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        assert isinstance(state, SourceStateWriter)


class TestPartialImportPreservesUntouchedTypes:
    """Skip-state-load partial import must not drop blobs for types it did not touch.

    Addresses a senior-review concern: if `dump_state` had REPLACE semantics
    (rewriting the source snapshot from in-memory state), starting from an
    empty `state.source` would drop blobs for non-imported types. This test
    verifies the actual PATCH semantics across the import flow end-to-end.
    """

    def test_dump_state_with_only_one_type_leaves_other_types_on_disk(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        # Pre-populate: write blobs for "roles" and "dashboards" via a regular State.
        seed = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        seed.set_source("roles", "role-1", {"id": "role-1", "name": "Admin"})
        seed.set_source("dashboards", "dash-1", {"id": "dash-1", "title": "Untouched"})
        seed.dump_state(Origin.SOURCE)

        roles_file = src / "roles.role-1.json"
        dashboards_file = src / "dashboards.dash-1.json"
        assert roles_file.exists()
        assert dashboards_file.exists()
        original_dashboards_mtime = dashboards_file.stat().st_mtime_ns
        original_dashboards_content = dashboards_file.read_text()

        # Simulate an import-with-skip-state-load that only touches "roles":
        # construct a fresh ImportState (no preload), write a single role,
        # and dump. The dashboards.dash-1.json blob must be untouched.
        imp = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        imp.clear_source_type("roles")
        imp.set_source("roles", "role-1", {"id": "role-1", "name": "AdminUpdated"})
        imp.dump_state(Origin.SOURCE)

        # roles was rewritten with fresh data:
        roles_content = json.loads(roles_file.read_text())
        assert roles_content["role-1"]["name"] == "AdminUpdated"

        # dashboards was NOT touched on disk (mtime + content both unchanged):
        assert dashboards_file.stat().st_mtime_ns == original_dashboards_mtime
        assert dashboards_file.read_text() == original_dashboards_content


class TestStaleWithinTypeBehaviorUnchanged:
    """Skip-state-load does not change stale-within-type semantics.

    If a resource was previously imported and is later deleted from the source
    org, the orphan blob persists on disk in both modes (this is what the
    separate `prune` command addresses). This test verifies the behavior is
    identical with and without --skip-state-load.
    """

    def test_skip_state_load_orphans_match_baseline(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        # Pre-populate four users.
        seed = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        for uid in ("user-1", "user-2", "user-3", "user-4"):
            seed.set_source("users", uid, {"id": uid})
        seed.dump_state(Origin.SOURCE)

        # ImportState-style re-import: only three users come back from the API.
        imp = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        imp.clear_source_type("users")
        for uid in ("user-1", "user-2", "user-3"):
            imp.set_source("users", uid, {"id": uid})
        imp.dump_state(Origin.SOURCE)

        # Fetched users were rewritten; user-4 orphan persists on disk.
        assert (src / "users.user-1.json").exists()
        assert (src / "users.user-2.json").exists()
        assert (src / "users.user-3.json").exists()
        assert (src / "users.user-4.json").exists()  # orphan, by design


class TestSkipStateLoadCLIValidation:
    """End-to-end Click-level validation of --skip-state-load flag combinations.

    Each invocation here passes the minimum args needed to reach build_config's
    validation block. The patterns mirror existing tests in
    test_minimize_reads_type_scoped.py.
    """

    def test_skip_state_load_requires_resources(self, runner):
        result = runner.invoke(
            cli,
            [
                "import",
                "--skip-state-load",
                "--resource-per-file",
                "--source-api-key=k",
                "--source-app-key=k",
                "--destination-api-key=k",
                "--destination-app-key=k",
            ],
        )
        assert result.exit_code != 0
        assert "--skip-state-load requires --resources" in result.output

    def test_skip_state_load_requires_resource_per_file(self, runner):
        result = runner.invoke(
            cli,
            [
                "import",
                "--skip-state-load",
                "--resources=users",
                "--source-api-key=k",
                "--source-app-key=k",
                "--destination-api-key=k",
                "--destination-app-key=k",
            ],
        )
        assert result.exit_code != 0
        assert "--skip-state-load requires --resource-per-file" in result.output

    def test_skip_state_load_still_rejects_deprecated_resource_conflicts(self, runner, caplog):
        # _handle_deprecated runs for both State and ImportState callers. The
        # conflict checks (logs_custom_pipelines + logs_pipelines, downtimes
        # + downtime_schedules) MUST still fire when --skip-state-load is set,
        # even though that flag bypasses the read-state fallback branch.
        # The error goes through config.logger so capture via caplog rather
        # than result.output (CliRunner does not capture logger writes).
        import logging

        from datadog_sync.constants import LOGGER_NAME

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            result = runner.invoke(
                cli,
                [
                    "import",
                    "--skip-state-load",
                    "--resource-per-file",
                    "--resources=logs_custom_pipelines,logs_pipelines",
                    "--source-api-key=k",
                    "--source-app-key=k",
                    "--destination-api-key=k",
                    "--destination-app-key=k",
                ],
            )
        # _handle_deprecated calls sys.exit(1) on conflict, so exit code is 1
        # specifically (not Click's 2 for parser errors).
        assert result.exit_code == 1, result.output
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any(
            "logs_custom_pipelines" in m and "logs_pipelines" in m and "should not" in m and "duplication" in m
            for m in errors
        ), errors

    def test_skip_state_load_still_rejects_downtimes_conflict(self, runner, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            result = runner.invoke(
                cli,
                [
                    "import",
                    "--skip-state-load",
                    "--resource-per-file",
                    "--resources=downtimes,downtime_schedules",
                    "--source-api-key=k",
                    "--source-app-key=k",
                    "--destination-api-key=k",
                    "--destination-app-key=k",
                ],
            )
        assert result.exit_code == 1, result.output
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any(
            "downtimes" in m and "downtime_schedules" in m and "should not" in m and "duplication" in m for m in errors
        ), errors

    def test_skip_state_load_rejected_with_minimize_reads(self, runner):
        # --minimize-reads is registered only on the sync command, so it's
        # never parseable on import in normal CLI use. To reach the
        # build_config rejection from a single command invocation, exercise
        # build_config directly with both kwargs set.
        from click import UsageError
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        with pytest.raises(UsageError, match="cannot be combined"):
            build_config(
                Command.IMPORT,
                resources="users",
                resource_per_file=True,
                skip_state_load=True,
                minimize_reads=True,
                source_api_key="k",
                source_app_key="k",
                destination_api_key="k",
                destination_app_key="k",
                source_api_url="https://example.com",
                destination_api_url="https://example.com",
                storage_type="local",
                source_resources_path="/tmp/src",
                destination_resources_path="/tmp/dst",
                max_workers=1,
                send_metrics=False,
                verify_ddr_status=False,
                validate=False,
                show_progress_bar=False,
                allow_self_lockout=False,
                force_missing_dependencies=False,
                skip_failed_resource_connections=False,
            )


class TestClickDecorationGuard:
    """`--skip-state-load` is only registered on import; Click rejects it elsewhere."""

    def test_import_help_lists_skip_state_load(self, runner):
        result = runner.invoke(cli, ["import", "--help"])
        assert "--skip-state-load" in result.output

    def test_sync_help_does_not_list_skip_state_load(self, runner):
        result = runner.invoke(cli, ["sync", "--help"])
        assert "--skip-state-load" not in result.output

    def test_migrate_help_does_not_list_skip_state_load(self, runner):
        result = runner.invoke(cli, ["migrate", "--help"])
        assert "--skip-state-load" not in result.output

    def test_diffs_help_does_not_list_skip_state_load(self, runner):
        result = runner.invoke(cli, ["diffs", "--help"])
        assert "--skip-state-load" not in result.output

    def test_reset_help_does_not_list_skip_state_load(self, runner):
        result = runner.invoke(cli, ["reset", "--help"])
        assert "--skip-state-load" not in result.output

    def test_prune_help_does_not_list_skip_state_load(self, runner):
        result = runner.invoke(cli, ["prune", "--help"])
        assert "--skip-state-load" not in result.output

    def test_sync_rejects_skip_state_load(self, runner):
        result = runner.invoke(cli, ["sync", "--skip-state-load", "--resources=users"])
        assert result.exit_code != 0
        assert "No such option" in result.output or "no such option" in result.output.lower()

    def test_migrate_rejects_skip_state_load(self, runner):
        result = runner.invoke(cli, ["migrate", "--skip-state-load", "--resources=users"])
        assert result.exit_code != 0
        assert "No such option" in result.output or "no such option" in result.output.lower()


class TestInstrumentationLogs:
    """Storage backends and state lifecycle emit `sync-cli-timing` log lines."""

    def test_state_init_emits_timing(self, tmp_path, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            State(
                type_=StorageType.LOCAL_FILE,
                source_resources_path=str(tmp_path / "source"),
                destination_resources_path=str(tmp_path / "dest"),
                resource_per_file=True,
            )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sync-cli-timing phase=state_init" in m for m in msgs), msgs

    def test_import_state_init_emits_timing_with_skip_flag(self, tmp_path, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            ImportState(
                type_=StorageType.LOCAL_FILE,
                source_resources_path=str(tmp_path / "source"),
                destination_resources_path=str(tmp_path / "dest"),
                resource_per_file=True,
            )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sync-cli-timing phase=state_init" in m and "skip_state_load=True" in m for m in msgs), msgs

    def test_local_file_get_emits_list_and_load_timing(self, tmp_path, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()
        backend = LocalFile(
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend.get(Origin.SOURCE)
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sync-cli-timing phase=list_and_load" in m and "backend=local_file" in m for m in msgs), msgs

    def test_local_file_put_emits_timing(self, tmp_path, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()
        backend = LocalFile(
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        data = StorageData()
        data.source["users"]["u1"] = {"id": "u1"}
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend.put(Origin.SOURCE, data)
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sync-cli-timing phase=put" in m and "backend=local_file" in m for m in msgs), msgs


# Shared log-schema fields the rollout plan relies on. Each list_and_load log
# line on a cloud backend must include every key in this set, so downstream
# parsers can index on a stable schema across backends. `aborted` distinguishes
# a successful empty result from an uncaught exception that exited mid-call.
EXPECTED_LIST_AND_LOAD_FIELDS = (
    "pages_listed=",
    "blobs_listed=",
    "blobs_downloaded=",
    "transient_errors=",
    "aborted=",
    "list_ms=",
    "download_ms=",
    "wall_ms=",
)


class TestCloudBackendListAndLoadLogSchema:
    """Each cloud backend's _list_and_load log must include the rollout-plan fields.

    Backend SDK clients are mocked to return one fake blob/object per call.
    The tests assert (a) the timing log fires once with `phase=list_and_load`,
    and (b) every required field key is present in the log line.
    """

    def _log_messages(self, caplog):
        return [r.getMessage() for r in caplog.records]

    def _find_list_and_load(self, msgs, backend):
        matches = [m for m in msgs if "sync-cli-timing phase=list_and_load" in m and f"backend={backend}" in m]
        return matches

    def test_gcs_list_and_load_log_schema(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.gcs_bucket import GCSBucket

        fake_blob = MagicMock()
        fake_blob.name = "resources/source/users.u1.json"
        fake_download = MagicMock()
        fake_download.download_as_text.return_value = '{"u1": {"id": "u1"}}'
        fake_bucket = MagicMock()
        fake_bucket.list_blobs.return_value = [fake_blob]
        fake_bucket.blob.return_value = fake_download
        fake_client = MagicMock()
        fake_client.bucket.return_value = fake_bucket

        with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage.Client", return_value=fake_client):
            backend = GCSBucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={"gcs_bucket_name": "test-bucket"},
                resource_per_file=True,
            )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend._list_and_load("resources/source", None, "source")
        matches = self._find_list_and_load(self._log_messages(caplog), "gcs")
        assert len(matches) == 1, matches
        for field in EXPECTED_LIST_AND_LOAD_FIELDS:
            assert field in matches[0], f"{field!r} missing from GCS log line: {matches[0]}"

    def test_aws_list_and_load_log_schema(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch
        from io import BytesIO

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

        fake_client = MagicMock()
        fake_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "resources/source/users.u1.json"}],
            "IsTruncated": False,
        }
        fake_client.get_object.return_value = {"Body": BytesIO(b'{"u1": {"id": "u1"}}')}

        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3.client", return_value=fake_client):
            backend = AWSS3Bucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={"aws_bucket_name": "test-bucket", "aws_region_name": "us-east-1"},
                resource_per_file=True,
            )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend._list_and_load("resources/source", None, "source")
        matches = self._find_list_and_load(self._log_messages(caplog), "aws_s3")
        assert len(matches) == 1, matches
        for field in EXPECTED_LIST_AND_LOAD_FIELDS:
            assert field in matches[0], f"{field!r} missing from AWS log line: {matches[0]}"

    def test_azure_list_and_load_log_schema(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer

        fake_blob = MagicMock()
        fake_blob.name = "resources/source/users.u1.json"
        fake_dl = MagicMock()
        fake_dl.readall.return_value = b'{"u1": {"id": "u1"}}'
        fake_container = MagicMock()
        fake_container.list_blobs.return_value = [fake_blob]
        fake_container.download_blob.return_value = fake_dl

        with patch.object(AzureBlobContainer, "__init__", lambda self, *a, **kw: None):
            backend = AzureBlobContainer()
            backend.source_resources_path = "resources/source"
            backend.destination_resources_path = "resources/destination"
            backend.resource_per_file = True
            backend.container_client = fake_container
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend._list_and_load("resources/source", None, "source")
        matches = self._find_list_and_load(self._log_messages(caplog), "azure_blob")
        assert len(matches) == 1, matches
        for field in EXPECTED_LIST_AND_LOAD_FIELDS:
            assert field in matches[0], f"{field!r} missing from Azure log line: {matches[0]}"

    def test_local_file_list_and_load_log_schema(self, tmp_path, caplog):
        import logging

        from datadog_sync.constants import LOGGER_NAME

        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()
        (src / "users.u1.json").write_text('{"u1": {"id": "u1"}}')
        backend = LocalFile(
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend.get(Origin.SOURCE)
        matches = self._find_list_and_load(self._log_messages(caplog), "local_file")
        assert len(matches) == 1, matches
        for field in EXPECTED_LIST_AND_LOAD_FIELDS:
            assert field in matches[0], f"{field!r} missing from LocalFile log line: {matches[0]}"


class TestCloudBackendListAndLoadFailureLogging:
    """If the underlying SDK raises during list/download, the timing log MUST
    still emit (from the finally block) with aborted=1. Otherwise operators
    lose visibility into exactly the contention failure mode this PR is meant
    to measure.
    """

    def _log_messages(self, caplog):
        return [r.getMessage() for r in caplog.records]

    def test_gcs_aborted_log_on_list_blobs_exception(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.gcs_bucket import GCSBucket

        fake_bucket = MagicMock()
        fake_bucket.list_blobs.side_effect = RuntimeError("simulated 5xx from GCS list")
        fake_client = MagicMock()
        fake_client.bucket.return_value = fake_bucket

        with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage.Client", return_value=fake_client):
            backend = GCSBucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={"gcs_bucket_name": "test-bucket"},
                resource_per_file=True,
            )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            with pytest.raises(RuntimeError):
                backend._list_and_load("resources/source", None, "source")
        msgs = self._log_messages(caplog)
        list_and_load_lines = [m for m in msgs if "phase=list_and_load" in m and "backend=gcs" in m]
        assert len(list_and_load_lines) == 1, list_and_load_lines
        assert "aborted=1" in list_and_load_lines[0], list_and_load_lines[0]

    def test_aws_aborted_log_on_list_objects_exception(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

        fake_client = MagicMock()
        fake_client.list_objects_v2.side_effect = RuntimeError("simulated 5xx from S3 ListObjects")

        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3.client", return_value=fake_client):
            backend = AWSS3Bucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={"aws_bucket_name": "test-bucket", "aws_region_name": "us-east-1"},
                resource_per_file=True,
            )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            with pytest.raises(RuntimeError):
                backend._list_and_load("resources/source", None, "source")
        msgs = self._log_messages(caplog)
        lines = [m for m in msgs if "phase=list_and_load" in m and "backend=aws_s3" in m]
        assert len(lines) == 1, lines
        assert "aborted=1" in lines[0], lines[0]

    def test_azure_aborted_log_on_list_blobs_exception(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer

        fake_container = MagicMock()
        fake_container.list_blobs.side_effect = RuntimeError("simulated 5xx from Azure list_blobs")

        with patch.object(AzureBlobContainer, "__init__", lambda self, *a, **kw: None):
            backend = AzureBlobContainer()
            backend.source_resources_path = "resources/source"
            backend.destination_resources_path = "resources/destination"
            backend.resource_per_file = True
            backend.container_client = fake_container
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            with pytest.raises(RuntimeError):
                backend._list_and_load("resources/source", None, "source")
        msgs = self._log_messages(caplog)
        lines = [m for m in msgs if "phase=list_and_load" in m and "backend=azure_blob" in m]
        assert len(lines) == 1, lines
        assert "aborted=1" in lines[0], lines[0]

    def test_gcs_paged_iterator_path_emits_correct_page_count(self, caplog):
        # The production HTTPIterator has a .pages attribute returning a
        # generator of pages. The fallback path (mocks returning flat lists)
        # is exercised by the happy-path tests above. This test exercises the
        # real-SDK path with .pages returning two pages.
        import logging
        from unittest.mock import MagicMock, patch

        from datadog_sync.constants import LOGGER_NAME
        from datadog_sync.utils.storage.gcs_bucket import GCSBucket

        blob_a = MagicMock()
        blob_a.name = "resources/source/users.u1.json"
        blob_b = MagicMock()
        blob_b.name = "resources/source/users.u2.json"
        page1 = [blob_a]
        page2 = [blob_b]

        # Iterator object with a `.pages` attribute. When the code finds
        # .pages it MUST iterate by page (not by blob) so the page count is
        # accurate.
        iterator = MagicMock()
        iterator.pages = iter([page1, page2])

        fake_download = MagicMock()
        fake_download.download_as_text.return_value = '{"u": {"id": "u"}}'

        fake_bucket = MagicMock()
        fake_bucket.list_blobs.return_value = iterator
        fake_bucket.blob.return_value = fake_download

        fake_client = MagicMock()
        fake_client.bucket.return_value = fake_bucket

        with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage.Client", return_value=fake_client):
            backend = GCSBucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={"gcs_bucket_name": "test-bucket"},
                resource_per_file=True,
            )
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            backend._list_and_load("resources/source", None, "source")
        msgs = self._log_messages(caplog)
        lines = [m for m in msgs if "phase=list_and_load" in m and "backend=gcs" in m]
        assert len(lines) == 1, lines
        assert "pages_listed=2" in lines[0], lines[0]
        assert "blobs_listed=2" in lines[0], lines[0]
        assert "blobs_downloaded=2" in lines[0], lines[0]
        assert "aborted=0" in lines[0], lines[0]

    def test_local_file_aborted_log_on_listdir_exception(self, tmp_path, caplog):
        import logging
        from unittest.mock import patch

        from datadog_sync.constants import LOGGER_NAME

        backend = LocalFile(
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        # Force os.listdir to raise; the backend has no broader except, so the
        # exception propagates out — but the finally block must still log.
        (tmp_path / "source").mkdir()
        with patch("datadog_sync.utils.storage.local_file.os.listdir", side_effect=PermissionError("denied")):
            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                with pytest.raises(PermissionError):
                    backend.get(Origin.SOURCE)
        msgs = self._log_messages(caplog)
        lines = [m for m in msgs if "phase=list_and_load" in m and "backend=local_file" in m]
        assert len(lines) >= 1, lines
        assert "aborted=1" in lines[0], lines[0]


class TestBuildConfigDispatch:
    """`--skip-state-load` constructs ImportState; absence preserves default."""

    @staticmethod
    def _base_kwargs(tmp_path):
        return dict(
            resources="users",
            resource_per_file=True,
            source_api_key="k",
            source_app_key="k",
            destination_api_key="k",
            destination_app_key="k",
            source_api_url="https://example.com",
            destination_api_url="https://example.com",
            storage_type="local",
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            max_workers=1,
            send_metrics=False,
            verify_ddr_status=False,
            validate=False,
            show_progress_bar=False,
            allow_self_lockout=False,
            force_missing_dependencies=False,
            skip_failed_resource_connections=False,
        )

    def test_skip_state_load_constructs_import_state(self, tmp_path):
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, skip_state_load=True, **self._base_kwargs(tmp_path))
        assert isinstance(cfg.state, ImportState)

    def test_default_constructs_state(self, tmp_path):
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, **self._base_kwargs(tmp_path))
        assert isinstance(cfg.state, State)


class TestRoundTripImportSkipLoadThenRead:
    """Import with --skip-state-load writes state that a subsequent State()
    construction (which loads from storage) reads back correctly.

    This is the round-trip test the rollout plan asked for: a fresh import
    that skips the preload must still produce a bucket layout that a normal
    sync (or any other state-reading caller) can pick up on the next run.
    """

    def test_import_state_writes_are_readable_by_fresh_state(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()

        imp = ImportState(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        imp.set_source("users", "u1", {"id": "u1", "name": "Alice"})
        imp.set_source("users", "u2", {"id": "u2", "name": "Bob"})
        imp.set_source("roles", "r1", {"id": "r1", "name": "Admin"})
        imp.dump_state(Origin.SOURCE)

        # Fresh State construction triggers a load from storage. The just-written
        # source state should be visible; destination remains empty (the import
        # never wrote to destination).
        fresh = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        )
        assert fresh.source["users"]["u1"]["name"] == "Alice"
        assert fresh.source["users"]["u2"]["name"] == "Bob"
        assert fresh.source["roles"]["r1"]["name"] == "Admin"
        assert fresh.destination == {}
