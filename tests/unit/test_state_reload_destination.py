# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for State.reload_destination().

Motivating case: an external orchestrator runs sync-cli once per resource
type as separate processes against a shared storage backend. An earlier
process may write destination blobs to storage AFTER a later process
loaded state at startup. reload_destination() re-reads the destination
side from storage with insert-if-absent semantics so the later process
can pick up late-arriving blobs without overwriting in-flight local
writes.
"""

from unittest.mock import MagicMock

from datadog_sync.constants import Origin
from datadog_sync.utils.state import State
from datadog_sync.utils.storage._base_storage import StorageData


def _make_state_with_stub_storage(source=None, destination=None):
    """Build a State instance with a stub storage backend that returns the
    given source/destination maps on `get()`. Bypasses the storage factory
    (which requires real config)."""
    state = State.__new__(State)  # skip __init__ which reads real storage
    state._resource_types = None
    state._exact_ids = None
    state._minimize_reads = False
    state._ensure_attempted = set()
    state._bulk_loaded_types = set()
    state._authoritative_source_types = set()
    state._data = StorageData()
    if source:
        for rt, entries in source.items():
            for k, v in entries.items():
                state._data.source[rt][k] = v
    if destination:
        for rt, entries in destination.items():
            for k, v in entries.items():
                state._data.destination[rt][k] = v

    storage = MagicMock()
    state._storage = storage
    return state, storage


def test_reload_destination_empty_types_is_noop():
    state, storage = _make_state_with_stub_storage()
    added = state.reload_destination([])
    assert added == {}
    storage.get.assert_not_called()


def test_reload_destination_loads_new_entries():
    """A destination blob that appeared on storage after startup must be
    loaded into state.destination on refresh."""
    state, storage = _make_state_with_stub_storage(
        destination={"roles": {"old-id": {"name": "OldRole"}}}
    )

    refreshed = StorageData()
    refreshed.destination["roles"]["old-id"] = {"name": "OldRole (stale copy)"}
    refreshed.destination["roles"]["new-id"] = {"name": "NewRole"}
    storage.get.return_value = refreshed

    added = state.reload_destination(["roles"])

    # Only the new entry was added; the pre-existing "old-id" was NOT
    # overwritten (insert-if-absent).
    assert added == {"roles": 1}
    assert state.destination["roles"]["old-id"] == {"name": "OldRole"}, \
        "existing entry must not be overwritten"
    assert state.destination["roles"]["new-id"] == {"name": "NewRole"}


def test_reload_destination_preserves_in_flight_writes():
    """A caller may have written to state.destination[type][id] between
    startup-load and refresh. The refresh must not clobber those writes
    with the storage-side value."""
    state, storage = _make_state_with_stub_storage()
    state._data.destination["monitors"]["mon-1"] = {"in_flight": True}

    refreshed = StorageData()
    refreshed.destination["monitors"]["mon-1"] = {"in_flight": False}
    storage.get.return_value = refreshed

    added = state.reload_destination(["monitors"])
    assert added == {"monitors": 0}
    assert state.destination["monitors"]["mon-1"] == {"in_flight": True}


def test_reload_destination_only_reads_destination_origin():
    """Refresh must not clobber state.source. Storage.get is called with
    Origin.DESTINATION so source is untouched."""
    state, storage = _make_state_with_stub_storage(
        source={"roles": {"src-only": {"name": "SourceOnly"}}}
    )
    storage.get.return_value = StorageData()

    state.reload_destination(["roles"])

    call = storage.get.call_args
    assert call.args[0] == Origin.DESTINATION, \
        "reload_destination must pass Origin.DESTINATION"
    assert state.source["roles"]["src-only"] == {"name": "SourceOnly"}, \
        "source state must be untouched by destination-only refresh"


def test_reload_destination_multiple_types():
    state, storage = _make_state_with_stub_storage()
    refreshed = StorageData()
    refreshed.destination["roles"]["r1"] = {}
    refreshed.destination["roles"]["r2"] = {}
    refreshed.destination["users"]["u1"] = {}
    storage.get.return_value = refreshed

    added = state.reload_destination(["roles", "users"])
    assert added == {"roles": 2, "users": 1}
    assert set(state.destination["roles"].keys()) == {"r1", "r2"}
    assert set(state.destination["users"].keys()) == {"u1"}


def test_reload_destination_missing_type_returns_zero():
    """Requesting a type that has no entries on storage returns count 0 —
    does not raise."""
    state, storage = _make_state_with_stub_storage()
    storage.get.return_value = StorageData()  # empty
    added = state.reload_destination(["nonexistent_type"])
    assert added == {"nonexistent_type": 0}


def test_reload_destination_populates_ensure_attempted():
    """After refresh, keys that were loaded (new or pre-existing) must be in
    _ensure_attempted so a downstream ensure_resource_loaded call is a no-op
    instead of re-fetching via storage.get_single. Mirrors the invariant
    maintained by ensure_resource_type_loaded."""
    state, storage = _make_state_with_stub_storage(
        destination={"roles": {"pre-existing": {"name": "Pre"}}}
    )
    refreshed = StorageData()
    refreshed.destination["roles"]["pre-existing"] = {"name": "Pre (storage copy)"}
    refreshed.destination["roles"]["freshly-loaded"] = {"name": "Fresh"}
    storage.get.return_value = refreshed

    state.reload_destination(["roles"])

    assert ("roles", "pre-existing") in state._ensure_attempted
    assert ("roles", "freshly-loaded") in state._ensure_attempted


def test_reload_destination_dedups_duplicate_types():
    """A caller passing the same type twice must not overwrite added[rt] to 0
    on the second pass (second pass sees all keys as pre-existing)."""
    state, storage = _make_state_with_stub_storage()
    refreshed = StorageData()
    refreshed.destination["roles"]["r1"] = {}
    storage.get.return_value = refreshed

    added = state.reload_destination(["roles", "roles"])
    assert added == {"roles": 1}
    # Storage.get called once with a deduped list.
    call = storage.get.call_args
    assert call.kwargs["resource_types"] == ["roles"]
