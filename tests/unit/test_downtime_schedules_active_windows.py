# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Active-window behavior for recurring v2 downtime schedules."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from freezegun import freeze_time

from datadog_sync.model.downtime_schedules import DowntimeSchedules
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource, check_diff


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _recurrence(start, rrule="FREQ=DAILY", duration="2h"):
    recurrence = {"duration": duration, "rrule": rrule}
    if start is not None:
        recurrence["start"] = start
    return recurrence


def _current(start, end):
    return {"start": start, "end": end}


def _bridge(start="2026-08-11T15:00:00", duration="60m"):
    return {"start": start, "duration": duration, "rrule": "FREQ=DAILY;COUNT=1"}


def _resource(recurrences, current_downtime=None, message=None):
    schedule = {"timezone": "UTC", "recurrences": recurrences}
    if current_downtime is not None:
        schedule["current_downtime"] = current_downtime
    attributes = {"schedule": schedule}
    if message is not None:
        attributes["message"] = message
    return {"attributes": attributes}


def _seed_destination(mock_config, source_id, resource):
    destination = deepcopy(resource)
    destination["id"] = "downtime-destination-test"
    mock_config.state.destination["downtime_schedules"][source_id] = destination
    return destination


@freeze_time("2026-08-11 15:00:00")
def test_create_active_recurrence_adds_remaining_window_bridge_before_future_cadence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-11T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )

    _run(downtime.pre_resource_action_hook("downtime-source-test", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [
        _bridge(),
        _recurrence("2026-08-12T14:00:00"),
    ]


@freeze_time("2026-08-11 15:00:00")
def test_create_active_count_preserves_only_unconsumed_future_occurrences(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-10T14:00:00", "FREQ=DAILY;COUNT=3")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )

    _run(downtime.pre_resource_action_hook("downtime-source-test", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [
        _bridge(),
        _recurrence("2026-08-12T14:00:00", "FREQ=DAILY;COUNT=1"),
    ]


@freeze_time("2026-08-11 15:00:00")
def test_create_active_window_under_one_minute_does_not_overmute(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-11T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T15:00:45+00:00"),
    )

    _run(downtime.pre_resource_action_hook("downtime-source-test", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [_recurrence("2026-08-12T14:00:00")]


@freeze_time("2026-08-11 15:00:00")
def test_create_inactive_recurrence_only_materializes_future_cadence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-11T14:00:00")],
        _current("2026-08-12T14:00:00+00:00", "2026-08-12T16:00:00+00:00"),
    )

    _run(downtime.pre_resource_action_hook("downtime-source-test", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [_recurrence("2026-08-12T14:00:00")]


@freeze_time("2026-08-11 15:00:00")
def test_create_active_final_occurrence_materializes_bridge_only(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-11T14:00:00", "FREQ=DAILY;COUNT=1")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )

    _run(downtime.pre_resource_action_hook("downtime-source-test", resource))

    assert resource["attributes"]["schedule"]["recurrences"] == [_bridge()]


@freeze_time("2026-08-11 15:00:00")
def test_create_inactive_exhausted_recurrence_is_skipped(mock_config):
    downtime = DowntimeSchedules(mock_config)
    resource = _resource(
        [_recurrence("2026-08-10T14:00:00", "FREQ=DAILY;COUNT=1")],
        _current("2026-08-10T14:00:00+00:00", "2026-08-10T16:00:00+00:00"),
    )

    with pytest.raises(SkipResource, match="no future occurrences or active window"):
        _run(downtime.pre_resource_action_hook("downtime-source-test", resource))


@freeze_time("2026-08-11 15:00:00")
def test_update_equivalent_active_window_omits_schedule_from_unrelated_patch(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    destination = _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-11T14:00:00")],
            _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
            message="Before",
        ),
    )
    source = _resource(
        [_recurrence("2026-08-01T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
        message="After",
    )
    captured = {}

    async def _patch(path, payload):
        captured["path"] = path
        captured["payload"] = deepcopy(payload)
        response = deepcopy(destination)
        response["attributes"]["message"] = "After"
        return {"data": response}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(source_id, source))
    assert check_diff(downtime.resource_config, source, destination)
    _run(downtime.update_resource(source_id, source))

    assert "schedule" not in captured["payload"]["data"]["attributes"]
    assert captured["payload"]["data"]["attributes"]["message"] == "After"


@freeze_time("2026-08-11 15:00:00")
def test_update_inactive_destination_adds_bridge_without_recreate(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-12T14:00:00")],
            _current("2026-08-12T14:00:00+00:00", "2026-08-12T16:00:00+00:00"),
        ),
    )
    source = _resource(
        [_recurrence("2026-08-11T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )
    captured = {}

    async def _patch(_path, payload):
        captured["payload"] = deepcopy(payload)
        return {"data": payload["data"]}

    async def _unexpected_delete(_path):
        pytest.fail("an inactive destination can be updated in place")

    mock_config.destination_client.patch = _patch
    mock_config.destination_client.delete = _unexpected_delete

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime.update_resource(source_id, source))

    assert captured["payload"]["data"]["attributes"]["schedule"]["recurrences"] == [
        _bridge(),
        _recurrence("2026-08-12T14:00:00"),
    ]


@freeze_time("2026-08-11 15:00:00")
def test_update_equivalent_active_final_occurrence_is_not_canceled(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    recurrence = _recurrence("2026-08-11T14:00:00", "FREQ=DAILY;COUNT=1")
    current = _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00")
    destination = _seed_destination(mock_config, source_id, _resource([recurrence], current, message="Before"))
    source = _resource([recurrence], current, message="After")
    captured = {}

    async def _patch(_path, payload):
        captured["payload"] = deepcopy(payload)
        response = deepcopy(destination)
        response["attributes"]["message"] = "After"
        return {"data": response}

    async def _unexpected_delete(_path):
        pytest.fail("the source recurrence is still active")

    mock_config.destination_client.patch = _patch
    mock_config.destination_client.delete = _unexpected_delete

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime.update_resource(source_id, source))

    assert "schedule" not in captured["payload"]["data"]["attributes"]


@freeze_time("2026-08-11 15:00:00")
def test_update_inactive_exhausted_source_cancels_active_final_destination_occurrence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-11T14:00:00", "FREQ=DAILY;COUNT=1")],
            _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
        ),
    )
    source = _resource(
        [_recurrence("2026-08-10T14:00:00", "FREQ=DAILY;COUNT=1")],
        _current("2026-08-10T14:00:00+00:00", "2026-08-10T16:00:00+00:00"),
    )
    captured = {}

    async def _delete(path):
        captured["path"] = path

    async def _unexpected_patch(*_args, **_kwargs):
        pytest.fail("an active destination must be canceled after the source expires")

    mock_config.destination_client.delete = _delete
    mock_config.destination_client.patch = _unexpected_patch

    _run(downtime.pre_resource_action_hook(source_id, source))
    assert check_diff(
        downtime.resource_config,
        source,
        mock_config.state.destination["downtime_schedules"][source_id],
    )
    _run(downtime._update_resource(source_id, source))

    assert captured["path"] == "/api/v2/downtime/downtime-destination-test"


@freeze_time("2026-08-11 15:58:30")
def test_created_bridge_converges_through_its_final_minute(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    destination = _seed_destination(
        mock_config,
        source_id,
        _resource(
            [
                _recurrence("2026-08-11T15:00:15", "FREQ=DAILY;COUNT=1", duration="59m"),
                _recurrence("2026-08-12T14:00:00"),
            ],
            _current("2026-08-11T15:00:15+00:00", "2026-08-11T15:59:15+00:00"),
        ),
    )
    source = _resource(
        [_recurrence("2026-08-01T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )

    _run(downtime.pre_resource_action_hook(source_id, source))

    assert not check_diff(downtime.resource_config, source, destination)


@freeze_time("2026-08-11 15:00:00")
def test_update_aligned_active_window_patches_original_anchor_for_future_change(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-11T14:00:00")],
            _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
        ),
    )
    source = _resource(
        [_recurrence("2026-08-01T14:00:00", "FREQ=DAILY;INTERVAL=2")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )
    captured = {}

    async def _patch(_path, payload):
        captured["payload"] = deepcopy(payload)
        return {"data": payload["data"]}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime.update_resource(source_id, source))

    assert captured["payload"]["data"]["attributes"]["schedule"]["recurrences"] == [
        _recurrence("2026-08-01T14:00:00", "FREQ=DAILY;INTERVAL=2")
    ]


@freeze_time("2026-08-11 15:01:00")
def test_update_bridge_defers_future_change_until_equivalent_active_window_ends(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    destination = _seed_destination(
        mock_config,
        source_id,
        _resource(
            [
                _recurrence("2026-08-11T15:00:15", "FREQ=DAILY;COUNT=1", duration="59m"),
                _recurrence("2026-08-12T14:00:00"),
            ],
            _current("2026-08-11T15:00:15+00:00", "2026-08-11T15:59:15+00:00"),
            message="Before",
        ),
    )
    source = _resource(
        [_recurrence("2026-08-01T14:00:00", "FREQ=DAILY;INTERVAL=2")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
        message="After",
    )
    captured = {}

    async def _patch(_path, payload):
        captured["payload"] = deepcopy(payload)
        response = deepcopy(destination)
        response["attributes"]["message"] = "After"
        return {"data": response}

    mock_config.destination_client.patch = _patch

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime.update_resource(source_id, source))

    assert "schedule" not in captured["payload"]["data"]["attributes"]
    assert captured["payload"]["data"]["attributes"]["message"] == "After"


@freeze_time("2026-08-11 15:00:00")
def test_update_mismatched_active_window_recreates_bridge_and_future_cadence(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-11T13:00:00")],
            _current("2026-08-11T13:00:00+00:00", "2026-08-11T18:00:00+00:00"),
        ),
    )
    source = _resource(
        [_recurrence("2026-08-11T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )
    captured = {"deleted": [], "posted": []}

    async def _delete(path):
        captured["deleted"].append(path)

    async def _post(path, payload):
        captured["posted"].append((path, deepcopy(payload)))
        return {"data": {"id": "downtime-replacement-test", **deepcopy(payload["data"])}}

    async def _unexpected_patch(*_args, **_kwargs):
        pytest.fail("the API rejects changing the start of an active destination")

    mock_config.destination_client.delete = _delete
    mock_config.destination_client.post = _post
    mock_config.destination_client.patch = _unexpected_patch

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime._update_resource(source_id, source))

    assert captured["deleted"] == ["/api/v2/downtime/downtime-destination-test"]
    assert captured["posted"][0][0] == "/api/v2/downtime"
    assert captured["posted"][0][1]["data"]["attributes"]["schedule"]["recurrences"] == [
        _bridge(),
        _recurrence("2026-08-12T14:00:00"),
    ]
    assert mock_config.state.destination["downtime_schedules"][source_id]["id"] == "downtime-replacement-test"


@freeze_time("2026-08-11 15:00:00")
def test_update_not_found_recreates_active_source_with_bridge(mock_config):
    downtime = DowntimeSchedules(mock_config)
    source_id = "downtime-source-test"
    _seed_destination(
        mock_config,
        source_id,
        _resource(
            [_recurrence("2026-08-11T14:00:00")],
            _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
            message="Before",
        ),
    )
    source = _resource(
        [_recurrence("2026-08-01T14:00:00")],
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
        message="After",
    )
    captured = {}

    async def _patch(_path, _payload):
        raise CustomClientHTTPError(
            SimpleNamespace(status=404, message="not found"),
            message='{"errors":["Downtime not found"]}',
        )

    async def _post(_path, payload):
        captured["payload"] = deepcopy(payload)
        return {"data": {"id": "downtime-replacement-test"}}

    mock_config.destination_client.patch = _patch
    mock_config.destination_client.post = _post

    _run(downtime.pre_resource_action_hook(source_id, source))
    _run(downtime.update_resource(source_id, source))

    assert captured["payload"]["data"]["attributes"]["schedule"]["recurrences"] == [
        _bridge(),
        _recurrence("2026-08-12T14:00:00"),
    ]
    assert "current_downtime" not in captured["payload"]["data"]["attributes"]["schedule"]


@freeze_time("2026-08-11 15:00:00")
def test_create_active_schedule_at_five_future_recurrence_limit_is_skipped(mock_config):
    downtime = DowntimeSchedules(mock_config)
    recurrences = [
        _recurrence("2026-08-04T14:00:00", "FREQ=WEEKLY", duration="2h"),
        _recurrence("2026-08-05T10:00:00", "FREQ=WEEKLY", duration="30m"),
        _recurrence("2026-08-06T11:00:00", "FREQ=WEEKLY", duration="30m"),
        _recurrence("2026-08-07T12:00:00", "FREQ=WEEKLY", duration="30m"),
        _recurrence("2026-08-08T13:00:00", "FREQ=WEEKLY", duration="30m"),
    ]
    resource = _resource(
        recurrences,
        _current("2026-08-11T14:00:00+00:00", "2026-08-11T16:00:00+00:00"),
    )

    with pytest.raises(SkipResource, match="maximum of 5 recurrences"):
        _run(downtime.pre_resource_action_hook("downtime-source-test", resource))
