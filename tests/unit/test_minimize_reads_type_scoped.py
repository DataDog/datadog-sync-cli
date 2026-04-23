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
