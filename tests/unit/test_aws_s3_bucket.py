# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket


@pytest.fixture
def mock_s3_client():
    with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_boto3, mock_client


class TestAWSS3Bucket:
    def test_init_with_explicit_credentials(self, mock_s3_client):
        mock_boto3, mock_client = mock_s3_client

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )

        mock_boto3.client.assert_called_once_with(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
            aws_session_token="",
        )
        assert bucket.bucket_name == "test-bucket"

    def test_init_with_default_credentials(self, mock_s3_client):
        mock_boto3, mock_client = mock_s3_client

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": None,
                "aws_access_key_id": None,
                "aws_secret_access_key": None,
                "aws_session_token": None,
            }
        )

        mock_boto3.client.assert_called_once_with("s3")
        assert bucket.bucket_name == "test-bucket"

    def test_init_no_config(self):
        with pytest.raises(ValueError, match="No S3 configuration passed in"):
            AWSS3Bucket(config=None)

    def test_init_missing_bucket_name(self, mock_s3_client):
        with pytest.raises(ValueError, match="AWS S3 bucket name is required"):
            AWSS3Bucket(
                config={
                    "aws_bucket_name": "",
                    "aws_region_name": "us-east-1",
                    "aws_access_key_id": "AKID",
                    "aws_secret_access_key": "SECRET",
                    "aws_session_token": "",
                }
            )

    def test_get_source(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "resources/source/monitors.json"}],
            "IsTruncated": False,
        }
        mock_client.get_object.return_value = {
            "Body": io.BytesIO(json.dumps({"id1": {"name": "monitor1"}}).encode("utf-8"))
        }

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )
        data = bucket.get(Origin.SOURCE)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert len(data.destination) == 0

    def test_get_destination(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "resources/destination/dashboards.json"}],
            "IsTruncated": False,
        }
        mock_client.get_object.return_value = {
            "Body": io.BytesIO(json.dumps({"id2": {"title": "dash1"}}).encode("utf-8"))
        }

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )
        data = bucket.get(Origin.DESTINATION)

        assert len(data.source) == 0
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_all(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "resources/source/monitors.json"}],
                "IsTruncated": False,
            },
            {
                "Contents": [{"Key": "resources/destination/dashboards.json"}],
                "IsTruncated": False,
            },
        ]
        mock_client.get_object.side_effect = [
            {"Body": io.BytesIO(json.dumps({"id1": {"name": "monitor1"}}).encode("utf-8"))},
            {"Body": io.BytesIO(json.dumps({"id2": {"title": "dash1"}}).encode("utf-8"))},
        ]

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )
        data = bucket.get(Origin.ALL)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_skips_non_json(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "resources/source/readme.txt"}],
            "IsTruncated": False,
        }

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )
        data = bucket.get(Origin.SOURCE)

        assert len(data.source) == 0
        mock_client.get_object.assert_not_called()

    def test_put_single_file(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.return_value = {"IsTruncated": False}

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            }
        )
        data = bucket.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "monitor1"}}

        bucket.put(Origin.SOURCE, data)

        mock_client.put_object.assert_called_once_with(
            Body=bytes(json.dumps({"id1": {"name": "monitor1"}}), "UTF-8"),
            Bucket="test-bucket",
            Key="resources/source/monitors.json",
        )

    def test_put_resource_per_file(self, mock_s3_client):
        _, mock_client = mock_s3_client

        mock_client.list_objects_v2.return_value = {"IsTruncated": False}

        bucket = AWSS3Bucket(
            config={
                "aws_bucket_name": "test-bucket",
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "AKID",
                "aws_secret_access_key": "SECRET",
                "aws_session_token": "",
            },
            resource_per_file=True,
        )
        data = bucket.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "mon1"}, "id2": {"name": "mon2"}}

        bucket.put(Origin.SOURCE, data)

        assert mock_client.put_object.call_count == 2
        mock_client.put_object.assert_any_call(
            Body=bytes(json.dumps({"id1": {"name": "mon1"}}), "UTF-8"),
            Bucket="test-bucket",
            Key="resources/source/monitors.id1.json",
        )
        mock_client.put_object.assert_any_call(
            Body=bytes(json.dumps({"id2": {"name": "mon2"}}), "UTF-8"),
            Bucket="test-bucket",
            Key="resources/source/monitors.id2.json",
        )
