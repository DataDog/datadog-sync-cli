# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for downtime_schedules create-path schedule normalization.

Prior behavior only rewrote past `schedule.start` forward. Downtimes with
a past `end` (one-off maintenance windows that already closed on the
source) still hit the destination API and 400'd with "Downtime cannot be
scheduled in the past".

New behavior:
- Past `schedule.end` → SkipResource (ended downtimes are not replicated).
- Past `schedule.start` with future/absent `end` → bump `start` to now+60s
  and leave `end` as-is (window may shrink, original end time preserved).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from dateutil.parser import parse
from freezegun import freeze_time

from datadog_sync.model.downtime_schedules import DowntimeSchedules
from datadog_sync.utils.resource_utils import SkipResource


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests, so asyncio.get_event_loop() may raise "no current event
    # loop" when this helper runs after unrelated async tests in the suite.
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
    """Baseline invariant: past schedule.start with no `end` (open-ended
    downtime) is rewritten to ~now+60s. Regression guard so the refactor
    didn't change the pre-existing contract."""
    downtime = DowntimeSchedules(mock_config)
    past = _past_iso(3600)
    resource = _make_resource({"start": past})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    rewritten = resource["attributes"]["schedule"]["start"]
    now_ts = _now_ts()
    assert rewritten != past
    assert now_ts - 5 < parse(rewritten).timestamp() < now_ts + 120


def test_past_start_future_end_preserves_end(mock_config):
    """Past `start` with a future `end`: bump `start` forward, leave `end`
    alone. The customer's maintenance still ends at their intended time —
    the window may be shorter than the source's, but the end boundary is
    honored."""
    downtime = DowntimeSchedules(mock_config)
    past_start = _past_iso(3600)
    future_end = _future_iso(3600)
    resource = _make_resource({"start": past_start, "end": future_end})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    schedule = resource["attributes"]["schedule"]
    assert schedule["start"] != past_start
    assert schedule["end"] == future_end, "future end must be untouched"
    # `end > start` invariant still holds because start is now ~now+60s and
    # end is ~+3600s.
    assert parse(schedule["end"]).timestamp() > parse(schedule["start"]).timestamp()


def test_past_end_raises_skip(mock_config):
    """New behavior: past `end` means the downtime has already ended on the
    source. Skip the resource — replicating an expired maintenance to the
    destination either produces a 400 or invents a phantom window."""
    downtime = DowntimeSchedules(mock_config)
    resource = _make_resource({"start": _past_iso(7200), "end": _past_iso(3600)})

    with pytest.raises(SkipResource) as excinfo:
        _run(downtime.pre_resource_action_hook("skip-id", resource))

    assert "past" in str(excinfo.value).lower()


def test_past_end_raises_skip_even_with_future_start(mock_config):
    """Degenerate but possible source shape: `end` in the past AND `start`
    in the future (source is a broken record). Still skip — the window
    doesn't make sense to replicate."""
    downtime = DowntimeSchedules(mock_config)
    resource = _make_resource({"start": _future_iso(3600), "end": _past_iso(3600)})

    with pytest.raises(SkipResource):
        _run(downtime.pre_resource_action_hook("skip-id", resource))


def test_future_start_and_end_untouched(mock_config):
    """Values already in the future must NOT be rewritten. Rewriting would
    change the customer's intended window and produce a spurious diff on
    subsequent syncs."""
    downtime = DowntimeSchedules(mock_config)
    start_future = _future_iso(3600)
    end_future = _future_iso(7200)
    resource = _make_resource({"start": start_future, "end": end_future})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    schedule = resource["attributes"]["schedule"]
    assert schedule["start"] == start_future
    assert schedule["end"] == end_future


def test_missing_or_null_schedule_no_op(mock_config):
    """Edge cases: schedule may be absent, empty, or None. The hook must
    tolerate all three without raising."""
    downtime = DowntimeSchedules(mock_config)

    # empty schedule
    r1 = _make_resource({})
    _run(downtime.pre_resource_action_hook("id-1", r1))
    assert r1["attributes"]["schedule"] == {}

    # schedule is None
    r2 = {"attributes": {"schedule": None}}
    _run(downtime.pre_resource_action_hook("id-2", r2))
    assert r2["attributes"]["schedule"] is None

    # attributes.schedule key absent
    r3 = {"attributes": {}}
    _run(downtime.pre_resource_action_hook("id-3", r3))
    assert r3 == {"attributes": {}}


def test_start_only_no_end_field(mock_config):
    """Open-ended downtime (no `end` key at all): past `start` is rewritten,
    the missing-`end` shape is preserved (not injected)."""
    downtime = DowntimeSchedules(mock_config)
    resource = _make_resource({"start": _past_iso()})

    _run(downtime.pre_resource_action_hook("id", resource))

    schedule = resource["attributes"]["schedule"]
    assert "end" not in schedule
    assert parse(schedule["start"]).timestamp() > _now_ts() - 5


# --- Recurring downtime recurrence-level normalization ----------------------


def _recurrence(start, rrule, duration="1h"):
    """Return the documented v2 recurring-downtime payload shape."""
    return {"start": start, "duration": duration, "rrule": rrule}


def _make_recurring_resource(recurrences, timezone_name="America/New_York"):
    return {
        "attributes": {
            "schedule": {
                "timezone": timezone_name,
                "recurrences": recurrences,
            }
        }
    }


@freeze_time("2026-08-11 15:00:00")
def test_recurring_past_start_advances_to_next_rrule_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;BYHOUR=9;BYMINUTE=0"
    resource = _make_recurring_resource([_recurrence("2026-08-03T09:00:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-17T09:00:00"
    assert recurrence["rrule"] == rule
    assert recurrence["duration"] == "1h"


@freeze_time("2026-03-08 15:00:00")
def test_recurring_start_preserves_local_time_across_dst(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource([_recurrence("2026-03-06T09:30:00", "FREQ=DAILY;BYHOUR=9;BYMINUTE=30")])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-03-09T09:30:00"
    assert not recurrence["start"].endswith("Z")
    assert "+" not in recurrence["start"]


@freeze_time("2026-08-11 15:00:00")
def test_recurring_future_start_is_untouched(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    resource = _make_recurring_resource([_recurrence("2026-08-12T09:00:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert resource["attributes"]["schedule"]["recurrences"][0] == _recurrence("2026-08-12T09:00:00", rule)


@freeze_time("2026-08-11 15:00:00")
def test_recurring_expired_rule_is_removed_but_active_sibling_is_preserved(mock_config):
    downtime = DowntimeSchedules(mock_config)
    expired = _recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=2")
    active_rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=10;BYMINUTE=15"
    active = _recurrence("2026-08-07T10:15:00", active_rule, duration="30m")
    resource = _make_recurring_resource([expired, active])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [
        _recurrence("2026-08-14T10:15:00", active_rule, duration="30m")
    ]


@freeze_time("2026-08-11 15:00:00")
def test_recurring_all_expired_rules_raise_skip(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource(
        [
            _recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=2"),
            _recurrence("2026-08-03T09:00:00", "FREQ=DAILY;UNTIL=20260805T130000Z"),
        ]
    )

    with pytest.raises(SkipResource) as excinfo:
        _run(downtime.pre_resource_action_hook("skip-id", resource))

    assert "future occurrences" in str(excinfo.value).lower()


@freeze_time("2026-08-03 15:00:00")
def test_recurring_count_is_reduced_to_remaining_occurrences(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource([_recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=5;BYHOUR=9")])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-04T09:00:00"
    assert recurrence["rrule"] == "FREQ=DAILY;COUNT=2;BYHOUR=9"


@freeze_time("2026-08-11 15:00:00")
def test_recurring_missing_start_is_left_for_api_default(mock_config):
    downtime = DowntimeSchedules(mock_config)
    recurrence = {"duration": "1h", "rrule": "FREQ=DAILY"}
    resource = _make_recurring_resource([recurrence], timezone_name="UTC")

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [recurrence]


@freeze_time("2026-08-11 15:00:00")
def test_recurring_unknown_timezone_raises_value_error(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", "FREQ=DAILY")],
        timezone_name="Invalid/Example",
    )

    with pytest.raises(ValueError, match="Unknown schedule timezone"):
        _run(downtime.pre_resource_action_hook("new-id", resource))


def test_recurring_no_recurrences_key_no_op(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_resource({"timezone": "UTC"})

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert "recurrences" not in resource["attributes"]["schedule"]


def test_recurring_empty_recurrences_no_op(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource([], timezone_name="UTC")

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == []


def test_update_path_untouched_by_this_fix(mock_config):
    """Explicit boundary: this change only touches the create branch. The
    update-path branch that clamps source start/end backwards to
    destination's stored values is intentionally out of scope; a follow-up
    is planned to address the update-path case."""
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    mock_config.state.destination["downtime_schedules"][_id] = {
        "attributes": {"schedule": {"start": _past_iso(1800), "end": _past_iso(900)}}
    }

    resource = _make_resource({"start": _past_iso(3600), "end": _past_iso(1200)})

    # No SkipResource on update path even though end is past — update-path
    # semantics are intentionally out of scope for this PR.
    _run(downtime.pre_resource_action_hook(_id, resource))

    schedule = resource["attributes"]["schedule"]
    dest = mock_config.state.destination["downtime_schedules"][_id]["attributes"]["schedule"]
    assert schedule["start"] == dest["start"]
    assert schedule["end"] == dest["end"]
