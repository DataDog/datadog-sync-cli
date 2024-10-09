# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Any, Dict, List, Tuple

from datadog_sync.constants import (
    Origin,
    DESTINATION_DIR_PARAM,
    DESTINATION_DIR_DEFAULT,
    SOURCE_DIR_PARAM,
    SOURCE_DIR_DEFAULT,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType


class State:
    def __init__(self, type_: StorageType = StorageType.LOCAL_FILE, **kwargs: Any) -> None:
        if type_ == StorageType.LOCAL_FILE:
            source_resources_dir = kwargs.get(SOURCE_DIR_PARAM, SOURCE_DIR_DEFAULT)
            destination_resources_dir = kwargs.get(DESTINATION_DIR_PARAM, DESTINATION_DIR_DEFAULT)
            self._storage: BaseStorage = LocalFile(
                source_resources_dir=source_resources_dir,
                destination_resources_dir=destination_resources_dir,
            )
        else:
            raise NotImplementedError(f"Storage type {type_} not implemented")

        self._data: StorageData = StorageData()
        self.load_state()

    @property
    def source(self):
        return self._data.source

    @property
    def destination(self):
        return self._data.destination

    def load_state(self, origin: Origin = Origin.ALL) -> None:
        self._data = self._storage.get(origin)

    def dump_state(self, origin: Origin = Origin.ALL) -> None:
        self._storage.put(origin, self._data)

    def get_all_resources(self, resources_types: List[str]) -> Dict[Tuple[str, str], Any]:
        """Returns all resources of the given types.

        Args:
            resources_types (List[str]): List of resource types.

        Returns:
            Dict[Tuple[str, str], Any]: Mapping of all resources.
            Key is a tuple of resource_type and resource id.
        """
        all_resources = {}

        for resource_type in resources_types:
            for _id, r in self._data.source[resource_type].items():
                all_resources[(resource_type, _id)] = r

        return all_resources

    def get_resources_to_cleanup(self, resources_types: List[str]) -> Dict[Tuple[str, str], Any]:
        """Returns all resources to cleanup.

        Args:
            resources_types (List[str]): List of resource types.

        Returns:
            Dict[Tuple[str, str], Any]: Mapping of all resources.
            Key is a tuple of resource_type and resource id.
        """
        cleanup_resources = {}

        for resource_type in resources_types:
            source_resources = set(self.source[resource_type].keys())
            destination_resources = set(self.destination[resource_type].keys())

            for _id in destination_resources.difference(source_resources):
                cleanup_resources[(resource_type, _id)] = None

        return cleanup_resources
