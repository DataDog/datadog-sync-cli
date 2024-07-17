# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
from logging import log
import os

from datadog_sync.constants import DESTINATION_RESOURCES_DIR, SOURCE_RESOURCES_DIR
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageItem


class LocalFile(BaseStorage):
    def get(self) -> StorageItem:
        data = StorageItem()

        if os.path.exists(SOURCE_RESOURCES_DIR):
            for file in os.listdir(SOURCE_RESOURCES_DIR):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(SOURCE_RESOURCES_DIR + f"/{file}", "r") as f:
                        try:
                            data.source[resource_type] = json.load(f)
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in source resource file: {resource_type}")

        if os.path.exists(DESTINATION_RESOURCES_DIR):
            for file in os.listdir(DESTINATION_RESOURCES_DIR):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(DESTINATION_RESOURCES_DIR + f"/{file}", "r") as f:
                        try:
                            data.destination[resource_type] = json.load(f)
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in destination resource file: {resource_type}")

        return data

    def put(self) -> None:
        pass
