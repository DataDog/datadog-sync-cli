# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for downtime_schedules create-path schedule normalization.

Pins the pre_resource_action_hook create-path behavior for schedule.start
and schedule.end when their values are in the past. Before this fix, only
schedule.start was rewritten forward; a downtime carrying a past `end`
(one-off window both in the past) still hit the destination API and 400'd
with "Downtime cannot be scheduled in the past".
"""

import asyncio
from datetime import datetime, timedelta, timezone

from dateutil.parser import parse

from datadog_sync.model.downtime_schedules import DowntimeSchedules


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _past_iso(seconds_ago: int = 3600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def _future_iso(seconds_ahead: int = 3600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)).isoformat().replace("+00:00", "Z")


def _make_resource(schedule):
    return {"attributes": {"schedule": schedule}}


def test_past_start_bumped_forward(mock_config):
    """Baseline invariant preserved: past schedule.start on create is
    rewritten to ~now. Regression guard so the refactor didn't change the
    pre-existing contract for schedule.start."""
    downtime = DowntimeSchedules(mock_config)
    past = _past_iso(3600)
    resource = _make_resource({"start": past})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    rewritten = resource["attributes"]["schedule"]["start"]
    assert rewritten != past
    assert parse(rewritten).timestamp() > _now_ts() - 5


def test_past_end_bumped_forward(mock_config):
    """New coverage: past schedule.end on create must also be rewritten.
    Without this, one-off downtimes whose `end` predates now still 400 with
    'Downtime cannot be scheduled in the past' — the destination API
    validates the full schedule window, not just start."""
    downtime = DowntimeSchedules(mock_config)
    past_start = _past_iso(7200)
    past_end = _past_iso(3600)
    resource = _make_resource({"start": past_start, "end": past_end})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    schedule = resource["attributes"]["schedule"]
    assert schedule["end"] != past_end
    assert parse(schedule["end"]).timestamp() > _now_ts() - 5
    # `end > start` invariant must hold after bumping — the destination API
    # rejects windows where end precedes start with a separate 400.
    assert parse(schedule["end"]).timestamp() > parse(schedule["start"]).timestamp()


def test_past_end_with_future_start_preserves_ordering(mock_config):
    """Adversarial case surfaced by pre-merge review: source has a future
    `start` (untouched) and a past `end` (needs bumping). Naively bumping
    end to `now+60s` would produce `end < start` and 400 with a different
    error. Bumped end must be at least `start + small_delta`."""
    downtime = DowntimeSchedules(mock_config)
    future_start = _future_iso(3600)
    past_end = _past_iso(3600)
    resource = _make_resource({"start": future_start, "end": past_end})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    schedule = resource["attributes"]["schedule"]
    assert schedule["start"] == future_start, "future start must be untouched"
    assert parse(schedule["end"]).timestamp() > parse(schedule["start"]).timestamp(), (
        "bumped end must land after start to preserve the ordering invariant"
    )


def test_future_fields_untouched(mock_config):
    """Values already in the future must NOT be rewritten. Rewriting a
    future start/end changes the customer's intended window and would
    produce a spurious diff on subsequent syncs."""
    downtime = DowntimeSchedules(mock_config)
    start_future = _future_iso(3600)
    end_future = _future_iso(7200)
    resource = _make_resource({"start": start_future, "end": end_future})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    schedule = resource["attributes"]["schedule"]
    assert schedule["start"] == start_future
    assert schedule["end"] == end_future


def test_missing_fields_no_op(mock_config):
    """Edge case: schedule may omit start or end. The hook must tolerate
    absent keys without raising."""
    downtime = DowntimeSchedules(mock_config)

    # start-only
    r1 = _make_resource({"start": _past_iso()})
    _run(downtime.pre_resource_action_hook("id-1", r1))
    assert "end" not in r1["attributes"]["schedule"]

    # end-only
    r2 = _make_resource({"end": _past_iso()})
    _run(downtime.pre_resource_action_hook("id-2", r2))
    assert "start" not in r2["attributes"]["schedule"]
    assert parse(r2["attributes"]["schedule"]["end"]).timestamp() > _now_ts() - 5

    # empty schedule
    r3 = _make_resource({})
    _run(downtime.pre_resource_action_hook("id-3", r3))
    assert r3["attributes"]["schedule"] == {}


def test_update_path_untouched_by_this_fix(mock_config):
    """Explicit boundary: this change only touches the create branch. The
    update-path branch that clamps source start/end backwards to
    destination's stored values is intentionally out of scope; a follow-up
    is planned to address the update-path case where a downtime already in
    state has a past stored `start`."""
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    mock_config.state.destination["downtime_schedules"][_id] = {
        "attributes": {"schedule": {"start": _past_iso(1800), "end": _past_iso(900)}}
    }

    resource = _make_resource({"start": _past_iso(3600), "end": _past_iso(1200)})

    _run(downtime.pre_resource_action_hook(_id, resource))

    schedule = resource["attributes"]["schedule"]
    dest = mock_config.state.destination["downtime_schedules"][_id]["attributes"]["schedule"]
    assert schedule["start"] == dest["start"]
    assert schedule["end"] == dest["end"]
