# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import os
import time
from typing import Dict, Optional, Set, Tuple

from datadog_sync.constants import (
    Origin,
    DESTINATION_PATH_DEFAULT,
    LOGGER_NAME,
    SOURCE_PATH_DEFAULT,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData


log = logging.getLogger(LOGGER_NAME)


class LocalFile(BaseStorage):
    def __init__(
        self,
        source_resources_path=SOURCE_PATH_DEFAULT,
        destination_resources_path=DESTINATION_PATH_DEFAULT,
        resource_per_file=False,
    ) -> None:
        super().__init__()
        # resource_per_file is a boolean, when False we maintain the behavior of storing all the resources
        # by their resource type, so there is one file for all the monitors and another file for all the
        # dashboards. In that case files are named {resource_type}.json. When the boolean is True each resource
        # will be in its own file. The file name will be {resource_type}.{identifier}.json.
        self.resource_per_file = resource_per_file
        self.source_resources_path = source_resources_path
        self.destination_resources_path = destination_resources_path

    def get(self, origin: Origin, resource_types=None) -> StorageData:
        data = StorageData()
        if origin in [Origin.SOURCE, Origin.ALL]:
            self._load_prefix(self.source_resources_path, data.source, resource_types, "source")
        if origin in [Origin.DESTINATION, Origin.ALL]:
            self._load_prefix(self.destination_resources_path, data.destination, resource_types, "destination")
        return data

    def _load_prefix(self, base_path: str, target: dict, resource_types, label: str) -> None:
        """Helper that loads one prefix and emits a `list_and_load` log line
        matching the cloud-backend schema (label, pages_listed, blobs_listed,
        blobs_downloaded, transient_errors, aborted, list_ms, download_ms,
        wall_ms). The log is emitted from a finally block so it fires even when
        os.listdir / open / json.load raises and the exception propagates out.

        For local files, pages_listed=1 (single os.listdir call), list_ms is
        the os.listdir wall-clock, download_ms sums the per-file open+json.load.
        """
        call_start_ns = time.perf_counter_ns()
        list_ns = 0
        download_ns = 0
        files_listed = 0
        files_loaded = 0
        transient_errors = 0
        aborted = 1
        try:
            if os.path.exists(base_path):
                list_start_ns = time.perf_counter_ns()
                entries = os.listdir(base_path)
                list_ns = time.perf_counter_ns() - list_start_ns
                for file in entries:
                    files_listed += 1
                    if not file.endswith(".json"):
                        continue
                    resource_type = file.split(".")[0]
                    if resource_types is not None and resource_type not in resource_types:
                        continue
                    dl_start_ns = time.perf_counter_ns()
                    with open(f"{base_path}/{file}", "r", encoding="utf-8") as input_file:
                        try:
                            target[resource_type].update(json.load(input_file))
                            files_loaded += 1
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in {label} resource file: {file}")
                            transient_errors += 1
                    download_ns += time.perf_counter_ns() - dl_start_ns
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=list_and_load backend=local_file label=%s pages_listed=1 "
                "blobs_listed=%d blobs_downloaded=%d transient_errors=%d aborted=%d "
                "list_ms=%d download_ms=%d wall_ms=%d",
                label,
                files_listed,
                files_loaded,
                transient_errors,
                aborted,
                list_ns // 1_000_000,
                download_ns // 1_000_000,
                (time.perf_counter_ns() - call_start_ns) // 1_000_000,
            )

    def put(self, origin: Origin, data: StorageData) -> None:
        call_start_ns = time.perf_counter_ns()
        blobs_written_source = 0
        blobs_written_destination = 0
        aborted = 1
        try:
            if origin in [Origin.SOURCE, Origin.ALL]:
                os.makedirs(self.source_resources_path, exist_ok=True)
                blobs_written_source = self.write_resources_file(Origin.SOURCE, data)

            if origin in [Origin.DESTINATION, Origin.ALL]:
                os.makedirs(self.destination_resources_path, exist_ok=True)
                blobs_written_destination = self.write_resources_file(origin, data)
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=put backend=local_file origin=%s "
                "blobs_written_source=%d blobs_written_destination=%d aborted=%d wall_ms=%d",
                origin.value,
                blobs_written_source,
                blobs_written_destination,
                aborted,
                (time.perf_counter_ns() - call_start_ns) // 1_000_000,
            )

    def write_resources_file(self, origin: Origin, data: StorageData) -> int:
        """Write the requested origin's data to disk. Returns the count of
        files written so callers can include it in their timing log.
        """
        written = 0
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, value in data.source.items():
                base_filename = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    skip_ids = self._check_id_collisions(value, resource_type)
                    for _id, resource in value.items():
                        if _id in skip_ids:
                            continue
                        safe_id = self._sanitize_id_for_filename(_id)
                        filename = f"{base_filename}.{safe_id}.json"
                        with open(filename, "w+", encoding="utf-8") as out_file:
                            json.dump({_id: resource}, out_file)
                        written += 1
                else:
                    filename = f"{base_filename}.json"
                    with open(filename, "w+", encoding="utf-8") as out_file:
                        json.dump(value, out_file)
                    written += 1

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, value in data.destination.items():
                base_filename = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    skip_ids = self._check_id_collisions(value, resource_type)
                    for _id, resource in value.items():
                        if _id in skip_ids:
                            continue
                        safe_id = self._sanitize_id_for_filename(_id)
                        filename = f"{base_filename}.{safe_id}.json"
                        with open(filename, "w+", encoding="utf-8") as out_file:
                            json.dump({_id: resource}, out_file)
                        written += 1
                else:
                    filename = f"{base_filename}.json"
                    with open(filename, "w+", encoding="utf-8") as out_file:
                        json.dump(value, out_file)
                    written += 1
        return written

    def _path_for(self, origin: Origin) -> str:
        if origin == Origin.SOURCE:
            return self.source_resources_path
        if origin == Origin.DESTINATION:
            return self.destination_resources_path
        raise ValueError(f"_path_for() requires SOURCE or DESTINATION, got {origin}")

    def list_filenames(self, origin: Origin, resource_type: str) -> Set[str]:
        base = self._path_for(origin)
        if not os.path.exists(base):
            return set()
        return {f for f in os.listdir(base) if self._is_per_resource_filename(resource_type, f)}

    def delete(self, origin: Origin, filename: str) -> None:
        path = f"{self._path_for(origin)}/{filename}"
        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # idempotent

    def get_single(self, resource_type: str, resource_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load one resource's source and destination state by ID.

        Constructs the filename using the sanitized ID, reads the file, and
        returns the content keyed by the original (unsanitized) resource_id.
        Returns (None, None) if the file does not exist.
        """
        safe_id = self._sanitize_id_for_filename(resource_id)

        src_data = None
        src_path = f"{self.source_resources_path}/{resource_type}.{safe_id}.json"
        if os.path.exists(src_path):
            try:
                with open(src_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    src_data = content.get(resource_id)
            except (json.decoder.JSONDecodeError, FileNotFoundError):
                log.warning(f"invalid json or missing source file: {src_path}")

        dst_data = None
        dst_path = f"{self.destination_resources_path}/{resource_type}.{safe_id}.json"
        if os.path.exists(dst_path):
            try:
                with open(dst_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    dst_data = content.get(resource_id)
            except (json.decoder.JSONDecodeError, FileNotFoundError):
                log.warning(f"invalid json or missing destination file: {dst_path}")

        return src_data, dst_data
