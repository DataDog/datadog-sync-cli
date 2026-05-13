# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

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
        """List and load Azure blobs, optionally scoped to resource_types.

        Emits a `sync-cli-timing phase=list_and_load` log line per call (from
        a finally block, so the line fires even when list_blobs or
        download_blob raises). aborted=1 indicates the call exited via an
        uncaught exception. SDK-internal retries below this layer are not
        visible; only outer-loop exceptions caught here are counted in
        transient_errors.

        Uses ItemPaged.by_page() introspection when the real Azure SDK iterator
        is in use; falls back to single-page counting against mocks that return
        a flat list.
        """
        call_start_ns = time.perf_counter_ns()
        result = defaultdict(dict)
        list_ns = 0
        download_ns = 0
        pages_listed = 0
        blobs_listed = 0
        blobs_downloaded = 0
        transient_errors = 0
        aborted = 1
        try:
            prefixes = (
                [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
            )
            for prefix in prefixes:
                iterator = self.container_client.list_blobs(name_starts_with=prefix)
                by_page = getattr(iterator, "by_page", None)
                if by_page is not None:
                    list_resume_ns = time.perf_counter_ns()
                    for page in by_page():
                        pages_listed += 1
                        list_ns += time.perf_counter_ns() - list_resume_ns
                        for blob in page:
                            blobs_listed += 1
                            if not blob.name.endswith(".json"):
                                continue
                            resource_type = blob.name.split(".")[0].split("/")[-1]
                            dl_start_ns = time.perf_counter_ns()
                            try:
                                content = self.container_client.download_blob(blob.name).readall().decode("utf-8")
                                result[resource_type].update(json.loads(content))
                                blobs_downloaded += 1
                            except json.decoder.JSONDecodeError:
                                log.warning(f"invalid json in azure {label} resource file: {resource_type}")
                                transient_errors += 1
                            except ResourceNotFoundError:
                                # Race-delete between list_blobs and download_blob.
                                # Matches GCS NotFound / AWS NoSuchKey handling.
                                log.warning(
                                    f"azure {label} resource file not found (may have been deleted): {blob.name}"
                                )
                                transient_errors += 1
                            download_ns += time.perf_counter_ns() - dl_start_ns
                        list_resume_ns = time.perf_counter_ns()
                else:
                    # Test-mock fallback: flat-list iterator with no by_page accessor.
                    pages_listed += 1
                    list_resume_ns = time.perf_counter_ns()
                    for blob in iterator:
                        list_ns += time.perf_counter_ns() - list_resume_ns
                        blobs_listed += 1
                        if not blob.name.endswith(".json"):
                            list_resume_ns = time.perf_counter_ns()
                            continue
                        resource_type = blob.name.split(".")[0].split("/")[-1]
                        dl_start_ns = time.perf_counter_ns()
                        try:
                            content = self.container_client.download_blob(blob.name).readall().decode("utf-8")
                            result[resource_type].update(json.loads(content))
                            blobs_downloaded += 1
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in azure {label} resource file: {resource_type}")
                            transient_errors += 1
                        except ResourceNotFoundError:
                            # Race-delete between list_blobs and download_blob.
                            # Matches GCS NotFound / AWS NoSuchKey handling.
                            log.warning(f"azure {label} resource file not found (may have been deleted): {blob.name}")
                            transient_errors += 1
                        download_ns += time.perf_counter_ns() - dl_start_ns
                        list_resume_ns = time.perf_counter_ns()
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=list_and_load backend=azure_blob label=%s pages_listed=%d "
                "blobs_listed=%d blobs_downloaded=%d transient_errors=%d aborted=%d "
                "list_ms=%d download_ms=%d wall_ms=%d",
                label,
                pages_listed,
                blobs_listed,
                blobs_downloaded,
                transient_errors,
                aborted,
                list_ns // 1_000_000,
                download_ns // 1_000_000,
                (time.perf_counter_ns() - call_start_ns) // 1_000_000,
            )
        return result

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("Azure Blob Storage put called")
        call_start_ns = time.perf_counter_ns()
        blobs_written_source = 0
        blobs_written_destination = 0
        aborted = 1
        try:
            if origin in [Origin.SOURCE, Origin.ALL]:
                for resource_type, resource_data in data.source.items():
                    base_key = f"{self.source_resources_path}/{resource_type}"
                    if self.resource_per_file:
                        skip_ids = self._check_id_collisions(resource_data, resource_type)
                        for _id, resource in resource_data.items():
                            if _id in skip_ids:
                                continue
                            safe_id = self._sanitize_id_for_filename(_id)
                            key = f"{base_key}.{safe_id}.json"
                            self.container_client.upload_blob(
                                name=key, data=json.dumps({_id: resource}), overwrite=True
                            )
                            blobs_written_source += 1
                    else:
                        key = f"{base_key}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)
                        blobs_written_source += 1

            if origin in [Origin.DESTINATION, Origin.ALL]:
                for resource_type, resource_data in data.destination.items():
                    base_key = f"{self.destination_resources_path}/{resource_type}"
                    if self.resource_per_file:
                        skip_ids = self._check_id_collisions(resource_data, resource_type)
                        for _id, resource in resource_data.items():
                            if _id in skip_ids:
                                continue
                            safe_id = self._sanitize_id_for_filename(_id)
                            key = f"{base_key}.{safe_id}.json"
                            self.container_client.upload_blob(
                                name=key, data=json.dumps({_id: resource}), overwrite=True
                            )
                            blobs_written_destination += 1
                    else:
                        key = f"{base_key}.json"
                        self.container_client.upload_blob(name=key, data=json.dumps(resource_data), overwrite=True)
                        blobs_written_destination += 1
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=put backend=azure_blob origin=%s "
                "blobs_written_source=%d blobs_written_destination=%d aborted=%d wall_ms=%d",
                origin.value,
                blobs_written_source,
                blobs_written_destination,
                aborted,
                (time.perf_counter_ns() - call_start_ns) // 1_000_000,
            )

    def _path_for(self, origin: Origin) -> str:
        if origin == Origin.SOURCE:
            return self.source_resources_path
        if origin == Origin.DESTINATION:
            return self.destination_resources_path
        raise ValueError(f"_path_for() requires SOURCE or DESTINATION, got {origin}")

    def list_filenames(self, origin: Origin, resource_type: str) -> Set[str]:
        prefix = f"{self._path_for(origin)}/{resource_type}."
        result: Set[str] = set()
        for blob in self.container_client.list_blobs(name_starts_with=prefix):
            filename = blob.name.split("/")[-1]
            if not self._is_per_resource_filename(resource_type, filename):
                continue
            result.add(filename)
        return result

    def delete(self, origin: Origin, filename: str) -> None:
        key = f"{self._path_for(origin)}/{filename}"
        try:
            self.container_client.delete_blob(key)
        except ResourceNotFoundError:
            pass  # idempotent

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
