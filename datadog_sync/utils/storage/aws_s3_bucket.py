# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError

from datadog_sync.constants import (
    Origin,
    DESTINATION_PATH_DEFAULT,
    LOGGER_NAME,
    SOURCE_PATH_DEFAULT,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData


log = logging.getLogger(LOGGER_NAME)


class AWSS3Bucket(BaseStorage):
    def __init__(
        self,
        source_resources_path=SOURCE_PATH_DEFAULT,
        destination_resources_path=DESTINATION_PATH_DEFAULT,
        resource_per_file=False,
        config=None,
    ) -> None:
        log.info("AWS S3 init called")
        super().__init__()
        self.source_resources_path = source_resources_path
        self.destination_resources_path = destination_resources_path
        # resource_per_file is a boolean, when False we maintain the behavior of storing all the resources
        # by their resource type, so there is one file for all the monitors and another file for all the
        # dashboards. In that case files are named {resource_type}.json. When the boolean is True each resource
        # will be in its own file. The file name will be {resource_type}.{identifier}.json.
        self.resource_per_file = resource_per_file
        if not config:
            raise ValueError("No S3 configuration passed in")
        elif config.get("aws_region_name", None):
            log.info("AWS S3 configured with command line parameters or env vars")
            self.client = boto3.client(
                "s3",
                region_name=config.get("aws_region_name", ""),
                aws_access_key_id=config.get("aws_access_key_id", ""),
                aws_secret_access_key=config.get("aws_secret_access_key", ""),
                aws_session_token=config.get("aws_session_token", ""),
            )
        elif config.get("aws_bucket_name", None):
            log.info("AWS S3 configured without command line parameters")
            self.client = boto3.client("s3")

        self.bucket_name = config.get("aws_bucket_name", "")
        if not self.bucket_name:
            raise ValueError("AWS S3 bucket name is required")

    def get(self, origin: Origin, resource_types=None) -> StorageData:
        log.info("AWS S3 get called")
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL]:
            data.source = self._list_and_load(self.source_resources_path, resource_types, "source")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            data.destination = self._list_and_load(self.destination_resources_path, resource_types, "destination")

        return data

    def _list_and_load(self, base_prefix: str, resource_types, label: str) -> defaultdict:
        """List and load all matching objects under base_prefix, optionally scoped to resource_types.

        When resource_types is None: single broad listing (existing behavior).
        When resource_types is set: one listing per type using type-specific prefix,
        reducing both list and get_object calls from O(all_resources) to O(requested_resources).

        Emits a `sync-cli-timing phase=list_and_load` log line per call (from
        a finally block, so the line fires even when a list_objects_v2 or
        get_object call raises). aborted=1 indicates the call exited via an
        uncaught exception; aborted=0 indicates normal return. SDK-internal
        retries below this layer are not visible; only outer-loop exceptions
        caught here are counted in transient_errors.
        """
        call_start_ns = time.perf_counter_ns()
        result = defaultdict(dict)
        list_ns = 0
        download_ns = 0
        pages_listed = 0
        objects_listed = 0
        objects_downloaded = 0
        transient_errors = 0
        aborted = 1
        try:
            # Scoped: iterate one type at a time using tight prefix "{base}/{type}."
            # Unscoped: single broad listing — existing behavior
            prefixes = (
                [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
            )
            for prefix in prefixes:
                continuation_token = None
                while True:
                    list_kwargs = {"Bucket": self.bucket_name, "Prefix": prefix}
                    if continuation_token:
                        list_kwargs["ContinuationToken"] = continuation_token

                    list_start_ns = time.perf_counter_ns()
                    response = self.client.list_objects_v2(**list_kwargs)
                    list_ns += time.perf_counter_ns() - list_start_ns
                    pages_listed += 1

                    if "Contents" in response:
                        for item in response["Contents"]:
                            key = item["Key"]
                            objects_listed += 1
                            if not key.endswith(".json"):
                                continue
                            resource_type = key.split(".")[0].split("/")[-1]
                            dl_start_ns = time.perf_counter_ns()
                            try:
                                obj = self.client.get_object(Bucket=self.bucket_name, Key=key)
                                result[resource_type].update(json.load(obj["Body"]))
                                objects_downloaded += 1
                            except json.decoder.JSONDecodeError:
                                log.warning(f"invalid json in aws {label} resource file: {resource_type}")
                                transient_errors += 1
                            except ClientError as e:
                                # NoSuchKey: race-delete between list and get. Count
                                # alongside the GCS/Azure equivalent, warn, continue.
                                # Other ClientErrors (auth, throttling) escape to the
                                # outer try/finally and are logged as aborted=1.
                                if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                                    log.warning(f"aws {label} resource file not found (may have been deleted): {key}")
                                    transient_errors += 1
                                else:
                                    raise
                            download_ns += time.perf_counter_ns() - dl_start_ns

                    if response.get("IsTruncated"):
                        continuation_token = response.get("NextContinuationToken")
                    else:
                        break
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=list_and_load backend=aws_s3 label=%s pages_listed=%d "
                "blobs_listed=%d blobs_downloaded=%d transient_errors=%d aborted=%d "
                "list_ms=%d download_ms=%d wall_ms=%d",
                label,
                pages_listed,
                objects_listed,
                objects_downloaded,
                transient_errors,
                aborted,
                list_ns // 1_000_000,
                download_ns // 1_000_000,
                (time.perf_counter_ns() - call_start_ns) // 1_000_000,
            )
        return result

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("AWS S3 put called")
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
                            binary_data = bytes(json.dumps({_id: resource}), "UTF-8")
                            self.client.put_object(
                                Body=binary_data,
                                Bucket=self.bucket_name,
                                Key=key,
                            )
                            blobs_written_source += 1
                    else:
                        key = f"{base_key}.json"
                        binary_data = bytes(json.dumps(resource_data), "UTF-8")
                        self.client.put_object(
                            Body=binary_data,
                            Bucket=self.bucket_name,
                            Key=key,
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
                            binary_data = bytes(json.dumps({_id: resource}), "UTF-8")
                            self.client.put_object(
                                Body=binary_data,
                                Bucket=self.bucket_name,
                                Key=key,
                            )
                            blobs_written_destination += 1
                    else:
                        key = f"{base_key}.json"
                        binary_data = bytes(json.dumps(resource_data), "UTF-8")
                        self.client.put_object(
                            Body=binary_data,
                            Bucket=self.bucket_name,
                            Key=key,
                        )
                        blobs_written_destination += 1
            aborted = 0
        finally:
            log.info(
                "sync-cli-timing phase=put backend=aws_s3 origin=%s "
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
        base = self._path_for(origin)
        prefix = f"{base}/{resource_type}."
        result: Set[str] = set()
        continuation_token = None
        while True:
            kwargs = {"Bucket": self.bucket_name, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []):
                key = item["Key"]
                filename = key.split("/")[-1]
                if not self._is_per_resource_filename(resource_type, filename):
                    continue
                result.add(filename)
            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
            else:
                break
        return result

    def delete(self, origin: Origin, filename: str) -> None:
        # S3 delete_object is idempotent — succeeds even if the key is absent.
        self.client.delete_object(
            Bucket=self.bucket_name,
            Key=f"{self._path_for(origin)}/{filename}",
        )

    def _try_get_object(self, key: str) -> Optional[Dict]:
        """Fetch and parse one S3 object. Returns None on NotFound."""
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return json.load(response["Body"])
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise
        except json.decoder.JSONDecodeError:
            log.warning(f"invalid json in aws resource file: {key}")
            return None

    def get_single(self, resource_type: str, resource_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load one resource's source and destination state by ID.

        Uses sanitized ID for key construction; content key is the original resource_id.
        Returns (None, None) if files don't exist (NoSuchKey is handled gracefully).
        """
        safe_id = self._sanitize_id_for_filename(resource_id)

        src_key = f"{self.source_resources_path}/{resource_type}.{safe_id}.json"
        src_obj = self._try_get_object(src_key)
        src_data = src_obj.get(resource_id) if src_obj else None

        dst_key = f"{self.destination_resources_path}/{resource_type}.{safe_id}.json"
        dst_obj = self._try_get_object(dst_key)
        dst_data = dst_obj.get(resource_id) if dst_obj else None

        return src_data, dst_data
