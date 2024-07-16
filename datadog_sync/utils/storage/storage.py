# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import json
from logging import log
from typing import Any, Dict, Tuple

from datadog_sync.constants import RESOURCE_FILE_PATH
from datadog_sync.utils.base_resource import BaseResource


class StorageDataItem:
    def __init__(self, resource_type: str) -> None:
        self.source, self.destination = self._load_resource(resource_type)

    @staticmethod
    def _load_resource(resource_type: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        source_resources = {}
        destination_resources = {}
        source_path = RESOURCE_FILE_PATH.format("source", resource_type)
        destination_path = RESOURCE_FILE_PATH.format("destination", resource_type)

        if os.path.exists(source_path):
            with open(source_path, "r") as f:
                try:
                    source_resources = json.load(f)
                except json.decoder.JSONDecodeError:
                    log.warning(f"invalid json in source resource file: {resource_type}")

        if os.path.exists(destination_path):
            with open(destination_path, "r") as f:
                try:
                    destination_resources = json.load(f)
                except json.decoder.JSONDecodeError:
                    log.warning(f"invalid json in destination resource file: {resource_type}")

        return source_resources, destination_resources


class Storage:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}

    def load(self, resources: Dict[str, BaseResource]) -> None:
        for resource_type in resources.keys():
            self.data[resource_type] = StorageDataItem(resource_type)
