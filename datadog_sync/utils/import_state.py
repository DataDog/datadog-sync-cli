# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Write-only state container for the import command.

State.__init__ unconditionally loads existing state from storage; on populated
buckets this can dominate per-run wall-clock even when the run only writes new
data. The import command never reads prior state — it discards in-memory source
state per type (resources_handler.py: `state.clear_source_type(...)`), fetches
fresh resources from the API, and writes the result via `dump_state`. So the
boot-time read is dead weight for import.

ImportState skips the load entirely. It exposes only write methods — no `source`
or `destination` property. Any attempt to read state on an ImportState instance
raises AttributeError at the language level rather than silently returning empty
data.

Suitable for: the import command.
Not suitable for: sync, diffs, migrate, reset, prune (all need to read prior
state). Those commands continue to use the regular State class, which loads on
construction. The Click decorator for --skip-state-load is registered only on
the import subcommand; the CLI parser rejects it elsewhere.
"""

import logging
import time
from typing import Any, Dict, List, Set

from datadog_sync.constants import LOGGER_NAME, Origin, RESOURCE_PER_FILE
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData, build_storage_backend
from datadog_sync.utils.storage.storage_types import StorageType


log = logging.getLogger(LOGGER_NAME)


class ImportState:
    """Write-only state for the import command. Reads are not supported.

    Construction performs NO state load from storage — `_data` starts empty.
    No `source` or `destination` property is exposed: attempting to read state
    raises AttributeError. Writers use `set_source` and `clear_source_type`;
    `dump_state(Origin.SOURCE)` flushes in-memory writes to storage.
    """

    def __init__(self, type_: StorageType = StorageType.LOCAL_FILE, **kwargs: object) -> None:
        init_start = time.perf_counter()
        resource_per_file = kwargs.get(RESOURCE_PER_FILE, False)
        self._storage: BaseStorage = build_storage_backend(type_, **kwargs)
        self._data: StorageData = StorageData()
        self._authoritative_source_types: Set[str] = set()
        log.info(
            "sync-cli-timing phase=state_init storage_type=%s resource_per_file=%s skip_state_load=True wall_ms=%d",
            type_.name,
            resource_per_file,
            int((time.perf_counter() - init_start) * 1000),
        )

    def set_source(self, resource_type: str, _id: str, resource: Dict[str, Any]) -> None:
        """Append/overwrite one resource in the in-memory source state."""
        self._data.source[resource_type][_id] = resource

    def clear_source_type(self, resource_type: str) -> None:
        """Clear the in-memory source dict for one resource type."""
        self._data.source[resource_type].clear()

    def mark_source_authoritative(self, resource_types: List[str]) -> None:
        self._authoritative_source_types.update(resource_types)

    def clear_source_authoritative(self, resource_types: List[str]) -> None:
        for resource_type in resource_types:
            self._authoritative_source_types.discard(resource_type)

    def dump_state(self, origin: Origin = Origin.SOURCE) -> None:
        """Flush in-memory writes to storage. Defaults to SOURCE-only.

        Rejects Origin.DESTINATION / Origin.ALL: import never populates
        destination state, so dumping it would write an empty dict and
        could be misinterpreted by readers as "no destination data".
        """
        if origin != Origin.SOURCE:
            raise ValueError(f"ImportState.dump_state only supports Origin.SOURCE; got {origin}")
        dump_start = time.perf_counter()
        self._storage.put(origin, self._data)
        log.info(
            "sync-cli-timing phase=dump_state origin=%s wall_ms=%d",
            origin.value,
            int((time.perf_counter() - dump_start) * 1000),
        )
