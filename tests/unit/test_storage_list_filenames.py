# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from unittest.mock import MagicMock, patch

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket
from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer
from datadog_sync.utils.storage.gcs_bucket import GCSBucket
from datadog_sync.utils.storage.local_file import LocalFile


def _make_local(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()
    return (
        LocalFile(
            source_resources_path=str(src),
            destination_resources_path=str(dst),
            resource_per_file=True,
        ),
        src,
        dst,
    )


class TestLocalFileListFilenames:
    def test_empty_directory_returns_empty_set(self, tmp_path):
        backend, _, _ = _make_local(tmp_path)
        assert backend.list_filenames(Origin.SOURCE, "monitors") == set()

    def test_returns_full_filenames_opaque(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        (src / "monitors.abc.json").write_text("{}")
        (src / "monitors.x.y.json").write_text("{}")  # multi-dot ID preserved
        result = backend.list_filenames(Origin.SOURCE, "monitors")
        assert result == {"monitors.abc.json", "monitors.x.y.json"}

    def test_ignores_non_json(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        (src / "monitors.abc.json").write_text("{}")
        (src / "monitors.txt").write_text("nope")
        (src / "readme").write_text("nope")
        assert backend.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_ignores_combined_resource_file(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        (src / "monitors.json").write_text("{}")
        (src / "monitors.abc.json").write_text("{}")
        assert backend.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_ignores_other_resource_types(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        (src / "monitors.abc.json").write_text("{}")
        (src / "dashboards.def.json").write_text("{}")
        assert backend.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}
        assert backend.list_filenames(Origin.SOURCE, "dashboards") == {"dashboards.def.json"}

    def test_origin_isolation(self, tmp_path):
        backend, src, dst = _make_local(tmp_path)
        (src / "monitors.in_source.json").write_text("{}")
        (dst / "monitors.in_dest.json").write_text("{}")
        assert backend.list_filenames(Origin.SOURCE, "monitors") == {"monitors.in_source.json"}
        assert backend.list_filenames(Origin.DESTINATION, "monitors") == {"monitors.in_dest.json"}

    def test_missing_directory_returns_empty(self, tmp_path):
        # Source path that doesn't exist on disk (e.g., never written)
        backend = LocalFile(
            source_resources_path=str(tmp_path / "does_not_exist"),
            destination_resources_path=str(tmp_path / "dest_missing"),
            resource_per_file=True,
        )
        assert backend.list_filenames(Origin.SOURCE, "monitors") == set()
        assert backend.list_filenames(Origin.DESTINATION, "monitors") == set()


@pytest.fixture
def mock_s3_client():
    with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_boto3, mock_client


def _make_s3():
    return AWSS3Bucket(
        config={
            "aws_bucket_name": "test-bucket",
            "aws_region_name": "us-east-1",
            "aws_access_key_id": "AKID",
            "aws_secret_access_key": "SECRET",
            "aws_session_token": "",
        },
        resource_per_file=True,
    )


class TestAWSS3ListFilenames:
    def test_empty_prefix_returns_empty(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.list_objects_v2.return_value = {"IsTruncated": False}
        bucket = _make_s3()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == set()

    def test_returns_full_filenames_opaque(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "resources/source/monitors.abc.json"},
                {"Key": "resources/source/monitors.x.y.json"},
            ],
            "IsTruncated": False,
        }
        bucket = _make_s3()
        result = bucket.list_filenames(Origin.SOURCE, "monitors")
        assert result == {"monitors.abc.json", "monitors.x.y.json"}
        # Listing must be scoped to the per-type prefix
        mock_client.list_objects_v2.assert_called_with(Bucket="test-bucket", Prefix="resources/source/monitors.")

    def test_ignores_non_json(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "resources/source/monitors.abc.json"},
                {"Key": "resources/source/monitors.txt"},
            ],
            "IsTruncated": False,
        }
        bucket = _make_s3()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_ignores_combined_resource_file(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "resources/source/monitors.json"},
                {"Key": "resources/source/monitors.abc.json"},
            ],
            "IsTruncated": False,
        }
        bucket = _make_s3()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_origin_isolation(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.list_objects_v2.return_value = {"IsTruncated": False}
        bucket = _make_s3()
        bucket.list_filenames(Origin.SOURCE, "monitors")
        bucket.list_filenames(Origin.DESTINATION, "monitors")
        prefixes_called = [call.kwargs.get("Prefix") for call in mock_client.list_objects_v2.call_args_list]
        assert "resources/source/monitors." in prefixes_called
        assert "resources/destination/monitors." in prefixes_called

    def test_pagination(self, mock_s3_client):
        _, mock_client = mock_s3_client
        # First page truncated, second page final
        mock_client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "resources/source/monitors.a.json"}],
                "IsTruncated": True,
                "NextContinuationToken": "tok",
            },
            {
                "Contents": [{"Key": "resources/source/monitors.b.json"}],
                "IsTruncated": False,
            },
        ]
        bucket = _make_s3()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {
            "monitors.a.json",
            "monitors.b.json",
        }


@pytest.fixture
def mock_gcs_client():
    with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage") as mock_storage:
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        yield mock_storage, mock_client, mock_bucket


def _make_gcs():
    return GCSBucket(
        config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None},
        resource_per_file=True,
    )


class TestGCSListFilenames:
    def test_empty_prefix_returns_empty(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        mock_bucket.list_blobs.return_value = []
        bucket = _make_gcs()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == set()

    def test_returns_full_filenames_opaque(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        b1 = MagicMock()
        b1.name = "resources/source/monitors.abc.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.x.y.json"
        mock_bucket.list_blobs.return_value = [b1, b2]
        bucket = _make_gcs()
        result = bucket.list_filenames(Origin.SOURCE, "monitors")
        assert result == {"monitors.abc.json", "monitors.x.y.json"}
        mock_bucket.list_blobs.assert_called_with(prefix="resources/source/monitors.")

    def test_ignores_non_json(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        b1 = MagicMock()
        b1.name = "resources/source/monitors.abc.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.txt"
        mock_bucket.list_blobs.return_value = [b1, b2]
        bucket = _make_gcs()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_ignores_combined_resource_file(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        b1 = MagicMock()
        b1.name = "resources/source/monitors.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.abc.json"
        mock_bucket.list_blobs.return_value = [b1, b2]
        bucket = _make_gcs()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}


@pytest.fixture
def mock_azure_container():
    with patch("datadog_sync.utils.storage.azure_blob_container.ContainerClient") as mock_cls:
        mock_container = MagicMock()
        mock_cls.from_connection_string.return_value = mock_container
        yield mock_cls, mock_container


def _make_azure():
    return AzureBlobContainer(
        config={
            "azure_container_name": "test-container",
            "azure_storage_connection_string": "conn",
            "azure_storage_account_name": None,
            "azure_storage_account_key": None,
        },
        resource_per_file=True,
    )


class TestAzureListFilenames:
    def test_empty_prefix_returns_empty(self, mock_azure_container):
        _, mock_container = mock_azure_container
        mock_container.list_blobs.return_value = []
        bucket = _make_azure()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == set()

    def test_returns_full_filenames_opaque(self, mock_azure_container):
        _, mock_container = mock_azure_container
        b1 = MagicMock()
        b1.name = "resources/source/monitors.abc.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.x.y.json"
        mock_container.list_blobs.return_value = [b1, b2]
        bucket = _make_azure()
        result = bucket.list_filenames(Origin.SOURCE, "monitors")
        assert result == {"monitors.abc.json", "monitors.x.y.json"}
        mock_container.list_blobs.assert_called_with(name_starts_with="resources/source/monitors.")

    def test_ignores_non_json(self, mock_azure_container):
        _, mock_container = mock_azure_container
        b1 = MagicMock()
        b1.name = "resources/source/monitors.abc.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.txt"
        mock_container.list_blobs.return_value = [b1, b2]
        bucket = _make_azure()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}

    def test_ignores_combined_resource_file(self, mock_azure_container):
        _, mock_container = mock_azure_container
        b1 = MagicMock()
        b1.name = "resources/source/monitors.json"
        b2 = MagicMock()
        b2.name = "resources/source/monitors.abc.json"
        mock_container.list_blobs.return_value = [b1, b2]
        bucket = _make_azure()
        assert bucket.list_filenames(Origin.SOURCE, "monitors") == {"monitors.abc.json"}


class TestBaseStorageDefault:
    """Out-of-tree backends that don't implement list_filenames must raise NotImplementedError."""

    def test_default_raises(self):
        from datadog_sync.utils.storage._base_storage import BaseStorage

        class FakeBackend(BaseStorage):
            def get(self, origin, resource_types=None):
                pass

            def get_single(self, resource_type, resource_id):
                return None, None

            def put(self, origin, data):
                pass

        backend = FakeBackend()
        with pytest.raises(NotImplementedError, match="list_filenames"):
            backend.list_filenames(Origin.SOURCE, "monitors")
