# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from datadog_sync.constants import LOGGER_NAME


log = logging.getLogger(LOGGER_NAME)


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
    def _check_id_collisions(resource_data: dict, resource_type: str) -> None:
        """Log an error for any two IDs that sanitize to the same filename segment.

        When resource_per_file=True, each resource ID becomes part of the filename.
        Two distinct IDs that differ only by ':' vs '.' (e.g. 'foo:bar' and 'foo.bar')
        would map to the same file, causing the second write to silently overwrite the first.
        """
        seen: dict = {}
        for _id in resource_data:
            safe = BaseStorage._sanitize_id_for_filename(_id)
            if safe in seen:
                log.error(
                    "Filename collision for resource type '%s': IDs '%s' and '%s' both "
                    "sanitize to '%s'. Only one will be written to storage.",
                    resource_type,
                    seen[safe],
                    _id,
                    safe,
                )
            else:
                seen[safe] = _id

    @abstractmethod
    def get(self, origin, resource_types=None) -> StorageData:
        """Get resources state from storage.

        Args:
            origin: Which data to load (SOURCE, DESTINATION, or ALL).
            resource_types: If provided, only load files for these resource types.
                            None (default) loads all types — existing behavior.
        """
        pass

    @abstractmethod
    def get_by_ids(self, origin, exact_ids: Dict[str, List[str]]) -> StorageData:
        """Load specific resources by ID, constructing keys directly. No listing needed.

        Args:
            origin: Which data to load (SOURCE, DESTINATION, or ALL).
            exact_ids: Mapping of resource_type -> [id1, id2, ...] to fetch.

        Returns StorageData with only the requested resources. Missing resources
        are silently skipped (no exception raised for NotFound).
        """
        pass

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
