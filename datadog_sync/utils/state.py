# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import logging
from typing import Any, Dict, List, Tuple

from datadog_sync.constants import (
    Origin,
    DESTINATION_PATH_DEFAULT,
    DESTINATION_PATH_PARAM,
    LOGGER_NAME,
    RESOURCE_PER_FILE,
    SOURCE_PATH_DEFAULT,
    SOURCE_PATH_PARAM,
)
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData
from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket
from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer
from datadog_sync.utils.storage.gcs_bucket import GCSBucket
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType

log = logging.getLogger(LOGGER_NAME)


class State:
    def __init__(self, type_: StorageType = StorageType.LOCAL_FILE, **kwargs: object) -> None:
        self._resource_types = kwargs.get("resource_types", None)  # type-scoped loading
        self._exact_ids = kwargs.get("exact_ids", None)  # ID-targeted loading
        self._minimize_reads = self._resource_types is not None or self._exact_ids is not None
        self._ensure_attempted: set = set()  # tracks IDs attempted by ensure_resource_loaded
        resource_per_file = kwargs.get(RESOURCE_PER_FILE, False)
        source_resources_path = kwargs.get(SOURCE_PATH_PARAM, SOURCE_PATH_DEFAULT)
        destination_resources_path = kwargs.get(DESTINATION_PATH_PARAM, DESTINATION_PATH_DEFAULT)
        if type_ == StorageType.LOCAL_FILE:
            self._storage: BaseStorage = LocalFile(
                source_resources_path=source_resources_path,
                destination_resources_path=destination_resources_path,
                resource_per_file=resource_per_file,
            )
        elif type_ == StorageType.AWS_S3_BUCKET:
            config = kwargs.get("config", {})
            if not config:
                raise ValueError("AWS configuration not found")
            self._storage: BaseStorage = AWSS3Bucket(
                source_resources_path=source_resources_path,
                destination_resources_path=destination_resources_path,
                config=config,
                resource_per_file=resource_per_file,
            )
        elif type_ == StorageType.GCS_BUCKET:
            config = kwargs.get("config", {})
            if not config:
                raise ValueError("GCS configuration not found")
            self._storage: BaseStorage = GCSBucket(
                source_resources_path=source_resources_path,
                destination_resources_path=destination_resources_path,
                config=config,
                resource_per_file=resource_per_file,
            )
        elif type_ == StorageType.AZURE_BLOB_CONTAINER:
            config = kwargs.get("config", {})
            if not config:
                raise ValueError("Azure configuration not found")
            self._storage: BaseStorage = AzureBlobContainer(
                source_resources_path=source_resources_path,
                destination_resources_path=destination_resources_path,
                config=config,
                resource_per_file=resource_per_file,
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
        if self._exact_ids is not None:
            # ID-targeted: fetch only specified resources by constructing keys directly
            self._data = self._storage.get_by_ids(origin, self._exact_ids)
        else:
            # Type-scoped (resource_types set) or full load (resource_types=None)
            self._data = self._storage.get(origin, resource_types=self._resource_types)

    def ensure_resource_loaded(self, resource_type: str, resource_id: str) -> None:
        """Lazily load source+destination state for one dependency resource.

        Called from _resource_connections() in resources_handler.py when a
        cross-type dependency is encountered that may not be in the initial
        (scoped) load. Loads both source and destination state so that
        connect_id() in _apply_resource_cb() can remap IDs correctly.

        Note: requires resource_per_file=True in the storage backend.
        get_single constructs per-resource filenames; monolithic layout
        will silently return (None, None) for every dependency.

        Contract:
        - Idempotent: no-op if (resource_type, resource_id) already attempted
        - No-op when not in minimize-reads mode (_minimize_reads=False)
        - Appends to state: never replaces existing entries
        - Missing file: (None, None) → resource stays absent (correct behavior)
        - asyncio-safe: fully synchronous, no await points
        """
        if not self._minimize_reads:
            return
        key = (resource_type, resource_id)
        if key in self._ensure_attempted:
            return
        self._ensure_attempted.add(key)
        log.debug(f"minimize-reads: lazy-loading dep {resource_type}.{resource_id}")
        src, dst = self._storage.get_single(resource_type, resource_id)
        if src is not None:
            self._data.source[resource_type][resource_id] = src
        if dst is not None:
            self._data.destination[resource_type][resource_id] = dst

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
