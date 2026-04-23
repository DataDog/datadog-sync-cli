# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from google.api_core.exceptions import NotFound
from google.cloud import storage as gcs_storage

from datadog_sync.constants import (
    Origin,
    DESTINATION_PATH_DEFAULT,
    LOGGER_NAME,
    SOURCE_PATH_DEFAULT,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData


log = logging.getLogger(LOGGER_NAME)


class GCSBucket(BaseStorage):

    def __init__(
        self,
        source_resources_path=SOURCE_PATH_DEFAULT,
        destination_resources_path=DESTINATION_PATH_DEFAULT,
        resource_per_file=False,
        config=None,
    ) -> None:
        log.info("GCS init called")
        super().__init__()
        self.source_resources_path = source_resources_path
        self.destination_resources_path = destination_resources_path
        self.resource_per_file = resource_per_file
        if not config:
            raise ValueError("No GCS configuration passed in")

        key_file = config.get("gcs_service_account_key_file", None)
        if key_file:
            log.info("GCS configured with service account key file")
            self.client = gcs_storage.Client.from_service_account_json(key_file)
        else:
            log.info("GCS configured with application default credentials")
            self.client = gcs_storage.Client()

        bucket_name = config.get("gcs_bucket_name", "")
        if not bucket_name:
            raise ValueError("GCS bucket name is required")
        self.bucket = self.client.bucket(bucket_name)

    def get(self, origin: Origin, resource_types=None) -> StorageData:
        log.info("GCS get called")
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL]:
            data.source = self._list_and_load(self.source_resources_path, resource_types, "source")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            data.destination = self._list_and_load(self.destination_resources_path, resource_types, "destination")

        return data

    def _list_and_load(self, base_prefix: str, resource_types, label: str):
        """List and load GCS blobs, optionally scoped to resource_types."""
        result = defaultdict(dict)
        prefixes = [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
        for prefix in prefixes:
            for blob in self.bucket.list_blobs(prefix=prefix):
                if not blob.name.endswith(".json"):
                    continue
                resource_type = blob.name.split(".")[0].split("/")[-1]
                try:
                    content = self.bucket.blob(blob.name).download_as_text()
                    result[resource_type].update(json.loads(content))
                except json.decoder.JSONDecodeError:
                    log.warning(f"invalid json in gcs {label} resource file: {resource_type}")
                except NotFound:
                    log.warning(f"gcs {label} resource file not found (may have been deleted): {blob.name}")
        return result

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("GCS put called")
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, resource_data in data.source.items():
                base_key = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    self._check_id_collisions(resource_data, resource_type)
                    for _id, resource in resource_data.items():
                        safe_id = self._sanitize_id_for_filename(_id)
                        key = f"{base_key}.{safe_id}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps({_id: resource}), content_type="application/json"
                        )
                else:
                    key = f"{base_key}.json"
                    self.bucket.blob(key).upload_from_string(json.dumps(resource_data), content_type="application/json")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, resource_data in data.destination.items():
                base_key = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    self._check_id_collisions(resource_data, resource_type)
                    for _id, resource in resource_data.items():
                        safe_id = self._sanitize_id_for_filename(_id)
                        key = f"{base_key}.{safe_id}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps({_id: resource}), content_type="application/json"
                        )
                else:
                    key = f"{base_key}.json"
                    self.bucket.blob(key).upload_from_string(json.dumps(resource_data), content_type="application/json")

    def _try_get_blob(self, key: str) -> Optional[Dict]:
        """Fetch and parse one GCS blob. Returns None on NotFound."""
        try:
            content = self.bucket.blob(key).download_as_text()
            return json.loads(content)
        except NotFound:
            return None
        except json.decoder.JSONDecodeError:
            log.warning(f"invalid json in gcs resource file: {key}")
            return None

    def get_by_ids(self, origin: Origin, exact_ids: Dict[str, List[str]]) -> StorageData:
        """Load specific resources by ID without listing. Constructs keys directly."""
        if not self.resource_per_file:
            raise ValueError(
                "get_by_ids() requires --resource-per-file. "
                "Re-run with --resource-per-file enabled."
            )
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
