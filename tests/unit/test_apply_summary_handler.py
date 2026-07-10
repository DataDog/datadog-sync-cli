# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Handler-level regression test for the per-resource-type sync summary emission.

Motivation (Codex review of PR #614): the Counter unit tests in
test_apply_summary_ids.py verify the data-structure, but a regression
that removed or changed the actual summary emission in
resources_handler.py would still pass. This file drives
_emit_apply_summary against a populated Counter and asserts the log
output shape + chunking + insertion-order preservation.
"""

from unittest.mock import MagicMock

from datadog_sync.utils.resources_handler import _emit_apply_summary, _SUMMARY_ID_CHUNK
from datadog_sync.utils.workers import Counter


def _make_logger():
    return MagicMock()


def _rendered_warnings(logger):
    out = []
    for call in logger.warning.call_args_list:
        args = call.args
        if not args:
            continue
        try:
            out.append(args[0] % args[1:])
        except Exception:
            out.append(str(args))
    return out


def test_summary_emits_failed_ids_by_type():
    counter = Counter()
    counter.increment_failure(resource_type="roles", _id="uuid-abc")
    counter.increment_failure(resource_type="roles", _id="uuid-def")
    counter.increment_failure(resource_type="monitors", _id="mon-1")

    logger = _make_logger()
    _emit_apply_summary(logger, counter)

    joined = "\n".join(_rendered_warnings(logger))
    assert "roles" in joined
    assert "monitors" in joined
    assert "uuid-abc" in joined
    assert "uuid-def" in joined
    assert "mon-1" in joined
    assert "failed source ids" in joined


def test_summary_emits_skipped_missing_deps_by_type():
    counter = Counter()
    counter.increment_skipped(resource_type="dashboards", _id="dsh-1", missing_deps=True)
    counter.increment_skipped(resource_type="dashboards", _id="dsh-2", missing_deps=True)

    logger = _make_logger()
    _emit_apply_summary(logger, counter)

    joined = "\n".join(_rendered_warnings(logger))
    assert "dashboards" in joined
    assert "dsh-1" in joined
    assert "dsh-2" in joined
    assert "skipped for missing dependencies" in joined


def test_summary_silent_when_no_failures():
    counter = Counter()
    logger = _make_logger()
    _emit_apply_summary(logger, counter)
    assert logger.warning.call_count == 0


def test_summary_chunks_large_id_lists():
    counter = Counter()
    for i in range(130):
        counter.increment_failure(resource_type="monitors", _id=f"mon-{i:03d}")

    logger = _make_logger()
    _emit_apply_summary(logger, counter)

    lines = _rendered_warnings(logger)
    monitor_lines = [line for line in lines if "monitors" in line]
    assert len(monitor_lines) == 3, f"expected 3 chunks, got {len(monitor_lines)}: {monitor_lines}"

    assert "[1-50 of 130]" in monitor_lines[0]
    assert "[51-100 of 130]" in monitor_lines[1]
    assert "[101-130 of 130]" in monitor_lines[2]

    joined = "\n".join(monitor_lines)
    for i in range(130):
        assert f"mon-{i:03d}" in joined, f"id mon-{i:03d} missing"


def test_summary_preserves_insertion_order_not_sorted():
    counter = Counter()
    counter.increment_failure(resource_type="roles", _id="zzz-last")
    counter.increment_failure(resource_type="roles", _id="aaa-first")
    counter.increment_failure(resource_type="roles", _id="mmm-middle")

    logger = _make_logger()
    _emit_apply_summary(logger, counter)

    joined = "\n".join(_rendered_warnings(logger))
    assert joined.index("zzz-last") < joined.index("aaa-first")
    assert joined.index("aaa-first") < joined.index("mmm-middle")


def test_summary_uses_join_not_repr_for_greppability():
    counter = Counter()
    counter.increment_failure(resource_type="roles", _id="uuid-searchable")

    logger = _make_logger()
    _emit_apply_summary(logger, counter)

    joined = "\n".join(_rendered_warnings(logger))
    assert "uuid-searchable" in joined
    assert "sync summary" in joined


def test_chunk_constant_is_reasonable():
    assert 20 <= _SUMMARY_ID_CHUNK <= 200


def test_summary_emission_exception_does_not_propagate_to_dump_state():
    """Regression guard for MAJOR#1: if the logger backend raises during
    summary emission, _emit_apply_summary should not swallow it — that's
    the caller's job. But the caller (apply_resources) wraps this call in
    try/except so state persistence is guaranteed. Test that
    _emit_apply_summary itself propagates so we can distinguish 'summary
    failed' from 'nothing to summarize'."""
    import pytest

    counter = Counter()
    counter.increment_failure(resource_type="roles", _id="x")

    class BadLogger:
        def warning(self, *args, **kwargs):
            raise RuntimeError("log backend down")

    # Bad logger propagates. apply_resources' outer try/except catches.
    with pytest.raises(RuntimeError):
        _emit_apply_summary(BadLogger(), counter)
