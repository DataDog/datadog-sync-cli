# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging

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

        self.bucket = self.client.bucket(config.get("gcs_bucket_name", ""))

    def get(self, origin: Origin) -> StorageData:
        log.info("GCS get called")
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL]:
            for blob in self.bucket.list_blobs(prefix=self.source_resources_path):
                if blob.name.endswith(".json"):
                    resource_type = blob.name.split(".")[0].split("/")[-1]
                    try:
                        content = blob.download_as_text()
                        data.source[resource_type].update(json.loads(content))
                    except json.decoder.JSONDecodeError:
                        log.warning(f"invalid json in gcs source resource file: {resource_type}")

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for blob in self.bucket.list_blobs(prefix=self.destination_resources_path):
                if blob.name.endswith(".json"):
                    resource_type = blob.name.split(".")[0].split("/")[-1]
                    try:
                        content = blob.download_as_text()
                        data.destination[resource_type].update(json.loads(content))
                    except json.decoder.JSONDecodeError:
                        log.warning(f"invalid json in gcs destination resource file: {resource_type}")

        return data

    def put(self, origin: Origin, data: StorageData) -> None:
        log.info("GCS put called")
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, resource_data in data.source.items():
                base_key = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps({_id: resource}), content_type="application/json"
                        )
                else:
                    key = f"{base_key}.json"
                    self.bucket.blob(key).upload_from_string(
                        json.dumps(resource_data), content_type="application/json"
                    )

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, resource_data in data.destination.items():
                base_key = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in resource_data.items():
                        key = f"{base_key}.{_id}.json"
                        self.bucket.blob(key).upload_from_string(
                            json.dumps({_id: resource}), content_type="application/json"
                        )
                else:
                    key = f"{base_key}.json"
                    self.bucket.blob(key).upload_from_string(
                        json.dumps(resource_data), content_type="application/json"
                    )
