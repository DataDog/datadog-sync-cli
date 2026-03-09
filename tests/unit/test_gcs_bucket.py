# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
from unittest.mock import MagicMock, patch

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.storage.gcs_bucket import GCSBucket


@pytest.fixture
def mock_gcs_client():
    with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage") as mock_storage:
        mock_client = MagicMock()
        mock_storage.Client.return_value = mock_client
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        yield mock_storage, mock_client, mock_bucket


class TestGCSBucket:
    def test_init_with_service_account_key(self):
        with patch("datadog_sync.utils.storage.gcs_bucket.gcs_storage") as mock_storage:
            mock_client = MagicMock()
            mock_storage.Client.from_service_account_json.return_value = mock_client
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket

            GCSBucket(
                config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": "/path/to/key.json"}
            )

            mock_storage.Client.from_service_account_json.assert_called_once_with("/path/to/key.json")
            mock_client.bucket.assert_called_once_with("test-bucket")

    def test_init_with_default_credentials(self, mock_gcs_client):
        mock_storage, mock_client, mock_bucket = mock_gcs_client

        GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})

        mock_storage.Client.assert_called_once()
        mock_client.bucket.assert_called_once_with("test-bucket")

    def test_init_no_config(self):
        with pytest.raises(ValueError, match="No GCS configuration passed in"):
            GCSBucket(config=None)

    def test_init_missing_bucket_name(self, mock_gcs_client):
        with pytest.raises(ValueError, match="GCS bucket name is required"):
            GCSBucket(config={"gcs_bucket_name": "", "gcs_service_account_key_file": None})

    def test_get_source(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/source/monitors.json"
        blob1.download_as_text.return_value = json.dumps({"id1": {"name": "monitor1"}})

        mock_bucket.list_blobs.return_value = [blob1]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert len(data.destination) == 0

    def test_get_destination(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/destination/dashboards.json"
        blob1.download_as_text.return_value = json.dumps({"id2": {"title": "dash1"}})

        mock_bucket.list_blobs.return_value = [blob1]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.DESTINATION)

        assert len(data.source) == 0
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_all(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        source_blob = MagicMock()
        source_blob.name = "resources/source/monitors.json"
        source_blob.download_as_text.return_value = json.dumps({"id1": {"name": "monitor1"}})

        dest_blob = MagicMock()
        dest_blob.name = "resources/destination/dashboards.json"
        dest_blob.download_as_text.return_value = json.dumps({"id2": {"title": "dash1"}})

        mock_bucket.list_blobs.side_effect = [[source_blob], [dest_blob]]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.ALL)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_skips_non_json(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/source/readme.txt"

        mock_bucket.list_blobs.return_value = [blob1]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        assert len(data.source) == 0

    def test_put_single_file(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_bucket.list_blobs.return_value = []

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "monitor1"}}

        bucket.put(Origin.SOURCE, data)

        mock_bucket.blob.assert_called_with("resources/source/monitors.json")
        mock_blob.upload_from_string.assert_called_once_with(
            json.dumps({"id1": {"name": "monitor1"}}), content_type="application/json"
        )

    def test_put_resource_per_file(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_bucket.list_blobs.return_value = []

        bucket = GCSBucket(
            config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None},
            resource_per_file=True,
        )
        data = bucket.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "mon1"}, "id2": {"name": "mon2"}}

        bucket.put(Origin.SOURCE, data)

        assert mock_bucket.blob.call_count == 2
        mock_bucket.blob.assert_any_call("resources/source/monitors.id1.json")
        mock_bucket.blob.assert_any_call("resources/source/monitors.id2.json")
