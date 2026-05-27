# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError
from google.api_core.exceptions import NotFound

from datadog_sync.constants import Origin
from datadog_sync.utils.storage._base_storage import BaseStorage
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


class TestLocalFileDelete:
    def test_delete_existing_file(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        f = src / "monitors.abc.json"
        f.write_text("{}")
        backend.delete(Origin.SOURCE, "monitors.abc.json")
        assert not f.exists()

    def test_delete_missing_file_no_op(self, tmp_path):
        backend, _, _ = _make_local(tmp_path)
        # Must not raise
        backend.delete(Origin.SOURCE, "monitors.does-not-exist.json")

    def test_delete_origin_isolation(self, tmp_path):
        backend, src, dst = _make_local(tmp_path)
        src_file = src / "monitors.abc.json"
        dst_file = dst / "monitors.abc.json"
        src_file.write_text("{}")
        dst_file.write_text("{}")
        backend.delete(Origin.SOURCE, "monitors.abc.json")
        assert not src_file.exists()
        assert dst_file.exists()


class TestLocalFileDeleteMany:
    def test_mix_present_and_missing(self, tmp_path):
        backend, src, _ = _make_local(tmp_path)
        (src / "monitors.a.json").write_text("{}")
        (src / "monitors.b.json").write_text("{}")
        result = backend.delete_many(
            Origin.SOURCE,
            ["monitors.a.json", "monitors.b.json", "monitors.missing.json"],
        )
        assert result["monitors.a.json"] == "ok"
        assert result["monitors.b.json"] == "ok"
        assert result["monitors.missing.json"] == "ok"  # missing is no-op
        assert not (src / "monitors.a.json").exists()
        assert not (src / "monitors.b.json").exists()

    def test_empty_input(self, tmp_path):
        backend, _, _ = _make_local(tmp_path)
        assert backend.delete_many(Origin.SOURCE, []) == {}


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


class TestAWSS3Delete:
    def test_delete_calls_delete_object(self, mock_s3_client):
        _, mock_client = mock_s3_client
        bucket = _make_s3()
        bucket.delete(Origin.SOURCE, "monitors.abc.json")
        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="resources/source/monitors.abc.json"
        )

    def test_delete_origin_isolation(self, mock_s3_client):
        _, mock_client = mock_s3_client
        bucket = _make_s3()
        bucket.delete(Origin.DESTINATION, "monitors.abc.json")
        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="resources/destination/monitors.abc.json"
        )

    def test_delete_many_loops_per_file(self, mock_s3_client):
        _, mock_client = mock_s3_client
        bucket = _make_s3()
        result = bucket.delete_many(Origin.SOURCE, ["monitors.a.json", "monitors.b.json"])
        assert result == {"monitors.a.json": "ok", "monitors.b.json": "ok"}
        assert mock_client.delete_object.call_count == 2

    def test_delete_many_partial_failure(self, mock_s3_client):
        _, mock_client = mock_s3_client
        mock_client.delete_object.side_effect = [None, Exception("AccessDenied")]
        bucket = _make_s3()
        result = bucket.delete_many(Origin.SOURCE, ["monitors.a.json", "monitors.b.json"])
        assert result["monitors.a.json"] == "ok"
        assert "AccessDenied" in result["monitors.b.json"]


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


class TestGCSDelete:
    def test_delete_calls_blob_delete(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        bucket = _make_gcs()
        bucket.delete(Origin.SOURCE, "monitors.abc.json")
        mock_bucket.blob.assert_called_with("resources/source/monitors.abc.json")
        mock_blob.delete.assert_called_once()

    def test_delete_missing_file_no_op(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        mock_blob = MagicMock()
        mock_blob.delete.side_effect = NotFound("missing")
        mock_bucket.blob.return_value = mock_blob
        bucket = _make_gcs()
        # Must not raise
        bucket.delete(Origin.SOURCE, "monitors.missing.json")

    def test_delete_many_partial_failure(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client
        ok_blob = MagicMock()
        bad_blob = MagicMock()
        bad_blob.delete.side_effect = Exception("PermissionDenied")
        mock_bucket.blob.side_effect = [ok_blob, bad_blob]
        bucket = _make_gcs()
        result = bucket.delete_many(Origin.SOURCE, ["monitors.a.json", "monitors.b.json"])
        assert result["monitors.a.json"] == "ok"
        assert "PermissionDenied" in result["monitors.b.json"]


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


class TestAzureDelete:
    def test_delete_calls_delete_blob(self, mock_azure_container):
        _, mock_container = mock_azure_container
        bucket = _make_azure()
        bucket.delete(Origin.SOURCE, "monitors.abc.json")
        mock_container.delete_blob.assert_called_once_with("resources/source/monitors.abc.json")

    def test_delete_missing_file_no_op(self, mock_azure_container):
        _, mock_container = mock_azure_container
        mock_container.delete_blob.side_effect = ResourceNotFoundError("missing")
        bucket = _make_azure()
        # Must not raise
        bucket.delete(Origin.SOURCE, "monitors.missing.json")

    def test_delete_many_partial_failure(self, mock_azure_container):
        _, mock_container = mock_azure_container
        mock_container.delete_blob.side_effect = [None, Exception("AccessDenied")]
        bucket = _make_azure()
        result = bucket.delete_many(Origin.SOURCE, ["monitors.a.json", "monitors.b.json"])
        assert result["monitors.a.json"] == "ok"
        assert "AccessDenied" in result["monitors.b.json"]


class TestBaseStorageDefaults:
    """Out-of-tree backends that don't implement delete must raise NotImplementedError."""

    def test_default_delete_raises(self):
        class FakeBackend(BaseStorage):
            def get(self, origin, resource_types=None):
                pass

            def get_single(self, resource_type, resource_id):
                return None, None

            def put(self, origin, data):
                pass

        backend = FakeBackend()
        with pytest.raises(NotImplementedError, match="delete"):
            backend.delete(Origin.SOURCE, "monitors.abc.json")

    def test_default_delete_many_uses_default_loop(self):
        """delete_many has a concrete default that loops delete() — so a backend
        that overrides only delete() will get a working delete_many() for free."""

        class PartialBackend(BaseStorage):
            def __init__(self):
                super().__init__()
                self.deleted: list = []

            def get(self, origin, resource_types=None):
                pass

            def get_single(self, resource_type, resource_id):
                return None, None

            def put(self, origin, data):
                pass

            def delete(self, origin, filename):
                self.deleted.append((origin, filename))

        backend = PartialBackend()
        result = backend.delete_many(Origin.SOURCE, ["a.json", "b.json"])
        assert result == {"a.json": "ok", "b.json": "ok"}
        assert backend.deleted == [(Origin.SOURCE, "a.json"), (Origin.SOURCE, "b.json")]
