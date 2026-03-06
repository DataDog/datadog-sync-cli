# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
from unittest.mock import MagicMock, patch

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer


@pytest.fixture
def mock_azure_connection_string():
    with patch("datadog_sync.utils.storage.azure_blob_container.ContainerClient") as mock_container_cls:
        mock_container = MagicMock()
        mock_container_cls.from_connection_string.return_value = mock_container
        yield mock_container_cls, mock_container


@pytest.fixture
def mock_azure_account_key():
    with patch("datadog_sync.utils.storage.azure_blob_container.BlobServiceClient") as mock_bsc_cls:
        mock_bsc = MagicMock()
        mock_bsc_cls.return_value = mock_bsc
        mock_container = MagicMock()
        mock_bsc.get_container_client.return_value = mock_container
        yield mock_bsc_cls, mock_bsc, mock_container


class TestAzureBlobContainer:
    def test_init_with_connection_string(self, mock_azure_connection_string):
        mock_container_cls, mock_container = mock_azure_connection_string

        AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "DefaultEndpointsProtocol=https;AccountName=test",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )

        mock_container_cls.from_connection_string.assert_called_once_with(
            conn_str="DefaultEndpointsProtocol=https;AccountName=test",
            container_name="test-container",
        )

    def test_init_with_account_key(self, mock_azure_account_key):
        mock_bsc_cls, mock_bsc, mock_container = mock_azure_account_key

        AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": None,
                "azure_storage_account_name": "myaccount",
                "azure_storage_account_key": "mykey",
            }
        )

        mock_bsc_cls.assert_called_once_with(
            account_url="https://myaccount.blob.core.windows.net",
            credential="mykey",
        )
        mock_bsc.get_container_client.assert_called_once_with("test-container")

    def test_init_with_default_credentials(self):
        with patch("datadog_sync.utils.storage.azure_blob_container.BlobServiceClient") as mock_bsc_cls, patch(
            "datadog_sync.utils.storage.azure_blob_container.DefaultAzureCredential"
        ) as mock_cred_cls:
            mock_bsc = MagicMock()
            mock_bsc_cls.return_value = mock_bsc
            mock_container = MagicMock()
            mock_bsc.get_container_client.return_value = mock_container
            mock_cred = MagicMock()
            mock_cred_cls.return_value = mock_cred

            AzureBlobContainer(
                config={
                    "azure_container_name": "test-container",
                    "azure_storage_connection_string": None,
                    "azure_storage_account_name": "myaccount",
                    "azure_storage_account_key": None,
                }
            )

            mock_bsc_cls.assert_called_once_with(
                account_url="https://myaccount.blob.core.windows.net",
                credential=mock_cred,
            )

    def test_init_no_config(self):
        with pytest.raises(ValueError, match="No Azure configuration passed in"):
            AzureBlobContainer(config=None)

    def test_init_missing_container_name(self, mock_azure_connection_string):
        with pytest.raises(ValueError, match="Azure container name is required"):
            AzureBlobContainer(
                config={
                    "azure_container_name": "",
                    "azure_storage_connection_string": "connstr",
                    "azure_storage_account_name": None,
                    "azure_storage_account_key": None,
                }
            )

    def test_init_missing_account_info(self):
        with pytest.raises(ValueError, match="Azure storage requires"):
            AzureBlobContainer(
                config={
                    "azure_container_name": "test-container",
                    "azure_storage_connection_string": None,
                    "azure_storage_account_name": None,
                    "azure_storage_account_key": None,
                }
            )

    def test_get_source(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string

        blob1 = MagicMock()
        blob1.name = "resources/source/monitors.json"
        mock_container.list_blobs.return_value = [blob1]

        download_mock = MagicMock()
        download_mock.readall.return_value = json.dumps({"id1": {"name": "monitor1"}}).encode("utf-8")
        mock_container.download_blob.return_value = download_mock

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )
        data = container.get(Origin.SOURCE)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert len(data.destination) == 0

    def test_get_destination(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string

        blob1 = MagicMock()
        blob1.name = "resources/destination/dashboards.json"
        mock_container.list_blobs.return_value = [blob1]

        download_mock = MagicMock()
        download_mock.readall.return_value = json.dumps({"id2": {"title": "dash1"}}).encode("utf-8")
        mock_container.download_blob.return_value = download_mock

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )
        data = container.get(Origin.DESTINATION)

        assert len(data.source) == 0
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_all(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string

        source_blob = MagicMock()
        source_blob.name = "resources/source/monitors.json"

        dest_blob = MagicMock()
        dest_blob.name = "resources/destination/dashboards.json"

        mock_container.list_blobs.side_effect = [[source_blob], [dest_blob]]

        source_download = MagicMock()
        source_download.readall.return_value = json.dumps({"id1": {"name": "monitor1"}}).encode("utf-8")
        dest_download = MagicMock()
        dest_download.readall.return_value = json.dumps({"id2": {"title": "dash1"}}).encode("utf-8")
        mock_container.download_blob.side_effect = [source_download, dest_download]

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )
        data = container.get(Origin.ALL)

        assert dict(data.source["monitors"]) == {"id1": {"name": "monitor1"}}
        assert dict(data.destination["dashboards"]) == {"id2": {"title": "dash1"}}

    def test_get_skips_non_json(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string

        blob1 = MagicMock()
        blob1.name = "resources/source/readme.txt"
        mock_container.list_blobs.return_value = [blob1]

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )
        data = container.get(Origin.SOURCE)

        assert len(data.source) == 0

    def test_put_single_file(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string
        mock_container.list_blobs.return_value = []

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            }
        )
        data = container.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "monitor1"}}

        container.put(Origin.SOURCE, data)

        mock_container.upload_blob.assert_called_once_with(
            name="resources/source/monitors.json",
            data=json.dumps({"id1": {"name": "monitor1"}}),
            overwrite=True,
        )

    def test_put_resource_per_file(self, mock_azure_connection_string):
        _, mock_container = mock_azure_connection_string
        mock_container.list_blobs.return_value = []

        container = AzureBlobContainer(
            config={
                "azure_container_name": "test-container",
                "azure_storage_connection_string": "connstr",
                "azure_storage_account_name": None,
                "azure_storage_account_key": None,
            },
            resource_per_file=True,
        )
        data = container.get(Origin.SOURCE)
        data.source["monitors"] = {"id1": {"name": "mon1"}, "id2": {"name": "mon2"}}

        container.put(Origin.SOURCE, data)

        assert mock_container.upload_blob.call_count == 2
        mock_container.upload_blob.assert_any_call(
            name="resources/source/monitors.id1.json",
            data=json.dumps({"id1": {"name": "mon1"}}),
            overwrite=True,
        )
        mock_container.upload_blob.assert_any_call(
            name="resources/source/monitors.id2.json",
            data=json.dumps({"id2": {"name": "mon2"}}),
            overwrite=True,
        )
