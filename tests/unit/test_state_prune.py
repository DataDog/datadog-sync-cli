# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for State.compute_stale_files / delete_stale_files (PR 2)."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.resources_handler import ResourcesHandler
from datadog_sync.utils.state import State
from datadog_sync.utils.storage.storage_types import StorageType


def _make_state(tmp_path):
    """Build a State backed by LocalFile with resource_per_file=True.

    State.__init__ calls load_state() which reads from disk; with empty
    directories that's a no-op and source/destination remain empty.
    """
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()
    state = State(
        type_=StorageType.LOCAL_FILE,
        resource_per_file=True,
        source_resources_path=str(src),
        destination_resources_path=str(dst),
    )
    return state, src, dst


def _seed(directory, *filenames):
    for fn in filenames:
        (directory / fn).write_text("{}")


def _mark_authoritative(state, *resource_types):
    state.mark_source_authoritative(list(resource_types))


class _ImportResource:
    resource_type = "monitors"

    def __init__(self, state, fail_import=False):
        self.state = state
        self.fail_import = fail_import

    async def _get_resources(self, _client):
        return [{"id": "kept"}]

    def filter(self, _resource):
        return True

    async def _import_resource(self, resource=None):
        if self.fail_import:
            raise RuntimeError("import failed")
        self.state.source[self.resource_type][resource["id"]] = resource

    async def _send_action_metrics(self, *_args, **_kwargs):
        pass


async def _run_import_without_saving(state, resource):
    config = SimpleNamespace(
        state=state,
        resources={"monitors": resource},
        resources_arg=["monitors"],
        source_client=None,
        show_progress_bar=False,
        max_workers=2,
        logger=MagicMock(),
        emit_json=False,
        id_payload=None,
        fatal_error=False,
    )
    handler = ResourcesHandler(config)
    await handler.init_async()
    await handler.import_resources_without_saving()


class TestComputeStaleFiles:
    def test_empty_disk_with_authoritative_source(self, tmp_path):
        """No on-disk files → empty stale set even if state.source has IDs."""
        state, _, _ = _make_state(tmp_path)
        state.source["monitors"] = {"id1": {"name": "m"}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE, Origin.DESTINATION], ["monitors"])
        assert result == {(Origin.SOURCE, "monitors"): set(), (Origin.DESTINATION, "monitors"): set()}

    def test_disk_matches_source_no_stale(self, tmp_path):
        """Populated disk that exactly matches state.source → no stale files."""
        state, src, dst = _make_state(tmp_path)
        _seed(src, "monitors.id1.json", "monitors.id2.json")
        _seed(dst, "monitors.id1.json", "monitors.id2.json")
        state.source["monitors"] = {"id1": {}, "id2": {}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE, Origin.DESTINATION], ["monitors"])
        assert result == {(Origin.SOURCE, "monitors"): set(), (Origin.DESTINATION, "monitors"): set()}

    def test_disk_has_extras(self, tmp_path):
        """Files on disk for IDs not in state.source → flagged stale on both origins."""
        state, src, dst = _make_state(tmp_path)
        _seed(src, "monitors.kept.json", "monitors.stale1.json", "monitors.stale2.json")
        _seed(dst, "monitors.kept.json", "monitors.stale1.json")
        state.source["monitors"] = {"kept": {}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE, Origin.DESTINATION], ["monitors"])
        assert result[(Origin.SOURCE, "monitors")] == {"monitors.stale1.json", "monitors.stale2.json"}
        assert result[(Origin.DESTINATION, "monitors")] == {"monitors.stale1.json"}

    def test_missing_type_raises_value_error(self, tmp_path):
        """Type listed in args but not in state.source → ValueError, never silent no-op."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.x.json")
        # state.source["monitors"] is intentionally not populated
        with pytest.raises(ValueError, match="authoritative source not loaded for type 'monitors'"):
            state.compute_stale_files([Origin.SOURCE], ["monitors"])

    def test_empty_source_key_without_authoritative_import_raises(self, tmp_path):
        """A failed import can create/clear state.source[type]; that empty key must not authorize pruning."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.kept.json")
        state.source["monitors"].clear()
        with pytest.raises(ValueError, match="authoritative source not loaded for type 'monitors'"):
            state.compute_stale_files([Origin.SOURCE], ["monitors"])

    def test_authoritative_empty_source_marks_all_files_stale(self, tmp_path):
        """A successful zero-resource import can explicitly authorize pruning all per-resource files."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.old.json")
        state.source["monitors"].clear()
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        assert result[(Origin.SOURCE, "monitors")] == {"monitors.old.json"}

    def test_id_with_colon_round_trips(self, tmp_path):
        """ID 'foo:bar' on disk as 'monitors.foo.bar.json' must NOT be flagged stale
        when state.source has 'foo:bar'. Sanitization happens in expected-set construction."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.foo.bar.json")  # colon-sanitized filename
        state.source["monitors"] = {"foo:bar": {}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        assert result[(Origin.SOURCE, "monitors")] == set()

    def test_id_with_dot_treated_opaquely(self, tmp_path):
        """ID 'abc.def' (no colon, dots preserved) round-trips opaquely."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.abc.def.json")
        state.source["monitors"] = {"abc.def": {}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        assert result[(Origin.SOURCE, "monitors")] == set()

    def test_collision_winner_keeps_file(self, tmp_path):
        """Two source IDs that sanitize to the same filename: only the FIRST wins
        per _check_id_collisions. The collision-loser's filename is still in the
        expected set (because the winner's sanitized filename is identical), so
        the on-disk file is preserved (it's the winner's file by name).

        This test asserts the *invariant*: no stale flag for the colliding filename."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.foo.bar.json")
        # Two distinct IDs that sanitize to "foo.bar"
        state.source["monitors"] = {"foo:bar": {}, "foo.bar": {}}
        _mark_authoritative(state, "monitors")
        result = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        # The single on-disk file 'monitors.foo.bar.json' is in the expected set
        # (winner's filename), so it must NOT be stale.
        assert result[(Origin.SOURCE, "monitors")] == set()

    def test_successful_import_marks_source_authoritative(self, tmp_path):
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.kept.json")
        asyncio.run(_run_import_without_saving(state, _ImportResource(state)))
        result = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        assert result[(Origin.SOURCE, "monitors")] == set()

    def test_failed_resource_import_does_not_mark_source_authoritative(self, tmp_path):
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.kept.json")
        asyncio.run(_run_import_without_saving(state, _ImportResource(state, fail_import=True)))
        with pytest.raises(ValueError, match="authoritative source not loaded for type 'monitors'"):
            state.compute_stale_files([Origin.SOURCE], ["monitors"])


class TestDeleteStaleFiles:
    def test_deletes_files_and_returns_counts(self, tmp_path):
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.a.json", "monitors.b.json", "monitors.c.json")
        stale = {(Origin.SOURCE, "monitors"): {"monitors.a.json", "monitors.b.json"}}
        counts = state.delete_stale_files(stale)
        assert counts == {(Origin.SOURCE, "monitors"): (2, 0)}
        assert (src / "monitors.a.json").exists() is False
        assert (src / "monitors.b.json").exists() is False
        assert (src / "monitors.c.json").exists()  # untouched

    def test_partial_failure_logs_at_debug(self, tmp_path, caplog, monkeypatch):
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.ok.json")
        # Intercept storage.delete_many to inject a partial-failure result
        original_delete_many = state._storage.delete_many

        def fake_delete_many(origin, filenames):
            result = {}
            for fn in filenames:
                if "fail" in fn:
                    result[fn] = "error: PermissionError: nope"
                else:
                    original_delete_many(origin, [fn])
                    result[fn] = "ok"
            return result

        monkeypatch.setattr(state._storage, "delete_many", fake_delete_many)
        stale = {(Origin.SOURCE, "monitors"): {"monitors.ok.json", "monitors.fail.json"}}
        with caplog.at_level(logging.DEBUG, logger="datadog_sync_cli"):
            counts = state.delete_stale_files(stale)
        assert counts == {(Origin.SOURCE, "monitors"): (1, 1)}
        # Per-file failure must be logged at DEBUG with the failing filename
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("monitors.fail.json" in m for m in debug_messages)

    def test_state_in_memory_unchanged(self, tmp_path):
        """delete_stale_files is a pure side-effect on disk; in-memory state untouched."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.stale.json")
        state.source["monitors"] = {"kept": {"some": "data"}}
        state.delete_stale_files({(Origin.SOURCE, "monitors"): {"monitors.stale.json"}})
        # state.source must be unchanged
        assert dict(state.source["monitors"]) == {"kept": {"some": "data"}}

    def test_idempotency(self, tmp_path):
        """compute → delete → compute again returns empty (no remaining stale files)."""
        state, src, _ = _make_state(tmp_path)
        _seed(src, "monitors.kept.json", "monitors.stale.json")
        state.source["monitors"] = {"kept": {}}
        _mark_authoritative(state, "monitors")
        first = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        state.delete_stale_files(first)
        second = state.compute_stale_files([Origin.SOURCE], ["monitors"])
        assert second[(Origin.SOURCE, "monitors")] == set()

    def test_destination_iterated_before_source(self, tmp_path):
        """For each resource_type, DESTINATION is processed before SOURCE.
        Capture the call order on a mocked storage.delete_many."""
        state, _, _ = _make_state(tmp_path)
        calls: list = []

        def record_call(origin, filenames):
            calls.append((origin, list(filenames)[0] if filenames else None))
            return {fn: "ok" for fn in filenames}

        state._storage.delete_many = record_call  # type: ignore[assignment]

        stale = {
            (Origin.SOURCE, "monitors"): {"monitors.s.json"},
            (Origin.DESTINATION, "monitors"): {"monitors.d.json"},
            (Origin.SOURCE, "dashboards"): {"dashboards.s.json"},
            (Origin.DESTINATION, "dashboards"): {"dashboards.d.json"},
        }
        state.delete_stale_files(stale)

        # Within each resource_type, DESTINATION must precede SOURCE.
        # Build per-type ordered call list.
        from collections import defaultdict

        per_type = defaultdict(list)
        for origin, fn in calls:
            rt = fn.split(".")[0] if fn else None
            per_type[rt].append(origin)
        for rt, ordered in per_type.items():
            assert ordered[0] == Origin.DESTINATION, f"Expected DESTINATION before SOURCE for {rt}, got {ordered}"
            assert Origin.SOURCE in ordered, f"SOURCE not called for {rt}"
