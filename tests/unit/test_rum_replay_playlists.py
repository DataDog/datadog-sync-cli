# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMReplayPlaylists resource model.

A RUM replay playlist is a static shell definition (name, description). The
playlist body itself contains no session-id references; the playlist->session
associations live on the ``.../sessions`` sub-resources, which are intake-tied
and out of scope. So this model syncs the playlist shell only (full CRUD) and
does not call the ``.../sessions`` endpoints.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_replay_playlists import RUMReplayPlaylists


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _pl(_id, name="prod-replay"):
    return {
        "id": _id,
        "type": "replay_playlists",
        "attributes": {
            "name": name,
            "description": "prod replay playlist",
        },
    }


def test_get_resources_hits_list_endpoint():
    pl = RUMReplayPlaylists(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": [_pl("pl-1")]})

    resources = _run(pl.get_resources(client))

    assert resources == [_pl("pl-1")]
    client.get.assert_awaited_once_with("/api/v2/rum/replay/playlists")


def test_import_resource_by_id_gets_and_returns():
    pl = RUMReplayPlaylists(MagicMock())
    source = AsyncMock()
    source.get = AsyncMock(return_value={"data": _pl("pl-1")})
    pl.config.source_client = source

    _id, data = _run(pl.import_resource(_id="pl-1"))

    assert _id == "pl-1"
    source.get.assert_awaited_once_with("/api/v2/rum/replay/playlists/pl-1")


def test_import_resource_passthrough():
    pl = RUMReplayPlaylists(MagicMock())
    pl.config.source_client = AsyncMock()
    resource = _pl("pl-1")
    _id, data = _run(pl.import_resource(resource=resource))
    assert _id == "pl-1"
    assert data is resource


def test_create_resource_posts_without_id():
    pl = RUMReplayPlaylists(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _pl("pl-dst")})
    pl.config.destination_client = dest

    resource = _pl("pl-1")
    _id, data = _run(pl.create_resource("pl-1", resource))

    assert _id == "pl-1"
    assert data["id"] == "pl-dst"
    assert "id" not in resource
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/replay/playlists"
    assert post_payload == {"data": resource}


def test_update_resource_puts_destination_id():
    pl = RUMReplayPlaylists(MagicMock())
    dest = AsyncMock()
    dest.put = AsyncMock(return_value={"data": _pl("pl-dst", name="updated")})
    pl.config.destination_client = dest
    pl.config.state = MagicMock()
    pl.config.state.destination = defaultdict(dict)
    pl.config.state.destination["rum_replay_playlists"]["pl-1"] = {"id": "pl-dst"}

    resource = _pl("pl-1", name="updated")
    _id, data = _run(pl.update_resource("pl-1", resource))

    assert _id == "pl-1"
    assert resource["id"] == "pl-dst"
    dest.put.assert_awaited_once()
    put_url, put_payload = dest.put.await_args.args
    assert put_url == "/api/v2/rum/replay/playlists/pl-dst"
    assert put_payload == {"data": resource}


def test_delete_resource_deletes_destination_id():
    pl = RUMReplayPlaylists(MagicMock())
    dest = AsyncMock()
    pl.config.destination_client = dest
    pl.config.state = MagicMock()
    pl.config.state.destination = defaultdict(dict)
    pl.config.state.destination["rum_replay_playlists"]["pl-1"] = {"id": "pl-dst"}

    _run(pl.delete_resource("pl-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/replay/playlists/pl-dst")
