# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Dict

from datadog_sync.utils.storage._base_storage import BaseStorage, StorageItem
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType


class State:
    def __init__(self, type_: StorageType = StorageType.LOCAL_FILE) -> None:
        if type_ == StorageType.LOCAL_FILE:
            self._storage: BaseStorage = LocalFile()

        self._data: Dict[str, StorageItem] = StorageItem()

    @property
    def source(self):
        return self._data.source

    @property
    def destination(self):
        return self._data.destination

    def load_state(self) -> None:
        self._data = self._storage.get()

    def dump_state(self) -> None:
        self._storage.put(self.data)
