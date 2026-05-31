# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from datadog_sync.constants import (
    DESTINATION_PATH_DEFAULT,
    DESTINATION_PATH_PARAM,
    LOGGER_NAME,
    Origin,
    RESOURCE_PER_FILE,
    SOURCE_PATH_DEFAULT,
    SOURCE_PATH_PARAM,
)


log = logging.getLogger(LOGGER_NAME)


def build_storage_backend(type_, **kwargs) -> "BaseStorage":
    """Construct the concrete BaseStorage subclass for a given StorageType.

    Shared by State and ImportState so the per-backend init logic does not
    drift across the two state classes. Imports the backend modules lazily
    so this module stays importable in environments that lack one cloud SDK.
    """
    # Lazy imports avoid a hard dependency on every cloud SDK at module-load
    # time; users who only need LocalFile shouldn't need boto3 / google-cloud
    # / azure-storage-blob installed.
    from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket
    from datadog_sync.utils.storage.azure_blob_container import AzureBlobContainer
    from datadog_sync.utils.storage.gcs_bucket import GCSBucket
    from datadog_sync.utils.storage.local_file import LocalFile
    from datadog_sync.utils.storage.storage_types import StorageType

    resource_per_file = kwargs.get(RESOURCE_PER_FILE, False)
    source_resources_path = kwargs.get(SOURCE_PATH_PARAM, SOURCE_PATH_DEFAULT)
    destination_resources_path = kwargs.get(DESTINATION_PATH_PARAM, DESTINATION_PATH_DEFAULT)

    if type_ == StorageType.LOCAL_FILE:
        return LocalFile(
            source_resources_path=source_resources_path,
            destination_resources_path=destination_resources_path,
            resource_per_file=resource_per_file,
        )
    config = kwargs.get("config", {})
    if type_ == StorageType.AWS_S3_BUCKET:
        if not config:
            raise ValueError("AWS configuration not found")
        return AWSS3Bucket(
            source_resources_path=source_resources_path,
            destination_resources_path=destination_resources_path,
            config=config,
            resource_per_file=resource_per_file,
        )
    if type_ == StorageType.GCS_BUCKET:
        if not config:
            raise ValueError("GCS configuration not found")
        return GCSBucket(
            source_resources_path=source_resources_path,
            destination_resources_path=destination_resources_path,
            config=config,
            resource_per_file=resource_per_file,
        )
    if type_ == StorageType.AZURE_BLOB_CONTAINER:
        if not config:
            raise ValueError("Azure configuration not found")
        return AzureBlobContainer(
            source_resources_path=source_resources_path,
            destination_resources_path=destination_resources_path,
            config=config,
            resource_per_file=resource_per_file,
        )
    raise NotImplementedError(f"Storage type {type_} not implemented")


@dataclass
class StorageData:
    source: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))
    destination: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))


class BaseStorage(ABC):
    """Base class for storage"""

    @staticmethod
    def _sanitize_id_for_filename(resource_id: str) -> str:
        """Replace ':' with '.' for cross-platform filename safety.

        Colons are problematic across storage backends:
        - GCS: explicitly recommends avoiding ':' in object names
        - S3: colons require special handling in some URL contexts
        - Azure: reserved URL characters must be percent-encoded
        - Windows: colons are invalid in filenames

        The original resource ID is always preserved in JSON file content;
        sanitization only affects the filename/object key used in storage.
        """
        return resource_id.replace(":", ".")

    @staticmethod
    def _check_id_collisions(resource_data: dict, resource_type: str) -> set:
        """Return the set of IDs that would collide with an earlier ID's sanitized filename.

        When resource_per_file=True, each resource ID becomes part of the filename.
        Two distinct IDs that differ only by ':' vs '.' (e.g. 'foo:bar' and 'foo.bar')
        would map to the same file. The first ID encountered wins; subsequent colliders
        are returned in the skip set so callers can omit them from the write loop.
        """
        # Dict iteration is insertion-ordered (Python 3.7+). The first ID
        # encountered wins; subsequent colliders are skipped (only logged).
        seen: dict = {}
        skip: set = set()
        for _id in resource_data:
            safe = BaseStorage._sanitize_id_for_filename(_id)
            if safe in seen:
                log.error(
                    "Filename collision for resource type '%s': IDs '%s' and '%s' both "
                    "sanitize to '%s'. Skipping '%s' to prevent overwrite.",
                    resource_type,
                    seen[safe],
                    _id,
                    safe,
                    _id,
                )
                skip.add(_id)
            else:
                seen[safe] = _id
        return skip

    @staticmethod
    def _is_per_resource_filename(resource_type: str, filename: str) -> bool:
        """Return True for <resource_type>.<id>.json, excluding <resource_type>.json."""
        prefix = f"{resource_type}."
        suffix = ".json"
        return filename.startswith(prefix) and filename.endswith(suffix) and len(filename) > len(prefix) + len(suffix)

    @abstractmethod
    def get(self, origin, resource_types=None) -> StorageData:
        """Get resources state from storage.

        Args:
            origin: Which data to load (SOURCE, DESTINATION, or ALL).
            resource_types: If provided, only load files for these resource types.
                            None (default) loads all types — existing behavior.
        """
        pass

    def get_by_ids(self, origin, exact_ids: Dict[str, List[str]]) -> StorageData:
        """Load specific resources by ID, constructing keys directly. No listing needed.

        Args:
            origin: Which data to load (SOURCE, DESTINATION, or ALL).
            exact_ids: Mapping of resource_type -> [id1, id2, ...] to fetch.

        Returns StorageData with only the requested resources. Missing resources
        are silently skipped (no exception raised for NotFound).
        """
        if not getattr(self, "resource_per_file", True):
            raise ValueError("get_by_ids() requires --resource-per-file. Re-run with --resource-per-file enabled.")
        data = StorageData()
        for resource_type, ids in exact_ids.items():
            for resource_id in ids:
                src, dst = self.get_single(resource_type, resource_id)
                if origin in [Origin.SOURCE, Origin.ALL] and src is not None:
                    data.source[resource_type][resource_id] = src
                if origin in [Origin.DESTINATION, Origin.ALL] and dst is not None:
                    data.destination[resource_type][resource_id] = dst
        return data

    @abstractmethod
    def get_single(self, resource_type: str, resource_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load one resource's source and destination state.

        Args:
            resource_type: The resource type (e.g., 'dashboards').
            resource_id: The original (unsanitized) resource ID.

        Returns:
            Tuple of (source_dict_entry, dest_dict_entry).
            Either element is None if the corresponding file does not exist.
            Never raises NotFound exceptions — returns None instead.
        """
        pass

    @abstractmethod
    def put(self, origin, data: StorageData) -> None:
        """Write resources into storage"""
        pass

    def list_filenames(self, origin: Origin, resource_type: str) -> Set[str]:
        """Return full filenames (no path) under <base>/<resource_type>.*.json for the origin.

        Concrete default raises NotImplementedError so out-of-tree backends not yet
        updated for prune fail loudly rather than appearing to find no stale files.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement list_filenames")

    def delete(self, origin: Origin, filename: str) -> None:
        """Delete a single per-resource file by its full filename. No-op if absent."""
        raise NotImplementedError(f"{type(self).__name__} does not implement delete")

    def delete_many(self, origin: Origin, filenames: Iterable[str]) -> Dict[str, str]:
        """Delete multiple files; returns {filename: "ok" | "error: <type>: <msg>"}.
        Default loops self.delete(); backends like S3 may override with batched APIs."""
        results: Dict[str, str] = {}
        for fn in filenames:
            try:
                self.delete(origin, fn)
                results[fn] = "ok"
            except Exception as e:
                results[fn] = f"error: {type(e).__name__}: {e}"
        return results
