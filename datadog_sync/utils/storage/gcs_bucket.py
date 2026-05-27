# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

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
        """List and load GCS blobs, optionally scoped to resource_types.

        Emits a `sync-cli-timing phase=list_and_load` log line per call (from
        a finally block, so the line fires even when an SDK call raises and
        the exception propagates out). Fields:
        - list_ms vs download_ms: split between time spent in the listing
          iterator and time spent in per-blob downloads. The primary signal
          for diagnosing reader/writer races on a bucket prefix being written
          concurrently — high list_ms with low download_ms suggests pagination
          cost (potentially elevated under concurrent writes), while the
          inverse points to per-blob fetch cost.
        - pages_listed, blobs_listed, blobs_downloaded: shape counters.
        - transient_errors: per-blob exceptions caught here
          (json.JSONDecodeError, NotFound). SDK-internal retries below this
          layer are not visible.
        - aborted: 1 if the call exited via an uncaught exception (e.g. a 5xx
          from list_blobs / download_as_text that escaped this layer's narrow
          except clauses), 0 on normal return. Lets operators distinguish
          a successful empty call from a failed call that yielded zero blobs.

        Uses iterator.pages introspection when the real google-cloud-storage
        HTTPIterator is in use; falls back to single-page counting against
        mocks that return a flat list.
        """
        call_start_ns = time.perf_counter_ns()
        result = defaultdict(dict)
        prefixes = [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
        list_ns = 0
        download_ns = 0
        pages_listed = 0
        blobs_listed = 0
        blobs_downloaded = 0
        transient_errors = 0
        aborted = 1
        try:
            for prefix in prefixes:
                iterator = self.bucket.list_blobs(prefix=prefix)
                pages_iter = getattr(iterator, "pages", None)
                if pages_iter is not None:
                    # Real HTTPIterator: iterate page-by-page for accurate page count.
                    list_resume_ns = time.perf_counter_ns()
                    for page in pages_iter:
                        pages_listed += 1
                        list_ns += time.perf_counter_ns() - list_resume_ns
                        for blob in page:
                            blobs_listed += 1
                            if not blob.name.endswith(".json"):
                                continue
                            resource_type = blob.name.split(".")[0].split("/")[-1]
                            dl_start_ns = time.perf_counter_ns()
                            try:
                                content = self.bucket.blob(blob.name).download_as_text()
                                result[resource_type].update(json.loads(content))
                                blobs_downloaded += 1
                            except json.decoder.JSONDecodeError:
                                log.warning(f"invalid json in gcs {label} resource file: {resource_type}")
                                transient_errors += 1
                            except NotFound:
                                log.warning(f"gcs {label} resource file not found (may have been deleted): {blob.name}")
                                transient_errors += 1
                            download_ns += time.perf_counter_ns() - dl_start_ns
                        list_resume_ns = time.perf_counter_ns()
                else:
                    # Test-mock fallback: flat-list iterator with no pages accessor.
                    # Treat the whole iterator as one page; per-blob download timing
                    # is still accurate.
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
                            content = self.bucket.blob(blob.name).download_as_text()
                            result[resource_type].update(json.loads(content))
                            blobs_downloaded += 1
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in gcs {label} resource file: {resource_type}")
                            transient_errors += 1
                        except NotFound:
                            log.warning(f"gcs {label} resource file not found (may have been deleted): {blob.name}")
                            transient_errors += 1
                        download_ns += time.perf_counter_ns() - dl_start_ns
                        list_resume_ns = time.perf_counter_ns()
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=list_and_load backend=gcs label=%s pages_listed=%d "
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
        log.info("GCS put called")
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
                            self.bucket.blob(key).upload_from_string(
                                json.dumps({_id: resource}), content_type="application/json"
                            )
                            blobs_written_source += 1
                    else:
                        key = f"{base_key}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps(resource_data), content_type="application/json"
                        )
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
                            self.bucket.blob(key).upload_from_string(
                                json.dumps({_id: resource}), content_type="application/json"
                            )
                            blobs_written_destination += 1
                    else:
                        key = f"{base_key}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps(resource_data), content_type="application/json"
                        )
                        blobs_written_destination += 1
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=put backend=gcs origin=%s "
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
        for blob in self.bucket.list_blobs(prefix=prefix):
            filename = blob.name.split("/")[-1]
            if not self._is_per_resource_filename(resource_type, filename):
                continue
            result.add(filename)
        return result

    def delete(self, origin: Origin, filename: str) -> None:
        key = f"{self._path_for(origin)}/{filename}"
        try:
            self.bucket.blob(key).delete()
        except NotFound:
            pass  # idempotent

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
