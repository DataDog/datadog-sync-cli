# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from azure.core.exceptions import ResourceNotFoundError
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

    def get(self, origin: Origin, resource_types=None) -> StorageData:
        log.info("Azure Blob Storage get called")
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL]:
            data.source = self._list_and_load(self.source_resources_path, resource_types, "source")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            data.destination = self._list_and_load(self.destination_resources_path, resource_types, "destination")

        return data

    def _list_and_load(self, base_prefix: str, resource_types, label: str):
        """List and load Azure blobs, optionally scoped to resource_types."""
        result = defaultdict(dict)
        prefixes = [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
        for prefix in prefixes:
            for blob in self.container_client.list_blobs(name_starts_with=prefix):
                if not blob.name.endswith(".json"):
                    continue
                resource_type = blob.name.split(".")[0].split("/")[-1]
                try:
                    content = self.container_client.download_blob(blob.name).readall().decode("utf-8")
                    result[resource_type].update(json.loads(content))
                except json.decoder.JSONDecodeError:
                    log.warning(f"invalid json in azure {label} resource file: {resource_type}")
        return result

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("Azure Blob Storage put called")
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, resource_data in data.source.items():
                base_key = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    self._check_id_collisions(resource_data, resource_type)
                    for _id, resource in resource_data.items():
                        safe_id = self._sanitize_id_for_filename(_id)
                        key = f"{base_key}.{safe_id}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps({_id: resource}), overwrite=True)
                else:
                    key = f"{base_key}.json"
                    self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, resource_data in data.destination.items():
                base_key = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    self._check_id_collisions(resource_data, resource_type)
                    for _id, resource in resource_data.items():
                        safe_id = self._sanitize_id_for_filename(_id)
                        key = f"{base_key}.{safe_id}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps({_id: resource}), overwrite=True)
                else:
                    key = f"{base_key}.json"
                    self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)

    def _try_get_blob(self, key: str) -> Optional[Dict]:
        """Fetch and parse one Azure blob. Returns None on ResourceNotFoundError."""
        try:
            content = self.container_client.download_blob(key).readall().decode("utf-8")
            return json.loads(content)
        except ResourceNotFoundError:
            return None
        except json.decoder.JSONDecodeError:
            log.warning(f"invalid json in azure resource file: {key}")
            return None

    def get_by_ids(self, origin: Origin, exact_ids: Dict[str, List[str]]) -> StorageData:
        """Load specific resources by ID without listing. Constructs keys directly."""
        if not self.resource_per_file:
            raise ValueError("get_by_ids() requires --resource-per-file. " "Re-run with --resource-per-file enabled.")
        data = StorageData()
        for resource_type, ids in exact_ids.items():
            for resource_id in ids:
                src, dst = self.get_single(resource_type, resource_id)
                if origin in [Origin.SOURCE, Origin.ALL] and src is not None:
                    data.source[resource_type][resource_id] = src
                if origin in [Origin.DESTINATION, Origin.ALL] and dst is not None:
                    data.destination[resource_type][resource_id] = dst
        return data

    def get_single(self, resource_type: str, resource_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load one resource's source and destination state by ID."""
        safe_id = self._sanitize_id_for_filename(resource_id)

        src_key = f"{self.source_resources_path}/{resource_type}.{safe_id}.json"
        src_obj = self._try_get_blob(src_key)
        src_data = src_obj.get(resource_id) if src_obj else None

        dst_key = f"{self.destination_resources_path}/{resource_type}.{safe_id}.json"
        dst_obj = self._try_get_blob(dst_key)
        dst_data = dst_obj.get(resource_id) if dst_obj else None

        return src_data, dst_data
