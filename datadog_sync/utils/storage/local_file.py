# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
import logging
import os

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

    def get(self, origin: Origin) -> StorageData:
        data = StorageData()

        if origin in [Origin.SOURCE, Origin.ALL] and os.path.exists(self.source_resources_path):
            for file in os.listdir(self.source_resources_path):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(self.source_resources_path + f"/{file}", "r", encoding="utf-8") as input_file:
                        try:
                            data.source[resource_type].update(json.load(input_file))
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in source resource file: {file}")

        if origin in [Origin.DESTINATION, Origin.ALL] and os.path.exists(self.destination_resources_path):
            for file in os.listdir(self.destination_resources_path):
                if file.endswith(".json"):
                    resource_type = file.split(".")[0]
                    with open(self.destination_resources_path + f"/{file}", "r", encoding="utf-8") as input_file:
                        try:
                            data.destination[resource_type].update(json.load(input_file))
                        except json.decoder.JSONDecodeError:
                            log.warning(f"invalid json in destination resource file: {file}")

        return data

    def put(self, origin: Origin, data: StorageData) -> None:
        if origin in [Origin.SOURCE, Origin.ALL]:
            os.makedirs(self.source_resources_path, exist_ok=True)
            self.write_resources_file(Origin.SOURCE, data)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            os.makedirs(self.destination_resources_path, exist_ok=True)
            self.write_resources_file(origin, data)

    def write_resources_file(self, origin: Origin, data: StorageData) -> None:
        if origin in [Origin.SOURCE, Origin.ALL]:
            for resource_type, value in data.source.items():
                base_filename = f"{self.source_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in value.items():
                        filename = f"{base_filename}.{_id.replace(':','.')}.json"  # windows can't handle ":"
                        with open(filename, "w+", encoding="utf-8") as out_file:
                            json.dump({_id: resource}, out_file)
                else:
                    filename = f"{base_filename}.json"
                    with open(filename, "w+", encoding="utf-8") as out_file:
                        json.dump(value, out_file)

        if origin in [Origin.DESTINATION, Origin.ALL]:
            for resource_type, value in data.destination.items():
                base_filename = f"{self.destination_resources_path}/{resource_type}"
                if self.resource_per_file:
                    for _id, resource in value.items():
                        filename = f"{base_filename}.{_id.replace(':','.')}.json"  # windows can't handle ":"
                        with open(filename, "w+", encoding="utf-8") as out_file:
                            json.dump({_id: resource}, out_file)
                else:
                    filename = f"{base_filename}.json"
                    with open(filename, "w+", encoding="utf-8") as out_file:
                        json.dump(value, out_file)
