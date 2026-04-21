# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound

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

            GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": "/path/to/key.json"})

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

        listed_blob = MagicMock()
        listed_blob.name = "resources/source/monitors.json"

        fresh_blob = MagicMock()
        fresh_blob.download_as_text.return_value = json.dumps({"id1": {"name": "monitor1"}})

        mock_bucket.list_blobs.return_value = [listed_blob]
        mock_bucket.blob.return_value = fresh_blob

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        # Should use fresh blob reference, not the listed blob
        mock_bucket.blob.assert_called_with("resources/source/monitors.json")
        fresh_blob.download_as_text.assert_called_once()
        listed_blob.download_as_text.assert_not_called()
        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert len(data.destination) == 0

    def test_get_destination(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        listed_blob = MagicMock()
        listed_blob.name = "resources/destination/dashboards.json"

        fresh_blob = MagicMock()
        fresh_blob.download_as_text.return_value = json.dumps({"id2": {"title": "dash1"}})

        mock_bucket.list_blobs.return_value = [listed_blob]
        mock_bucket.blob.return_value = fresh_blob

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.DESTINATION)

        mock_bucket.blob.assert_called_with("resources/destination/dashboards.json")
        fresh_blob.download_as_text.assert_called_once()
        listed_blob.download_as_text.assert_not_called()
        assert len(data.source) == 0
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_all(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        source_listed = MagicMock()
        source_listed.name = "resources/source/monitors.json"

        dest_listed = MagicMock()
        dest_listed.name = "resources/destination/dashboards.json"

        source_fresh = MagicMock()
        source_fresh.download_as_text.return_value = json.dumps({"id1": {"name": "monitor1"}})

        dest_fresh = MagicMock()
        dest_fresh.download_as_text.return_value = json.dumps({"id2": {"title": "dash1"}})

        mock_bucket.list_blobs.side_effect = [[source_listed], [dest_listed]]
        mock_bucket.blob.side_effect = [source_fresh, dest_fresh]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.ALL)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}
        source_listed.download_as_text.assert_not_called()
        dest_listed.download_as_text.assert_not_called()

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

    def test_get_source_handles_not_found(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/source/roles.id1.json"
        blob2 = MagicMock()
        blob2.name = "resources/source/roles.id2.json"
        blob3 = MagicMock()
        blob3.name = "resources/source/roles.id3.json"
        mock_bucket.list_blobs.return_value = [blob1, blob2, blob3]

        fresh1 = MagicMock()
        fresh1.download_as_text.return_value = json.dumps({"id1": {"name": "role1"}})
        fresh2 = MagicMock()
        fresh2.download_as_text.side_effect = NotFound("object deleted")
        fresh3 = MagicMock()
        fresh3.download_as_text.return_value = json.dumps({"id3": {"name": "role3"}})
        mock_bucket.blob.side_effect = [fresh1, fresh2, fresh3]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        assert "id1" in data.source["roles"]
        assert "id2" not in data.source["roles"]
        assert "id3" in data.source["roles"]

    def test_get_destination_handles_not_found(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/destination/users.id1.json"
        blob2 = MagicMock()
        blob2.name = "resources/destination/users.id2.json"
        blob3 = MagicMock()
        blob3.name = "resources/destination/users.id3.json"
        mock_bucket.list_blobs.return_value = [blob1, blob2, blob3]

        fresh1 = MagicMock()
        fresh1.download_as_text.return_value = json.dumps({"id1": {"email": "a@test.com"}})
        fresh2 = MagicMock()
        fresh2.download_as_text.side_effect = NotFound("object deleted")
        fresh3 = MagicMock()
        fresh3.download_as_text.return_value = json.dumps({"id3": {"email": "c@test.com"}})
        mock_bucket.blob.side_effect = [fresh1, fresh2, fresh3]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.DESTINATION)

        assert "id1" in data.destination["users"]
        assert "id2" not in data.destination["users"]
        assert "id3" in data.destination["users"]

    def test_get_source_handles_invalid_json(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blob1 = MagicMock()
        blob1.name = "resources/source/roles.id1.json"
        blob2 = MagicMock()
        blob2.name = "resources/source/roles.id2.json"
        mock_bucket.list_blobs.return_value = [blob1, blob2]

        fresh1 = MagicMock()
        fresh1.download_as_text.return_value = "not valid json {{{"
        fresh2 = MagicMock()
        fresh2.download_as_text.return_value = json.dumps({"id2": {"name": "role2"}})
        mock_bucket.blob.side_effect = [fresh1, fresh2]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        assert "id2" in data.source["roles"]

    def test_get_mixed_errors_and_success(self, mock_gcs_client):
        _, _, mock_bucket = mock_gcs_client

        blobs = [
            MagicMock(name="resources/source/roles.good1.json"),
            MagicMock(name="resources/source/roles.deleted.json"),
            MagicMock(name="resources/source/roles.badjson.json"),
            MagicMock(name="resources/source/roles.good2.json"),
        ]
        # MagicMock(name=...) sets the mock's internal name, not .name attribute
        for blob, n in zip(
            blobs,
            [
                "resources/source/roles.good1.json",
                "resources/source/roles.deleted.json",
                "resources/source/roles.badjson.json",
                "resources/source/roles.good2.json",
            ],
        ):
            blob.name = n

        non_json = MagicMock()
        non_json.name = "resources/source/readme.txt"
        blobs.append(non_json)

        mock_bucket.list_blobs.return_value = blobs

        fresh_good1 = MagicMock()
        fresh_good1.download_as_text.return_value = json.dumps({"good1": {"name": "admin"}})
        fresh_deleted = MagicMock()
        fresh_deleted.download_as_text.side_effect = NotFound("gone")
        fresh_badjson = MagicMock()
        fresh_badjson.download_as_text.return_value = "{corrupt"
        fresh_good2 = MagicMock()
        fresh_good2.download_as_text.return_value = json.dumps({"good2": {"name": "viewer"}})
        mock_bucket.blob.side_effect = [fresh_good1, fresh_deleted, fresh_badjson, fresh_good2]

        bucket = GCSBucket(config={"gcs_bucket_name": "test-bucket", "gcs_service_account_key_file": None})
        data = bucket.get(Origin.SOURCE)

        assert data.source["roles"]["good1"] == {"name": "admin"}
        assert "deleted" not in data.source["roles"]
        assert data.source["roles"]["good2"] == {"name": "viewer"}
        # non-.json file should not trigger a blob() call
        assert mock_bucket.blob.call_count == 4
