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
from threading import Event

import pytest
from dateutil.parser import parse
from freezegun import freeze_time

from datadog_sync.model.downtime_schedules import DowntimeSchedules
from datadog_sync.utils.resource_utils import SkipResource, check_diff


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


@freeze_time("2026-03-07 15:00:00")
def test_recurring_start_skips_nonexistent_dst_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;BYHOUR=2;BYMINUTE=30"
    resource = _make_recurring_resource([_recurrence("2026-03-07T02:30:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-03-09T02:30:00"
    assert recurrence["rrule"] == rule


@freeze_time("2026-03-07 15:00:00")
def test_recurring_count_does_not_consume_nonexistent_dst_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource([_recurrence("2026-03-07T02:30:00", "FREQ=DAILY;COUNT=3;BYHOUR=2;BYMINUTE=30")])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-03-09T02:30:00"
    assert recurrence["rrule"] == "FREQ=DAILY;COUNT=2;BYHOUR=2;BYMINUTE=30"


@freeze_time("2026-10-31 15:00:00")
def test_recurring_ambiguous_fall_back_start_remains_offset_free(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;BYHOUR=1;BYMINUTE=30"
    resource = _make_recurring_resource([_recurrence("2026-10-31T01:30:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-11-01T01:30:00"
    assert recurrence["rrule"] == rule
    assert "+" not in recurrence["start"]


@freeze_time("2026-08-11 15:00:00")
def test_recurring_future_start_is_untouched(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    resource = _make_recurring_resource([_recurrence("2026-08-12T09:00:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    assert resource["attributes"]["schedule"]["recurrences"][0] == _recurrence("2026-08-12T09:00:00", rule)


@freeze_time("2026-08-12 08:59:30")
def test_recurring_start_within_create_safety_window_advances(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    resource = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", rule)],
        timezone_name="UTC",
    )

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-13T09:00:00"


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
def test_recurring_local_until_is_interpreted_in_schedule_timezone(mock_config):
    downtime = DowntimeSchedules(mock_config)
    rule = "FREQ=DAILY;UNTIL=20260813T090000"
    resource = _make_recurring_resource([_recurrence("2026-08-01T09:00:00", rule)])

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-12T09:00:00"
    assert recurrence["rrule"] == rule


@freeze_time("2026-08-10 00:00:00")
def test_recurring_large_count_is_normalized_without_rejection(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource(
        [_recurrence("2026-06-01T00:00:00", "FREQ=HOURLY;COUNT=200000")],
        timezone_name="UTC",
    )

    _run(downtime.pre_resource_action_hook("new-id", resource))

    recurrence = resource["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-10T01:00:00"
    assert recurrence["rrule"] == "FREQ=HOURLY;COUNT=198319"


@freeze_time("2026-08-11 15:00:00")
def test_recurring_expansion_does_not_block_event_loop(mock_config, monkeypatch):
    downtime = DowntimeSchedules(mock_config)
    resource = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", "FREQ=DAILY")],
        timezone_name="UTC",
    )
    expansion_started = Event()
    allow_expansion = Event()
    original_next_occurrence = DowntimeSchedules._next_valid_occurrence

    def blocking_next_occurrence(cls, rrule, start, cutoff):
        expansion_started.set()
        if not allow_expansion.wait(timeout=1):
            raise AssertionError("recurrence expansion blocked the event loop")
        return original_next_occurrence(rrule, start, cutoff)

    monkeypatch.setattr(DowntimeSchedules, "_next_valid_occurrence", classmethod(blocking_next_occurrence))

    async def normalize_while_event_loop_progresses():
        normalization = asyncio.create_task(downtime.pre_resource_action_hook("new-id", resource))
        while not expansion_started.is_set():
            await asyncio.sleep(0)
        allow_expansion.set()
        await normalization

    _run(normalize_while_event_loop_progresses())


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


def test_update_path_preserves_one_time_schedule_behavior(mock_config):
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


@freeze_time("2026-08-20 15:00:00")
def test_update_recurring_rebased_anchor_does_not_diff_after_another_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    rule = "FREQ=WEEKLY;INTERVAL=1;BYDAY=FR;BYHOUR=19;BYMINUTE=0"
    destination = _make_recurring_resource(
        [_recurrence("2026-08-14T19:00:00", rule, duration="30m")],
    )
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence("2026-07-24T19:00:00", rule, duration="30m")],
    )

    _run(downtime.pre_resource_action_hook(_id, source))

    assert not check_diff(downtime.resource_config, source, destination)
    assert destination["attributes"]["schedule"]["recurrences"][0]["start"] == "2026-08-14T19:00:00"


@freeze_time("2026-08-03 15:00:00")
def test_update_recurring_rebased_count_does_not_diff(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    destination = _make_recurring_resource(
        [_recurrence("2026-08-04T09:00:00", "FREQ=DAILY;COUNT=2;BYHOUR=9")],
        timezone_name="UTC",
    )
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=5;BYHOUR=9")],
        timezone_name="UTC",
    )

    _run(downtime.pre_resource_action_hook(_id, source))

    assert not check_diff(downtime.resource_config, source, destination)


@freeze_time("2026-08-11 15:00:00")
def test_update_recurring_removed_expired_sibling_does_not_diff(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    active_rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=10;BYMINUTE=15"
    destination = _make_recurring_resource([_recurrence("2026-08-14T10:15:00", active_rule, duration="30m")])
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [
            _recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=2"),
            _recurrence("2026-08-07T10:15:00", active_rule, duration="30m"),
        ]
    )

    _run(downtime.pre_resource_action_hook(_id, source))

    assert not check_diff(downtime.resource_config, source, destination)


@pytest.mark.parametrize(
    ("source_start", "source_rule", "source_duration", "source_timezone"),
    [
        ("2026-07-24T19:00:00", "FREQ=WEEKLY;BYDAY=FR;BYHOUR=19;BYMINUTE=0", "45m", "America/New_York"),
        ("2026-07-24T19:00:00", "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR;BYHOUR=19;BYMINUTE=0", "30m", "America/New_York"),
        ("2026-08-28T19:00:00", "FREQ=WEEKLY;BYDAY=FR;BYHOUR=19;BYMINUTE=0", "30m", "America/New_York"),
        ("2026-07-24T19:00:00", "FREQ=WEEKLY;BYDAY=FR;BYHOUR=19;BYMINUTE=0", "30m", "America/Chicago"),
    ],
)
@freeze_time("2026-08-20 15:00:00")
def test_update_recurring_semantic_change_still_diffs(
    mock_config,
    source_start,
    source_rule,
    source_duration,
    source_timezone,
):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=19;BYMINUTE=0"
    destination = _make_recurring_resource([_recurrence("2026-08-14T19:00:00", rule, duration="30m")])
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence(source_start, source_rule, duration=source_duration)],
        timezone_name=source_timezone,
    )

    _run(downtime.pre_resource_action_hook(_id, source))

    assert check_diff(downtime.resource_config, source, destination)


@freeze_time("2026-08-21 18:59:30")
def test_update_payload_advances_recurrence_past_create_safety_window(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=19;BYMINUTE=0"
    destination = _make_recurring_resource(
        [_recurrence("2026-08-14T19:00:00", rule, duration="30m")],
        timezone_name="UTC",
    )
    destination["id"] = "downtime-destination-test"
    destination["attributes"]["message"] = "Original message"
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence("2026-07-24T19:00:00", rule, duration="30m")],
        timezone_name="UTC",
    )
    source["attributes"]["message"] = "Updated message"
    captured = {}

    async def _patch(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"data": payload["data"]}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(_id, source))
    _run(downtime.update_resource(_id, source))

    recurrence = captured["payload"]["data"]["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-08-28T19:00:00"
    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"


@freeze_time("2026-03-07 15:00:00")
def test_update_payload_skips_nonexistent_dst_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    rule = "FREQ=DAILY;COUNT=3;BYHOUR=2;BYMINUTE=30"
    destination = _make_recurring_resource(
        [_recurrence("2026-03-07T02:30:00", rule)],
    )
    destination["id"] = "downtime-destination-test"
    destination["attributes"]["message"] = "Original message"
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence("2026-03-07T02:30:00", rule)],
    )
    source["attributes"]["message"] = "Updated message"
    captured = {}

    async def _patch(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"data": payload["data"]}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(_id, source))
    _run(downtime.update_resource(_id, source))

    recurrence = captured["payload"]["data"]["attributes"]["schedule"]["recurrences"][0]
    assert recurrence["start"] == "2026-03-09T02:30:00"
    assert recurrence["rrule"] == "FREQ=DAILY;COUNT=2;BYHOUR=2;BYMINUTE=30"
    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"


@freeze_time("2026-08-21 15:00:00")
def test_update_payload_omits_expired_schedule_but_preserves_unrelated_change(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    expired_recurrence = _recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=2")
    destination = _make_recurring_resource([expired_recurrence], timezone_name="UTC")
    destination["id"] = "downtime-destination-test"
    destination["attributes"]["message"] = "Original message"
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource([expired_recurrence], timezone_name="UTC")
    source["attributes"]["message"] = "Updated message"
    captured = {}

    async def _patch(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"data": payload["data"]}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(_id, source))
    _run(downtime.update_resource(_id, source))

    attributes = captured["payload"]["data"]["attributes"]
    assert "schedule" not in attributes
    assert attributes["message"] == "Updated message"
    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"


def test_update_payload_preserves_unrelated_change_when_final_recurrence_crosses_cutoff(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    final_recurrence = _recurrence("2026-08-11T19:00:00", "FREQ=DAILY;COUNT=1")
    destination = _make_recurring_resource([final_recurrence], timezone_name="UTC")
    destination["id"] = "downtime-destination-test"
    destination["attributes"]["message"] = "Original message"
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource([final_recurrence], timezone_name="UTC")
    source["attributes"]["message"] = "Updated message"
    captured = {}

    async def _patch(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"data": payload["data"]}

    mock_config.destination_client.patch = _patch

    with freeze_time("2026-08-11 18:58:59"):
        _run(downtime.pre_resource_action_hook(_id, source))
    with freeze_time("2026-08-11 18:59:01"):
        _run(downtime.update_resource(_id, source))

    attributes = captured["payload"]["data"]["attributes"]
    assert "schedule" not in attributes
    assert attributes["message"] == "Updated message"
    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"


@freeze_time("2026-08-21 15:00:00")
def test_update_cancels_active_destination_when_source_recurrence_expired(mock_config):
    downtime = DowntimeSchedules(mock_config)
    _id = "existing-id"
    destination = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", "FREQ=DAILY")],
        timezone_name="UTC",
    )
    destination["id"] = "downtime-destination-test"
    mock_config.state.destination["downtime_schedules"][_id] = destination
    source = _make_recurring_resource(
        [_recurrence("2026-08-01T09:00:00", "FREQ=DAILY;COUNT=2")],
        timezone_name="UTC",
    )
    captured = {}

    async def _delete(path):
        captured["path"] = path

    async def _unexpected_patch(*_args, **_kwargs):
        pytest.fail("an exhausted source must cancel an active destination")

    mock_config.destination_client.delete = _delete
    mock_config.destination_client.patch = _unexpected_patch

    _run(downtime.pre_resource_action_hook(_id, source))
    assert check_diff(downtime.resource_config, source, destination)
    _run(downtime._update_resource(_id, source))

    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"
    assert _id not in mock_config.state.destination["downtime_schedules"]
