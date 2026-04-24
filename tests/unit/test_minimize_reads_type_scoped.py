# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for PR 2: type-scoped loading via --minimize-reads flag (type-scoped strategy)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.constants import Origin
from datadog_sync.utils.storage._base_storage import StorageData
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=True)


class TestStateTypeScopedLoading:
    def test_state_loads_only_requested_types_when_type_scoped(self, tmp_path):
        """State with resource_types only loads those types from storage."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        # Write two resource types to storage
        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["roles"]["role-1"] = {"id": "role-1", "name": "Admin"}
        data.source["dashboards"]["dash-1"] = {"id": "dash-1", "title": "My Dash"}
        backend.put(Origin.SOURCE, data)

        # Load only roles
        result = backend.get(Origin.SOURCE, resource_types=["roles"])
        assert "role-1" in result.source["roles"]
        assert "dash-1" not in result.source["dashboards"], "dashboards should not be loaded"

    def test_state_loads_all_when_no_resource_types(self, tmp_path):
        """Regression: no resource_types loads all (existing behavior unchanged)."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["roles"]["role-1"] = {"id": "role-1"}
        data.source["dashboards"]["dash-1"] = {"id": "dash-1"}
        backend.put(Origin.SOURCE, data)

        result = backend.get(Origin.SOURCE, resource_types=None)
        assert "role-1" in result.source["roles"]
        assert "dash-1" in result.source["dashboards"]

    def test_state_minimize_reads_flag(self, tmp_path):
        """State._minimize_reads is True when resource_types is set."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_types=["roles"],
        )
        assert state._minimize_reads is True

    def test_state_no_minimize_reads_by_default(self, tmp_path):
        """Regression: State._minimize_reads is False when resource_types is None."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
        )
        assert state._minimize_reads is False


class TestMinimizeReadsCLIValidation:
    def test_minimize_reads_requires_resource_per_file(self, runner):
        """--minimize-reads without --resource-per-file must fail with error."""
        result = runner.invoke(
            cli,
            [
                "sync",
                "--minimize-reads",
                "--resources=roles",
                "--source-api-key=x",
                "--destination-api-key=y",
            ],
        )
        assert result.exit_code != 0
        assert "resource-per-file" in (result.output + str(result.exception)).lower()

    def test_minimize_reads_requires_resources_flag(self, runner):
        """--minimize-reads without --resources must fail with error."""
        result = runner.invoke(
            cli,
            [
                "sync",
                "--minimize-reads",
                "--resource-per-file",
                "--source-api-key=x",
                "--destination-api-key=y",
            ],
        )
        assert result.exit_code != 0
        assert "resources" in (result.output + str(result.exception)).lower()

    def test_minimize_reads_not_available_on_diffs_command(self, runner):
        """--minimize-reads must not be accepted by the diffs command."""
        result = runner.invoke(
            cli,
            ["diffs", "--minimize-reads", "--source-api-key=x", "--destination-api-key=y"],
        )
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()

    def test_minimize_reads_not_available_on_import_command(self, runner):
        """--minimize-reads must not be accepted by the import command."""
        result = runner.invoke(
            cli,
            ["import", "--minimize-reads", "--source-api-key=x"],
        )
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()

    def test_minimize_reads_cannot_be_combined_with_cleanup(self, runner):
        """--minimize-reads + --cleanup must be rejected before any I/O."""
        result = runner.invoke(
            cli,
            [
                "sync",
                "--minimize-reads",
                "--resource-per-file",
                "--resources=roles",
                "--cleanup=Force",
                "--source-api-key=x",
                "--destination-api-key=y",
            ],
        )
        assert result.exit_code != 0
        assert "cleanup" in (result.output + str(result.exception)).lower()


class TestS3TypeScopedGet:
    def test_s3_get_type_scoped_uses_per_type_prefix(self):
        """S3 get() with resource_types uses type-specific prefix, not broad prefix."""
        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            # Empty paginated response
            mock_client.list_objects_v2.return_value = {"IsTruncated": False}

            from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

            backend = AWSS3Bucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={
                    "aws_bucket_name": "test-bucket",
                    "aws_region_name": "us-east-1",
                    "aws_access_key_id": "",
                    "aws_secret_access_key": "",
                    "aws_session_token": "",
                },
            )

            backend.get(Origin.SOURCE, resource_types=["roles"])

            # Should list with type-specific prefix, not broad prefix
            call_kwargs = mock_client.list_objects_v2.call_args_list[0][1]
            assert call_kwargs["Prefix"] == "resources/source/roles."
            assert "dashboard" not in call_kwargs["Prefix"]

    def test_s3_get_broad_prefix_when_no_resource_types(self):
        """S3 get() without resource_types uses the broad prefix (existing behavior)."""
        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.list_objects_v2.return_value = {"IsTruncated": False}

            from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

            backend = AWSS3Bucket(
                source_resources_path="resources/source",
                destination_resources_path="resources/destination",
                config={
                    "aws_bucket_name": "test-bucket",
                    "aws_region_name": "us-east-1",
                    "aws_access_key_id": "",
                    "aws_secret_access_key": "",
                    "aws_session_token": "",
                },
            )

            backend.get(Origin.SOURCE, resource_types=None)

            call_kwargs = mock_client.list_objects_v2.call_args_list[0][1]
            assert call_kwargs["Prefix"] == "resources/source"


# ---------------------------------------------------------------------------
# GREEN/GREEN Regression Tests — must pass BEFORE and AFTER the fix
# ---------------------------------------------------------------------------


class TestMonitorsConnectIdRegression:
    """Existing monitors connect_id behavior must be unchanged after the synthetics fix."""

    def _make_monitors(self, destination_state=None):
        from collections import defaultdict
        from datadog_sync.model.monitors import Monitors

        mock_config = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        return Monitors(mock_config)

    def test_composite_monitor_remaps_regular_monitor_id(self):
        """Composite monitor query remapped via monitors dict when ID found directly."""
        dst = {
            "monitors": {"12345": {"id": "99999"}},
            "synthetics_tests": {},
            "service_level_objectives": {},
        }
        monitors = self._make_monitors(dst)
        r_obj = {"type": "composite", "query": "12345"}
        result = monitors.connect_id("query", r_obj, "monitors")
        assert r_obj["query"] == "99999"
        assert result == []

    def test_slo_alert_monitor_remaps_slo_id(self):
        """SLO alert monitor query remapped via service_level_objectives lookup."""
        dst = {
            "monitors": {},
            "synthetics_tests": {},
            "service_level_objectives": {"slo-src-id": {"id": "slo-dest-id"}},
        }
        monitors = self._make_monitors(dst)
        r_obj = {"type": "slo alert", "query": 'error_budget("slo-src-id").'}
        result = monitors.connect_id("query", r_obj, "service_level_objectives")
        assert "slo-dest-id" in r_obj["query"]
        assert result == []

    def test_non_composite_query_returns_none(self):
        """Non-composite, non-slo-alert query key returns None (base class fallthrough)."""
        dst = {"monitors": {}, "synthetics_tests": {}, "service_level_objectives": {}}
        monitors = self._make_monitors(dst)
        r_obj = {"type": "metric alert", "query": "avg:system.cpu.user{*}"}
        result = monitors.connect_id("query", r_obj, "monitors")
        assert result is None

    def test_principals_remapping_unchanged(self):
        """Principal remapping is unaffected by the synthetics bypass fix."""
        dst = {
            "monitors": {},
            "synthetics_tests": {},
            "service_level_objectives": {},
            "users": {"src-user-id": {"id": "dst-user-id"}},
            "roles": {},
            "teams": {},
        }
        monitors = self._make_monitors(dst)
        r_obj = {"principals": ["user:src-user-id"]}
        result = monitors.connect_id("principals", r_obj, "users")
        assert r_obj["principals"][0] == "user:dst-user-id"
        assert result == []


class TestSLOConnectIdRegression:
    """Existing SLO connect_id behavior must be unchanged after the fix."""

    def _make_slo(self, destination_state=None):
        from collections import defaultdict
        from datadog_sync.model.service_level_objectives import ServiceLevelObjectives

        mock_config = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        return ServiceLevelObjectives(mock_config)

    def test_slo_remaps_regular_monitor_id(self):
        """SLO monitor_ids remapped when monitor found directly in destination state."""
        dst = {"monitors": {"12345": {"id": "99999"}}, "synthetics_tests": {}}
        slo = self._make_slo(dst)
        r_obj = {"monitor_ids": [12345]}
        result = slo.connect_id("monitor_ids", r_obj, "monitors")
        assert r_obj["monitor_ids"][0] == "99999"
        assert result == []

    def test_slo_remaps_synthetics_monitor_in_full_load(self):
        """SLO falls back to synthetics_tests scan when monitor not found directly."""
        dst = {
            "monitors": {},
            "synthetics_tests": {"pub-abc#12345": {"monitor_id": 99999}},
        }
        slo = self._make_slo(dst)
        r_obj = {"monitor_ids": [12345]}
        result = slo.connect_id("monitor_ids", r_obj, "monitors")
        assert r_obj["monitor_ids"][0] == 99999
        assert result == []


class TestSyntheticsTestSuitesConnectIdRegression:
    """Existing SyntheticsTestSuites connect_id behavior unchanged after fix."""

    def _make_test_suites(self, destination_state=None):
        from collections import defaultdict
        from datadog_sync.model.synthetics_test_suites import SyntheticsTestSuites

        mock_config = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        return SyntheticsTestSuites(mock_config)

    def test_connect_id_remaps_in_full_load(self):
        """connect_id remaps public_id via startswith scan when all data is loaded."""
        dst = {"synthetics_tests": {"pub-abc#12345": {"public_id": "dest-pub-abc"}}}
        suites = self._make_test_suites(dst)
        r_obj = {"public_id": "pub-abc"}
        result = suites.connect_id("public_id", r_obj, "synthetics_tests")
        assert r_obj["public_id"] == "dest-pub-abc"
        assert result == []


class TestSyntheticsGlobalVariablesConnectIdRegression:
    """Existing SyntheticsGlobalVariables connect_id behavior unchanged after fix."""

    def _make_global_vars(self, destination_state=None):
        from collections import defaultdict
        from datadog_sync.model.synthetics_global_variables import SyntheticsGlobalVariables

        mock_config = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        return SyntheticsGlobalVariables(mock_config)

    def test_connect_id_remaps_in_full_load(self):
        """connect_id remaps value via startswith scan when all data is loaded."""
        dst = {"synthetics_tests": {"pub-abc#12345": {"public_id": "dest-pub-abc"}}}
        gvars = self._make_global_vars(dst)
        r_obj = {"formula": "pub-abc"}
        result = gvars.connect_id("formula", r_obj, "synthetics_tests")
        assert r_obj["formula"] == "dest-pub-abc"
        assert result == []


# ---------------------------------------------------------------------------
# RED/GREEN Tests — fail BEFORE fix, pass AFTER
# ---------------------------------------------------------------------------


class TestEnsureResourceTypeLoaded:
    """State.ensure_resource_type_loaded() — new bulk-load method."""

    def _make_state(self, tmp_path, resource_types=None):
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir(exist_ok=True)
        Path(dst_path).mkdir(exist_ok=True)
        kwargs = dict(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        if resource_types is not None:
            kwargs["resource_types"] = resource_types
        return State(**kwargs)

    def _write_synthetics(self, tmp_path, src=True, dst=True):
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir(exist_ok=True)
        Path(dst_path).mkdir(exist_ok=True)
        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        if src:
            data.source["synthetics_tests"]["pub-id#12345"] = {"public_id": "pub-id", "monitor_id": 12345}
        if dst:
            data.destination["synthetics_tests"]["pub-id#12345"] = {"public_id": "dest-pub-id", "monitor_id": 99999}
        if src and dst:
            origin = Origin.ALL
        elif src:
            origin = Origin.SOURCE
        else:
            origin = Origin.DESTINATION
        backend.put(origin, data)

    def test_noop_when_not_minimize_reads(self, tmp_path):
        """ensure_resource_type_loaded is a no-op when _minimize_reads is False."""
        state = self._make_state(tmp_path)  # no resource_types → full load
        assert state._minimize_reads is False
        with patch.object(state._storage, "get", wraps=state._storage.get) as mock_get:
            state.ensure_resource_type_loaded("synthetics_tests")
            mock_get.assert_not_called()

    def test_loads_source_and_destination(self, tmp_path):
        """ensure_resource_type_loaded loads both source and destination into state."""
        self._write_synthetics(tmp_path)
        state = self._make_state(tmp_path, resource_types=["dashboards"])
        assert not state._data.source.get("synthetics_tests")

        state.ensure_resource_type_loaded("synthetics_tests")

        assert "pub-id#12345" in state._data.source["synthetics_tests"]
        assert "pub-id#12345" in state._data.destination["synthetics_tests"]

    def test_idempotent_second_call(self, tmp_path):
        """ensure_resource_type_loaded calls _storage.get exactly once on repeated calls."""
        self._write_synthetics(tmp_path, dst=False)
        state = self._make_state(tmp_path, resource_types=["dashboards"])
        with patch.object(state._storage, "get", wraps=state._storage.get) as mock_get:
            state.ensure_resource_type_loaded("synthetics_tests")
            state.ensure_resource_type_loaded("synthetics_tests")
            assert mock_get.call_count == 1

    def test_does_not_overwrite_existing_entries(self, tmp_path):
        """ensure_resource_type_loaded uses insert-if-absent: never overwrites modified entries."""
        self._write_synthetics(tmp_path, src=False, dst=True)
        state = self._make_state(tmp_path, resource_types=["dashboards"])
        # Simulate a mid-sync modification
        modified = {"public_id": "MODIFIED", "monitor_id": 0}
        state._data.destination["synthetics_tests"]["pub-id#12345"] = modified

        state.ensure_resource_type_loaded("synthetics_tests")

        assert state._data.destination["synthetics_tests"]["pub-id#12345"] == modified

    def test_handles_empty_storage_gracefully(self, tmp_path):
        """ensure_resource_type_loaded handles missing resource type without error."""
        state = self._make_state(tmp_path, resource_types=["dashboards"])
        # No synthetics_tests files in storage — should not raise
        state.ensure_resource_type_loaded("synthetics_tests")
        # defaultdict should NOT be polluted with an empty entry
        assert state._data.source.get("synthetics_tests") is None

    def test_populates_ensure_attempted_for_bulk_loaded_keys(self, tmp_path):
        """Bulk-loaded keys are added to _ensure_attempted to prevent redundant I/O."""
        self._write_synthetics(tmp_path, dst=False)
        state = self._make_state(tmp_path, resource_types=["dashboards"])
        state.ensure_resource_type_loaded("synthetics_tests")
        assert ("synthetics_tests", "pub-id#12345") in state._ensure_attempted


class TestMonitorsCompositeMinimizeReads:
    """Monitors composite connect_id remaps synthetics monitor IDs in minimize-reads mode."""

    def _setup(self, tmp_path):
        from datadog_sync.model.monitors import Monitors
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.destination["synthetics_tests"]["pub-abc#12345"] = {"public_id": "pub-abc", "monitor_id": 99999}
        backend.put(Origin.DESTINATION, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
            resource_types=["monitors"],
        )
        mock_config = MagicMock()
        mock_config.state = state
        return state, Monitors(mock_config)

    def test_composite_monitor_remaps_synthetics_monitor_id(self, tmp_path):
        """Composite monitor query remapped via synthetics_tests bulk-load in minimize-reads mode."""
        state, monitors = self._setup(tmp_path)
        r_obj = {"type": "composite", "query": "12345"}
        result = monitors.connect_id("query", r_obj, "monitors")
        assert r_obj["query"] == "99999"
        assert result == []

    def test_non_composite_does_not_trigger_bulk_load(self, tmp_path):
        """Non-composite monitors do not trigger ensure_resource_type_loaded."""
        state, monitors = self._setup(tmp_path)
        r_obj = {"type": "metric alert", "query": "avg:system.cpu.user{*}"}
        monitors.connect_id("query", r_obj, "monitors")
        assert "synthetics_tests" not in state._bulk_loaded_types

    def test_slo_alert_monitor_accesses_slo_state_lazily(self, tmp_path):
        """SLO alert branch accesses service_level_objectives without crashing in minimize-reads mode."""
        state, monitors = self._setup(tmp_path)
        # SLO not in state — should return failed connection, not crash
        r_obj = {"type": "slo alert", "query": 'error_budget("slo-src-id").'}
        result = monitors.connect_id("query", r_obj, "service_level_objectives")
        assert "slo-src-id" in result


class TestSLOConnectIdMinimizeReads:
    """SLO connect_id remaps synthetics monitor IDs in minimize-reads mode."""

    def _setup(self, tmp_path):
        from datadog_sync.model.service_level_objectives import ServiceLevelObjectives
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.destination["synthetics_tests"]["pub-abc#12345"] = {"public_id": "pub-abc", "monitor_id": 99999}
        backend.put(Origin.DESTINATION, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
            resource_types=["service_level_objectives"],
        )
        mock_config = MagicMock()
        mock_config.state = state
        return state, ServiceLevelObjectives(mock_config)

    def test_slo_monitor_ids_remaps_synthetics_monitor(self, tmp_path):
        """SLO monitor_ids remapped via synthetics_tests fallback in minimize-reads mode."""
        state, slo = self._setup(tmp_path)
        r_obj = {"monitor_ids": [12345]}
        result = slo.connect_id("monitor_ids", r_obj, "monitors")
        assert r_obj["monitor_ids"][0] == 99999
        assert result == []

    def test_slo_regular_monitor_does_not_trigger_bulk_load(self, tmp_path):
        """SLO does not trigger ensure_resource_type_loaded when monitor found directly."""
        from datadog_sync.model.service_level_objectives import ServiceLevelObjectives
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
            resource_types=["service_level_objectives"],
        )
        # Simulate resource_connections pipeline having loaded the monitor
        state._data.destination["monitors"]["12345"] = {"id": "99999"}

        mock_config = MagicMock()
        mock_config.state = state
        slo = ServiceLevelObjectives(mock_config)

        r_obj = {"monitor_ids": [12345]}
        slo.connect_id("monitor_ids", r_obj, "monitors")
        assert "synthetics_tests" not in state._bulk_loaded_types


class TestSyntheticsTestSuitesMinimizeReads:
    """SyntheticsTestSuites connect_id remaps public_id in minimize-reads mode."""

    def test_connect_id_remaps_via_startswith_after_bulk_load(self, tmp_path):
        """connect_id remaps public_id after bulk-loading synthetics_tests."""
        from datadog_sync.model.synthetics_test_suites import SyntheticsTestSuites
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.destination["synthetics_tests"]["pub-abc#12345"] = {"public_id": "dest-pub-abc"}
        backend.put(Origin.DESTINATION, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
            resource_types=["synthetics_test_suites"],
        )
        mock_config = MagicMock()
        mock_config.state = state
        suites = SyntheticsTestSuites(mock_config)

        r_obj = {"public_id": "pub-abc"}
        result = suites.connect_id("public_id", r_obj, "synthetics_tests")
        assert r_obj["public_id"] == "dest-pub-abc"
        assert result == []


class TestSyntheticsGlobalVariablesMinimizeReads:
    """SyntheticsGlobalVariables connect_id remaps via startswith in minimize-reads mode."""

    def test_connect_id_remaps_via_startswith_after_bulk_load(self, tmp_path):
        """connect_id remaps value after bulk-loading synthetics_tests."""
        from datadog_sync.model.synthetics_global_variables import SyntheticsGlobalVariables
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.destination["synthetics_tests"]["pub-abc#12345"] = {"public_id": "dest-pub-abc"}
        backend.put(Origin.DESTINATION, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
            resource_types=["synthetics_global_variables"],
        )
        mock_config = MagicMock()
        mock_config.state = state
        gvars = SyntheticsGlobalVariables(mock_config)

        r_obj = {"formula": "pub-abc"}
        result = gvars.connect_id("formula", r_obj, "synthetics_tests")
        assert r_obj["formula"] == "dest-pub-abc"
        assert result == []
