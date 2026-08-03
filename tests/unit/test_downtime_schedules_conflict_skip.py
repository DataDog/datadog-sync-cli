# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for downtime_schedules apply-time conflict/gone handling.

Two deterministic destination-API rejections must not fail the whole
downtime_schedules resource for a batch:

1. create path -> 400 "... duplicate of one or more existing downtimes ...":
   reconcile an unambiguous existing downtime into destination state; reject
   missing or multiple candidate IDs instead of accepting an unmanaged object.
2. update/delete path -> 404 "Downtime not found": the mapped destination
   downtime was removed out-of-band. Recreate it on update so the stale mapping
   is replaced, and treat delete as an idempotent no-op.

Non-matching 4xx/5xx must still propagate so the retry layer and failure
accounting engage.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.model.downtime_schedules import DowntimeSchedules
from datadog_sync.utils.resource_utils import CustomClientHTTPError

DUPLICATE_BODY = (
    '{"errors":["The downtime being created is a duplicate of one or more '
    "existing downtimes: ['downtime-existing']\"]}"
)
NOT_FOUND_BODY = '{"errors":["Downtime not found"]}'


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _http_error(status, message):
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


def _make_resource():
    return {"attributes": {"schedule": {"start": "2999-01-01T00:00:00Z"}}}


@pytest.fixture
def downtime(mock_config):
    return DowntimeSchedules(mock_config)


# --- create path: duplicate 400 --------------------------------------------


def test_create_duplicate_400_reconciles_single_existing_downtime(downtime):
    downtime.config.destination_client.post = _http_error_raiser(400, DUPLICATE_BODY)

    async def _get(path):
        assert path == "/api/v2/downtime/downtime-existing"
        return {"data": {"id": "downtime-existing", "type": "downtime"}}

    downtime.config.destination_client.get = _get
    _run(downtime._create_resource("src-id", _make_resource()))

    assert downtime.config.state.destination["downtime_schedules"]["src-id"] == {
        "id": "downtime-existing",
        "type": "downtime",
    }


def test_create_duplicate_400_logs_reconciliation_at_info(downtime, caplog):
    downtime.config.destination_client.post = _http_error_raiser(400, DUPLICATE_BODY)

    async def _get(_path):
        return {"data": {"id": "downtime-existing", "type": "downtime"}}

    downtime.config.destination_client.get = _get
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _run(downtime.create_resource("src-id", _make_resource()))
    recs = [r for r in caplog.records if r.name == LOGGER_NAME and "src-id" in r.getMessage()]
    assert recs, "expected an INFO reconciliation log identifying the downtime"
    assert all(r.levelno == logging.INFO for r in recs)


@pytest.mark.parametrize(
    "body",
    [
        '{"errors":["duplicate of one or more existing downtimes"]}',
        '{"errors":["The downtime being created is a duplicate of one or more existing downtimes: '
        "['dest-one', 'dest-two']\"]}",
    ],
)
def test_create_duplicate_without_one_unambiguous_id_propagates(downtime, body):
    downtime.config.destination_client.post = _http_error_raiser(400, body)
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.create_resource("src-id", _make_resource()))
    assert exc.value.status_code == 400


def test_create_non_duplicate_400_propagates(downtime):
    downtime.config.destination_client.post = _http_error_raiser(
        400, '{"errors":["Downtime cannot be scheduled in the past"]}'
    )
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.create_resource("src-id", _make_resource()))
    assert exc.value.status_code == 400


def test_create_500_propagates(downtime):
    downtime.config.destination_client.post = _http_error_raiser(500, "Internal Server Error")
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.create_resource("src-id", _make_resource()))
    assert exc.value.status_code == 500


def test_create_200_unchanged(downtime):
    async def _ok(_path, _payload):
        return {"data": {"id": "dest-id", "type": "downtime"}}

    downtime.config.destination_client.post = _ok
    _id, data = _run(downtime.create_resource("src-id", _make_resource()))
    assert _id == "src-id"
    assert data == {"id": "dest-id", "type": "downtime"}


# --- update path: not-found 404 --------------------------------------------


def _seed_dest(downtime, _id="src-id", dest_id="dest-id"):
    downtime.config.state.destination["downtime_schedules"][_id] = {"id": dest_id}


def test_update_not_found_404_recreates_and_replaces_stale_mapping(downtime):
    _seed_dest(downtime)
    downtime.config.destination_client.patch = _http_error_raiser(404, NOT_FOUND_BODY)
    posted = []

    async def _post(_path, payload):
        posted.append(payload)
        return {"data": {"id": "replacement-id", "type": "downtime"}}

    downtime.config.destination_client.post = _post
    _run(downtime._update_resource("src-id", _make_resource()))

    assert posted[0]["data"].get("id") is None
    assert downtime.config.state.destination["downtime_schedules"]["src-id"] == {
        "id": "replacement-id",
        "type": "downtime",
    }


def test_update_not_found_404_logs_recreation_at_info(downtime, caplog):
    _seed_dest(downtime)
    downtime.config.destination_client.patch = _http_error_raiser(404, NOT_FOUND_BODY)

    async def _post(_path, _payload):
        return {"data": {"id": "replacement-id", "type": "downtime"}}

    downtime.config.destination_client.post = _post
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _run(downtime.update_resource("src-id", _make_resource()))
    recs = [r for r in caplog.records if r.name == LOGGER_NAME and "src-id" in r.getMessage()]
    assert recs and all(r.levelno == logging.INFO for r in recs)


def test_update_unrelated_404_propagates(downtime):
    _seed_dest(downtime)
    downtime.config.destination_client.patch = _http_error_raiser(404, '{"errors":["Route not found"]}')
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.update_resource("src-id", _make_resource()))
    assert exc.value.status_code == 404


def test_update_400_propagates(downtime):
    _seed_dest(downtime)
    downtime.config.destination_client.patch = _http_error_raiser(400, "Bad Request")
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.update_resource("src-id", _make_resource()))
    assert exc.value.status_code == 400


def test_update_200_unchanged(downtime):
    _seed_dest(downtime)

    async def _ok(_path, _payload):
        return {"data": {"id": "dest-id", "type": "downtime"}}

    downtime.config.destination_client.patch = _ok
    _id, data = _run(downtime.update_resource("src-id", _make_resource()))
    assert _id == "src-id"
    assert data == {"id": "dest-id", "type": "downtime"}


# --- delete path: not-found 404 is a no-op ---------------------------------


def test_delete_404_is_noop(downtime):
    _seed_dest(downtime)
    downtime.config.destination_client.delete = _http_error_raiser(404, NOT_FOUND_BODY)
    # Already gone == delete succeeded; must not raise.
    assert _run(downtime.delete_resource("src-id")) is None


def test_delete_500_propagates(downtime):
    _seed_dest(downtime)
    downtime.config.destination_client.delete = _http_error_raiser(500, "Internal Server Error")
    with pytest.raises(CustomClientHTTPError) as exc:
        _run(downtime.delete_resource("src-id"))
    assert exc.value.status_code == 500


# --- batch resilience: one duplicate doesn't stop siblings ------------------


def test_duplicate_does_not_block_siblings(downtime):
    calls = {"n": 0}

    async def _post(_path, _payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise _http_error(400, DUPLICATE_BODY)
        return {"data": {"id": f"dest-{calls['n']}"}}

    async def _get(_path):
        return {"data": {"id": "downtime-existing"}}

    downtime.config.destination_client.post = _post
    downtime.config.destination_client.get = _get
    synced = []
    for src in ("a", "b", "c"):
        _id, _ = _run(downtime.create_resource(src, _make_resource()))
        synced.append(_id)
    assert synced == ["a", "b", "c"]


def _http_error_raiser(status, message):
    async def _raise(*_args, **_kwargs):
        raise _http_error(status, message)

    return _raise
