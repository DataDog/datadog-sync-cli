# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import os

from datadog_sync.constants import LOGGER_NAME, Origin
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData


log = logging.getLogger(LOGGER_NAME)


class LocalFile(BaseStorage):
    SOURCE_RESOURCES_DIR = "resources/source"
    DESTINATION_RESOURCES_DIR = "resources/destination"

    def get(self, origin: Origin) -> StorageData:
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL] and os.path.exists(self.SOURCE_RESOURCES_DIR):
            for file in os.listdir(self.SOURCE_RESOURCES_DIR):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(self.SOURCE_RESOURCES_DIR + f"/{file}", "r") as f:
                        try:
                            data.source[resource_type] = json.load(f)
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in source resource file: {resource_type}")

        if origin in [Origin.DESTINATION, Origin.ALL] and os.path.exists(self.DESTINATION_RESOURCES_DIR):
            for file in os.listdir(self.DESTINATION_RESOURCES_DIR):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(self.DESTINATION_RESOURCES_DIR + f"/{file}", "r") as f:
                        try:
                            data.destination[resource_type] = json.load(f)
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in destination resource file: {resource_type}")

        return data

    def put(self, origin: Origin, data: StorageData) -> None:
        if origin in [Origin.SOURCE, Origin.ALL]:
            os.makedirs(self.SOURCE_RESOURCES_DIR, exist_ok=True)
            self.write_resources_file(Origin.SOURCE, data)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            os.makedirs(self.DESTINATION_RESOURCES_DIR, exist_ok=True)
            self.write_resources_file(origin, data)

    def write_resources_file(self, origin: Origin, data: StorageData) -> None:
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, v in data.source.items():
                with open(self.SOURCE_RESOURCES_DIR + f"/{resource_type}.json", "w+") as f:
                    json.dump(v, f)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, v in data.destination.items():
                with open(self.DESTINATION_RESOURCES_DIR + f"/{resource_type}.json", "w+") as f:
                    json.dump(v, f)
