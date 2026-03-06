# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging

from azure.storage.blob import BlobServiceClient, ContainerClient
from azure.identity import DefaultAzureCredential

from datadog_sync.constants import (
    Origin,
    DESTINATION_PATH_DEFAULT,
    LOGGER_NAME,
    SOURCE_PATH_DEFAULT,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData


log = logging.getLogger(LOGGER_NAME)


class AzureBlobContainer(BaseStorage):

    def __init__(
        self,
        source_resources_path=SOURCE_PATH_DEFAULT,
        destination_resources_path=DESTINATION_PATH_DEFAULT,
        resource_per_file=False,
        config=None,
    ) -> None:
        log.info("Azure Blob Storage init called")
        super().__init__()
        self.source_resources_path = source_resources_path
        self.destination_resources_path = destination_resources_path
        self.resource_per_file = resource_per_file
        if not config:
            raise ValueError("No Azure configuration passed in")

        container_name = config.get("azure_container_name", "")
        if not container_name:
            raise ValueError("Azure container name is required")
        connection_string = config.get("azure_storage_connection_string", None)
        account_name = config.get("azure_storage_account_name", None)
        account_key = config.get("azure_storage_account_key", None)

        if connection_string:
            log.info("Azure Blob Storage configured with connection string")
            self.container_client = ContainerClient.from_connection_string(
                conn_str=connection_string,
                container_name=container_name,
            )
        elif account_name and account_key:
            log.info("Azure Blob Storage configured with account name and key")
            account_url = f"https://{account_name}.blob.core.windows.net"
            blob_service_client = BlobServiceClient(account_url=account_url, credential=account_key)
            self.container_client = blob_service_client.get_container_client(container_name)
        elif account_name:
            log.info("Azure Blob Storage configured with default credentials")
            account_url = f"https://{account_name}.blob.core.windows.net"
            blob_service_client = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
            self.container_client = blob_service_client.get_container_client(container_name)
        else:
            raise ValueError("Azure storage requires at least a connection string or storage account name")

    def get(self, origin: Origin) -> StorageData:
        log.info("Azure Blob Storage get called")
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL]:
            for blob in self.container_client.list_blobs(name_starts_with=self.source_resources_path):
                if blob.name.endswith(".json"):
                    resource_type = blob.name.split(".")[0].split("/")[-1]
                    try:
                        content = self.container_client.download_blob(blob.name).readall().decode("utf-8")
                        data.source[resource_type].update(json.loads(content))
                    except json.decoder.JSONDecodeError:
                        log.warning(f"invalid json in azure source resource file: {resource_type}")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for blob in self.container_client.list_blobs(name_starts_with=self.destination_resources_path):
                if blob.name.endswith(".json"):
                    resource_type = blob.name.split(".")[0].split("/")[-1]
                    try:
                        content = self.container_client.download_blob(blob.name).readall().decode("utf-8")
                        data.destination[resource_type].update(json.loads(content))
                    except json.decoder.JSONDecodeError:
                        log.warning(f"invalid json in azure destination resource file: {resource_type}")

        return data

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("Azure Blob Storage put called")
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, resource_data in data.source.items():
                base_key = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps({_id: resource}), overwrite=True)
                else:
                    key = f"{base_key}.json"
                    self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, resource_data in data.destination.items():
                base_key = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps({_id: resource}), overwrite=True)
                else:
                    key = f"{base_key}.json"
                    self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)
