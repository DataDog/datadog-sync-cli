# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import logging
import time
from typing import Any, Dict, List, Set, Tuple

from datadog_sync.constants import LOGGER_NAME, Origin, RESOURCE_PER_FILE
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData, build_storage_backend
from datadog_sync.utils.storage.storage_types import StorageType

log = logging.getLogger(LOGGER_NAME)


class State:
    def __init__(self, type_: StorageType = StorageType.LOCAL_FILE, **kwargs: object) -> None:
        init_start = time.perf_counter()
        self._resource_types = kwargs.get("resource_types", None)  # type-scoped loading
        self._exact_ids = kwargs.get("exact_ids", None)  # ID-targeted loading
        self._minimize_reads = self._resource_types is not None or self._exact_ids is not None
        self._ensure_attempted: set = set()  # tracks IDs attempted by ensure_resource_loaded
        self._bulk_loaded_types: set = set()  # tracks types bulk-loaded by ensure_resource_type_loaded
        self._authoritative_source_types: Set[str] = set()
        resource_per_file = kwargs.get(RESOURCE_PER_FILE, False)
        self._storage: BaseStorage = build_storage_backend(type_, **kwargs)
        self._data: StorageData = StorageData()
        self.load_state()
        log.info(
            "sync-cli-timing phase=state_init storage_type=%s resource_per_file=%s minimize_reads=%s wall_ms=%d",
            type_.name,
            resource_per_file,
            self._minimize_reads,
            int((time.perf_counter() - init_start) * 1000),
        )

    @property
    def source(self):
        return self._data.source

    @property
    def destination(self):
        return self._data.destination

    def mark_source_authoritative(self, resource_types: List[str]) -> None:
        """Mark source resource types as complete enough to drive stale-file pruning."""
        self._authoritative_source_types.update(resource_types)

    def clear_source_authoritative(self, resource_types: List[str]) -> None:
        """Clear authoritative-source markers before reloading or re-importing source data."""
        for resource_type in resource_types:
            self._authoritative_source_types.discard(resource_type)

    def load_state(self, origin: Origin = Origin.ALL) -> None:
        load_start = time.perf_counter()
        self._authoritative_source_types.clear()
        if self._exact_ids is not None:
            # ID-targeted: fetch only specified resources by constructing keys directly
            strategy = "id_targeted"
            self._data = self._storage.get_by_ids(origin, self._exact_ids)
        else:
            # Type-scoped (resource_types set) or full load (resource_types=None)
            strategy = "type_scoped" if self._resource_types is not None else "full"
            self._data = self._storage.get(origin, resource_types=self._resource_types)
        log.info(
            "sync-cli-timing phase=load_state origin=%s strategy=%s wall_ms=%d",
            origin.value,
            strategy,
            int((time.perf_counter() - load_start) * 1000),
        )

    def set_source(self, resource_type: str, _id: str, resource: Dict[str, Any]) -> None:
        """Append/overwrite one resource in the in-memory source state.

        Provided to satisfy the SourceStateWriter protocol so import-path code
        can be written against the protocol and work with either State or
        ImportState. State's implementation mutates the underlying dict;
        ImportState's implementation does the same on an unloaded data store.

        Intended for the import command path only. Sync/diffs/migrate code
        continues to read and write `self.source[type][_id]` directly via the
        property accessors. This method is not a sync-path API and adding new
        sync-path callers is discouraged; the existing direct dict access is
        the established pattern for those code paths.
        """
        self._data.source[resource_type][_id] = resource

    def clear_source_type(self, resource_type: str) -> None:
        """Clear the in-memory source dict for one resource type.

        Provided to satisfy the SourceStateWriter protocol; see set_source for
        intent. Intended for the import command path only.
        """
        self._data.source[resource_type].clear()

    def ensure_resource_type_loaded(self, resource_type: str) -> None:
        """Bulk-load all resources of a type into state for full-scan lookups.

        Some connect_id overrides scan all entries of a resource type using
        partial key matching (endswith/startswith on compound keys).
        In minimize-reads mode these entries may not be loaded — this method
        fills the gap by loading the entire type from storage.

        Distinct from ensure_resource_loaded() which loads a single resource
        by exact key. This method loads ALL resources of a type at once.

        No-op when not in minimize-reads mode or when the type is already loaded.
        The type is added to _bulk_loaded_types BEFORE loading to prevent
        infinite retry on persistent storage failures.

        Fully synchronous — safe to call from concurrent asyncio workers
        without locking (Python GIL protects set/dict operations).
        """
        if not self._minimize_reads or resource_type in self._bulk_loaded_types:
            return
        self._bulk_loaded_types.add(resource_type)
        log.debug("minimize-reads: bulk-loading %s for full-scan lookups", resource_type)
        data = self._storage.get(Origin.ALL, resource_types=[resource_type])
        src_loaded = data.source.get(resource_type, {})
        dst_loaded = data.destination.get(resource_type, {})
        if not src_loaded and not dst_loaded:
            log.debug("minimize-reads: bulk-load for %s returned no data from storage", resource_type)
            return
        # Insert-if-absent: never overwrite entries modified during this sync run
        # or loaded earlier by ensure_resource_loaded.
        for key, val in src_loaded.items():
            if key not in self._data.source[resource_type]:
                self._data.source[resource_type][key] = val
        for key, val in dst_loaded.items():
            if key not in self._data.destination[resource_type]:
                self._data.destination[resource_type][key] = val
        # Populate _ensure_attempted so subsequent ensure_resource_loaded() calls
        # for bulk-loaded keys are no-ops (avoids redundant per-resource I/O).
        for key in src_loaded:
            self._ensure_attempted.add((resource_type, key))
        for key in dst_loaded:
            self._ensure_attempted.add((resource_type, key))

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
        dump_start = time.perf_counter()
        self._storage.put(origin, self._data)
        log.info(
            "sync-cli-timing phase=dump_state origin=%s wall_ms=%d",
            origin.value,
            int((time.perf_counter() - dump_start) * 1000),
        )

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

    def compute_stale_files(
        self, origins: List[Origin], resource_types: List[str]
    ) -> Dict[Tuple[Origin, str], Set[str]]:
        """For each (origin, resource_type), return the set of full filenames on
        disk that don't correspond to any in-memory source ID. Pure read; no mutation.

        Precondition: state.source[type] for each requested type must reflect the
        full authoritative source (post-import), with no filter applied — i.e.
        the dict's keyset must be the complete set of source IDs the caller
        wants to keep. Calling this with a partial/filtered state.source will
        mark legitimate files as stale.

        Raises ValueError if a requested type is absent from state.source —
        never silently no-ops, so missing-import bugs surface loudly rather
        than as silent over-pruning.

        Destination state files are keyed by source IDs (verified at
        base_resource.py:184-186 and model/monitors.py:113), so the same
        authoritative ID set covers both Origin.SOURCE and Origin.DESTINATION.
        """
        from datadog_sync.utils.storage._base_storage import BaseStorage

        result: Dict[Tuple[Origin, str], Set[str]] = {}
        for rt in resource_types:
            if rt not in self._authoritative_source_types:
                raise ValueError(f"authoritative source not loaded for type '{rt}'; refusing to compute stale set")
            ids_dict = self._data.source.get(rt, {})
            skip = BaseStorage._check_id_collisions(ids_dict, rt)
            expected = {
                f"{rt}.{BaseStorage._sanitize_id_for_filename(_id)}.json" for _id in ids_dict if _id not in skip
            }
            for origin in origins:
                on_disk = self._storage.list_filenames(origin, rt)
                result[(origin, rt)] = on_disk - expected
        return result

    def delete_stale_files(
        self, stale: Dict[Tuple[Origin, str], Set[str]]
    ) -> Dict[Tuple[Origin, str], Tuple[int, int]]:
        """Delete files via storage.delete_many(). Returns (success, failure)
        per (origin, resource_type). Per-file failure messages from delete_many
        are logged at DEBUG level (one log line per failed filename); aggregate
        counts are returned for the caller to log at INFO level. Never raises
        — partial failures are normal.

        Iteration order: for each resource_type, Origin.DESTINATION is processed
        before Origin.SOURCE. Rationale: if interrupted mid-prune, the operator
        can re-run without first having to recover from a partially-pruned
        source/ directory.
        """
        # Group filenames by resource_type so we can sequence DESTINATION-before-SOURCE per type.
        by_type: Dict[str, Dict[Origin, Set[str]]] = {}
        for (origin, rt), filenames in stale.items():
            by_type.setdefault(rt, {})[origin] = filenames

        counts: Dict[Tuple[Origin, str], Tuple[int, int]] = {}
        # Within each resource_type: DESTINATION first, then SOURCE. Order across types is unconstrained.
        for rt, by_origin in by_type.items():
            for origin in (Origin.DESTINATION, Origin.SOURCE):
                if origin not in by_origin:
                    continue
                filenames = by_origin[origin]
                if not filenames:
                    counts[(origin, rt)] = (0, 0)
                    continue
                results = self._storage.delete_many(origin, filenames)
                ok = sum(1 for v in results.values() if v == "ok")
                fail = len(results) - ok
                for fn, status in results.items():
                    if status != "ok":
                        log.debug("prune: failed to delete %s/%s: %s", origin.value, fn, status)
                counts[(origin, rt)] = (ok, fail)
        return counts

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
