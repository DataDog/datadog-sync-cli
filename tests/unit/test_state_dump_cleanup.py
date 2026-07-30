# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the delete-on-drop behavior in State.dump_state.

Under --resource-per-file the write path historically only wrote files for
currently-live IDs. Files for IDs that dropped between two dump_state calls
survived on the storage backend and got re-read into state.source on the
next load_state, resurrecting deleted-source records as apparent-authoritative
content. The fix plumbs the existing compute_stale_files / delete_stale_files
primitives into dump_state, gated on the same authoritative-source marker
that compute_stale_files itself uses.
"""

from datadog_sync.constants import Origin
from datadog_sync.utils.state import State
from datadog_sync.utils.storage.storage_types import StorageType


def _make_state(tmp_path, resource_per_file=True):
    src = tmp_path / "source"
    dst = tmp_path / "dest"
    src.mkdir()
    dst.mkdir()
    state = State(
        type_=StorageType.LOCAL_FILE,
        resource_per_file=resource_per_file,
        source_resources_path=str(src),
        destination_resources_path=str(dst),
    )
    return state, src, dst


def _list_source_files(src, resource_type):
    return sorted(p.name for p in src.iterdir() if p.name.startswith(f"{resource_type}."))


def _list_destination_files(dst, resource_type):
    return sorted(p.name for p in dst.iterdir() if p.name.startswith(f"{resource_type}."))


# ---------- create path (baseline, unchanged) ----------


def test_dump_state_creates_per_id_files_under_resource_per_file(tmp_path):
    state, src, _dst = _make_state(tmp_path)
    state.source["monitors"] = {"a": {"id": "a"}, "b": {"id": "b"}}
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.SOURCE)

    assert _list_source_files(src, "monitors") == ["monitors.a.json", "monitors.b.json"]


# ---------- update path (baseline, unchanged) ----------


def test_dump_state_overwrites_existing_files_for_current_ids(tmp_path):
    state, src, _dst = _make_state(tmp_path)
    (src / "monitors.a.json").write_text('{"a": {"id": "a", "stale": true}}')

    state.source["monitors"] = {"a": {"id": "a", "stale": False}}
    state.mark_source_authoritative(["monitors"])
    state.dump_state(Origin.SOURCE)

    assert '"stale": false' in (src / "monitors.a.json").read_text()


# ---------- new: source-ID-drop cleanup ----------


def test_dump_state_deletes_source_file_for_dropped_id(tmp_path):
    state, src, _dst = _make_state(tmp_path)
    # Simulate a prior dump that produced two files.
    (src / "monitors.keep.json").write_text('{"keep": {"id": "keep"}}')
    (src / "monitors.drop.json").write_text('{"drop": {"id": "drop"}}')

    # New in-memory state has only one of the two IDs (the other was deleted
    # on source and the import phase dropped it from state.source).
    state.source["monitors"] = {"keep": {"id": "keep"}}
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.SOURCE)

    assert _list_source_files(src, "monitors") == ["monitors.keep.json"]


def test_dump_state_deletes_destination_file_for_dropped_source_id(tmp_path):
    """Destination state files are keyed by source ID (state.py:275-277).

    A source ID that dropped from state.source therefore has its
    destination-side cache file cleaned on the same dump. This is what allows
    a subsequent load_state to correctly compute
    ``state.destination - state.source`` for the --cleanup path.
    """
    state, src, dst = _make_state(tmp_path)
    (src / "monitors.keep.json").write_text('{"keep": {"id": "keep"}}')
    (dst / "monitors.keep.json").write_text('{"keep": {"id": "dest_1"}}')
    (dst / "monitors.dropped.json").write_text('{"dropped": {"id": "dest_2"}}')

    state.source["monitors"] = {"keep": {"id": "keep"}}
    # Destination in-memory keeps dropped's mapping (as it would after a
    # successful load_state on a wrapper-orchestrated per-type sync).
    state.destination["monitors"] = {
        "keep": {"id": "dest_1"},
        "dropped": {"id": "dest_2"},
    }
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.ALL)

    assert _list_source_files(src, "monitors") == ["monitors.keep.json"]
    # dropped's destination file removed because its source ID is gone;
    # keep's remains.
    assert _list_destination_files(dst, "monitors") == ["monitors.keep.json"]


# ---------- safety gates ----------


def test_dump_state_does_not_prune_when_type_is_not_authoritative(tmp_path):
    """Partial / filtered / --minimize-reads state.source has no authoritative
    marker. dump_state must not over-prune in that case.
    """
    state, src, _dst = _make_state(tmp_path)
    (src / "monitors.a.json").write_text('{"a": {"id": "a"}}')
    (src / "monitors.b.json").write_text('{"b": {"id": "b"}}')

    # Only a subset in-memory; authoritative NOT marked.
    state.source["monitors"] = {"a": {"id": "a"}}
    # No mark_source_authoritative call.

    state.dump_state(Origin.SOURCE)

    # Both files still present — b.json was NOT pruned.
    assert _list_source_files(src, "monitors") == ["monitors.a.json", "monitors.b.json"]


def test_dump_state_does_not_prune_under_legacy_layout(tmp_path):
    """Legacy (monolithic-per-type) mode is naturally self-cleaning via
    single-file overwrite; the new pruning step must be a no-op there.
    """
    state, src, _dst = _make_state(tmp_path, resource_per_file=False)
    state.source["monitors"] = {"a": {"id": "a"}}
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.SOURCE)

    # Under legacy layout the write path uses a single monitors.json; no
    # per-ID files are produced and the prune step is bypassed.
    assert (src / "monitors.json").exists()
    assert not (src / "monitors.a.json").exists()


def test_dump_state_source_only_does_not_prune_destination(tmp_path):
    """When dump_state is called with Origin.SOURCE, destination-side files
    must be untouched even if they'd be considered stale — the caller is
    only refreshing source.
    """
    state, src, dst = _make_state(tmp_path)
    (dst / "monitors.dropped.json").write_text('{"dropped": {"id": "dest_2"}}')
    state.source["monitors"] = {"keep": {"id": "keep"}}
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.SOURCE)

    # Destination file survives because Origin.SOURCE was requested.
    assert _list_destination_files(dst, "monitors") == ["monitors.dropped.json"]


def test_dump_state_prunes_only_marked_types(tmp_path):
    """If only some types are marked authoritative, only those are pruned.
    Untouched types keep their pre-existing files even if the in-memory
    dict is empty."""
    state, src, _dst = _make_state(tmp_path)
    (src / "monitors.stale.json").write_text('{"stale": {"id": "stale"}}')
    (src / "dashboards.stale.json").write_text('{"stale": {"id": "stale"}}')

    state.source["monitors"] = {}  # emptied
    state.source["dashboards"] = {}  # emptied
    state.mark_source_authoritative(["monitors"])  # only monitors

    state.dump_state(Origin.SOURCE)

    # monitors got pruned; dashboards did not.
    assert _list_source_files(src, "monitors") == []
    assert _list_source_files(src, "dashboards") == ["dashboards.stale.json"]


def test_dump_state_empty_in_memory_prunes_all_files_when_authoritative(tmp_path):
    """Deliberate empty state + authoritative marker means "source has no
    records of this type" and every on-disk file for the type is stale.
    """
    state, src, _dst = _make_state(tmp_path)
    for _id in ("a", "b", "c"):
        (src / f"monitors.{_id}.json").write_text('{"%s": {"id": "%s"}}' % (_id, _id))

    state.source["monitors"] = {}
    state.mark_source_authoritative(["monitors"])

    state.dump_state(Origin.SOURCE)

    assert _list_source_files(src, "monitors") == []


def test_dump_state_does_not_raise_when_no_stale_files(tmp_path):
    state, src, _dst = _make_state(tmp_path)
    state.source["monitors"] = {"a": {"id": "a"}}
    state.mark_source_authoritative(["monitors"])

    # No pre-existing files, no stale set. Should be a clean no-op prune.
    state.dump_state(Origin.SOURCE)

    assert _list_source_files(src, "monitors") == ["monitors.a.json"]


# ---------- id-file / subset-scope interaction ----------


def test_dump_state_respects_partial_subset_when_only_subset_is_authoritative(tmp_path):
    """Regression guard: an import scoped to a subset of source IDs (e.g. via
    --id-file) must NOT cause the new prune to delete files for IDs outside
    the subset. Callers that scope imports to a subset also refrain from
    calling mark_source_authoritative for the scoped type — this test proves
    that discipline is respected end-to-end at the State layer.
    """
    state, src, _dst = _make_state(tmp_path)
    # Simulate cache from a prior full-scope run: three monitor files exist.
    for _id in ("a", "b", "c"):
        (src / f"monitors.{_id}.json").write_text('{"%s": {"id": "%s"}}' % (_id, _id))

    # Current run only imported one ID (as would happen with --id-file). The
    # caller correctly does NOT mark monitors authoritative because only a
    # subset was fetched.
    state.source["monitors"] = {"a": {"id": "a"}}
    # No mark_source_authoritative call for monitors.

    state.dump_state(Origin.SOURCE)

    # b.json and c.json survive because monitors is not authoritative for
    # this dump. Data-loss on --id-file paths would fail this test.
    assert _list_source_files(src, "monitors") == [
        "monitors.a.json",
        "monitors.b.json",
        "monitors.c.json",
    ]


# ---------- resilience ----------


def test_dump_state_kill_switch_disables_prune(tmp_path, monkeypatch):
    """DD_SYNC_CLI_DISABLE_DUMP_PRUNE=1 must fully disable the new prune
    behavior at runtime, without needing a code revert. Operator escape
    hatch for a destructive-cleanup regression discovered in prod.
    """
    state, src, _dst = _make_state(tmp_path)
    (src / "monitors.stale.json").write_text('{"stale": {"id": "stale"}}')
    state.source["monitors"] = {"keep": {"id": "keep"}}
    state.mark_source_authoritative(["monitors"])

    monkeypatch.setenv("DD_SYNC_CLI_DISABLE_DUMP_PRUNE", "1")

    state.dump_state(Origin.SOURCE)

    # Stale file survives because the kill switch was set.
    assert "monitors.stale.json" in _list_source_files(src, "monitors")
    assert "monitors.keep.json" in _list_source_files(src, "monitors")


def test_dump_state_swallows_backend_delete_errors(tmp_path, monkeypatch, caplog):
    """A transient backend error during stale-file delete must not abort
    dump_state's happy path — the write has already succeeded and callers
    rely on returning normally. The stale files remain and next dump_state
    retries."""
    import logging as _logging

    state, src, _dst = _make_state(tmp_path)
    (src / "monitors.stale.json").write_text('{"stale": {"id": "stale"}}')
    state.source["monitors"] = {"keep": {"id": "keep"}}
    state.mark_source_authoritative(["monitors"])

    def boom(*_a, **_kw):
        raise RuntimeError("simulated backend transient")

    monkeypatch.setattr(state, "delete_stale_files", boom)

    # Should not raise despite delete_stale_files exploding.
    with caplog.at_level(_logging.WARNING):
        state.dump_state(Origin.SOURCE)

    # The keep file was written normally; the stale file survives (retry
    # will handle it on the next dump_state cycle).
    files = _list_source_files(src, "monitors")
    assert "monitors.keep.json" in files
    assert "monitors.stale.json" in files
    # Warning was emitted so operators can see the swallowed failure.
    assert any("delete_stale_files" in r.getMessage() for r in caplog.records)
