# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging

import boto3

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

    def get(self, origin: Origin) -> StorageData:
        log.info("AWS S3 get called")
        data = StorageData()

        # Load source resources with pagination
        if origin in [Origin.SOURCE, Origin.ALL]:
            continuation_token = None
            while True:
                # Build list_objects_v2 kwargs
                list_kwargs = {
                    "Bucket": self.bucket_name,
                    "Prefix": self.source_resources_path,
                }
                if continuation_token:
                    list_kwargs["ContinuationToken"] = continuation_token

                prefix_contents = self.client.list_objects_v2(**list_kwargs)

                # Process contents if they exist
                if "Contents" in prefix_contents:
                    for item in prefix_contents["Contents"]:
                        key = item["Key"]
                        if key.endswith(".json"):
                            resource_type = key.split(".")[0].split("/")[-1]
                            response = self.client.get_object(
                                Bucket=self.bucket_name,
                                Key=key,
                            )
                            content_body = response.get("Body")
                            try:
                                data.source[resource_type].update(json.load(content_body))
                            except json.decoder.JSONDecodeError:
                                log.warning(f"invalid json in aws source resource file: {resource_type}")

                # Check if there are more pages to fetch
                if prefix_contents.get("IsTruncated"):
                    continuation_token = prefix_contents.get("NextContinuationToken")
                else:
                    break

        # Load destination resources with pagination
        if origin in [Origin.DESTINATION, Origin.ALL]:
            continuation_token = None
            while True:
                # Build list_objects_v2 kwargs
                list_kwargs = {
                    "Bucket": self.bucket_name,
                    "Prefix": self.destination_resources_path,
                }
                if continuation_token:
                    list_kwargs["ContinuationToken"] = continuation_token

                prefix_contents = self.client.list_objects_v2(**list_kwargs)

                # Process contents if they exist
                if "Contents" in prefix_contents:
                    for item in prefix_contents["Contents"]:
                        key = item["Key"]
                        if key.endswith(".json"):
                            resource_type = key.split(".")[0].split("/")[-1]
                            response = self.client.get_object(
                                Bucket=self.bucket_name,
                                Key=key,
                            )
                            content_body = response.get("Body")
                            try:
                                data.destination[resource_type].update(json.load(content_body))
                            except json.decoder.JSONDecodeError:
                                log.warning(f"invalid json in aws destination resource file: {resource_type}")

                # Check if there are more pages to fetch
                if prefix_contents.get("IsTruncated"):
                    continuation_token = prefix_contents.get("NextContinuationToken")
                else:
                    break

        return data

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("AWS S3 put called")
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, resource_data in data.source.items():
                base_key = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
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
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
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
