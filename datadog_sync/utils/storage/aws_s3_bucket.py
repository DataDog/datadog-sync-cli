# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

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
        """
        result = defaultdict(dict)
        # Scoped: iterate one type at a time using tight prefix "{base}/{type}."
        # Unscoped: single broad listing — existing behavior
        prefixes = [f"{base_prefix}/{rt}." for rt in resource_types] if resource_types is not None else [base_prefix]
        for prefix in prefixes:
            continuation_token = None
            while True:
                list_kwargs = {"Bucket": self.bucket_name, "Prefix": prefix}
                if continuation_token:
                    list_kwargs["ContinuationToken"] = continuation_token

                response = self.client.list_objects_v2(**list_kwargs)

                if "Contents" in response:
                    for item in response["Contents"]:
                        key = item["Key"]
                        if not key.endswith(".json"):
                            continue
                        resource_type = key.split(".")[0].split("/")[-1]
                        obj = self.client.get_object(Bucket=self.bucket_name, Key=key)
                        try:
                            result[resource_type].update(json.load(obj["Body"]))
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in aws {label} resource file: {resource_type}")

                if response.get("IsTruncated"):
                    continuation_token = response.get("NextContinuationToken")
                else:
                    break
        return result

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("AWS S3 put called")
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
                else:
                    key = f"{base_key}.json"
                    binary_data = bytes(json.dumps(resource_data), "UTF-8")
                    self.client.put_object(
                        Body=binary_data,
                        Bucket=self.bucket_name,
                        Key=key,
                    )

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
                else:
                    key = f"{base_key}.json"
                    binary_data = bytes(json.dumps(resource_data), "UTF-8")
                    self.client.put_object(
                        Body=binary_data,
                        Bucket=self.bucket_name,
                        Key=key,
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
