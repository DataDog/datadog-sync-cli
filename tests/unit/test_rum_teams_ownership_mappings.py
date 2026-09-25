# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMTeamsOwnershipMappings resource model.

RUM teams-ownership mappings have no PATCH endpoint, so update is implemented
as delete-then-recreate. ``attributes.application_id`` is a RUM application
uuid remapped via ``resource_connections``. ``attributes.team_handle`` is a
stable handle (teams are mapped by name:handle and the handle is preserved
across orgs), so it is NOT remapped -- the dependency on teams is soft
(documented) rather than a resource_connection (which would mis-remap a handle
as a uuid).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_teams_ownership_mappings import RUMTeamsOwnershipMappings


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _m(_id, app_id="app-src", handle="team-platform"):
    return {
        "id": _id,
        "type": "teams_ownership_mappings",
        "attributes": {
            "application_id": app_id,
            "team_handle": handle,
            "match_type": "exact",
            "service": "web",
            "view_name": "checkout",
        },
    }


def test_get_resources_hits_list_endpoint():
    m = RUMTeamsOwnershipMappings(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": [_m("m-1")]})

    resources = _run(m.get_resources(client))

    assert resources == [_m("m-1")]
    client.get.assert_awaited_once_with("/api/v2/rum/config/teams-ownership/mappings")


def test_import_resource_by_id_gets_and_returns():
    m = RUMTeamsOwnershipMappings(MagicMock())
    source = AsyncMock()
    source.get = AsyncMock(return_value={"data": _m("m-1")})
    m.config.source_client = source

    _id, data = _run(m.import_resource(_id="m-1"))

    assert _id == "m-1"
    source.get.assert_awaited_once_with("/api/v2/rum/config/teams-ownership/mappings/m-1")


def test_import_resource_passthrough():
    m = RUMTeamsOwnershipMappings(MagicMock())
    m.config.source_client = AsyncMock()
    resource = _m("m-1")
    _id, data = _run(m.import_resource(resource=resource))
    assert _id == "m-1"
    assert data is resource


def test_create_resource_posts_without_id():
    m = RUMTeamsOwnershipMappings(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _m("m-dst", app_id="app-dst")})
    m.config.destination_client = dest

    resource = _m("m-1", app_id="app-dst")
    _id, data = _run(m.create_resource("m-1", resource))

    assert _id == "m-1"
    assert data["id"] == "m-dst"
    assert "id" not in resource
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/config/teams-ownership/mappings"
    assert post_payload == {"data": resource}


def test_update_resource_deletes_then_recreates():
    m = RUMTeamsOwnershipMappings(MagicMock())
    dest = AsyncMock()
    dest.delete = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _m("m-dst-new", app_id="app-dst")})
    m.config.destination_client = dest
    m.config.state = MagicMock()
    m.config.state.destination = defaultdict(dict)
    m.config.state.destination["rum_teams_ownership_mappings"]["m-1"] = {"id": "m-dst-old"}

    resource = _m("m-1", app_id="app-dst")
    _id, data = _run(m.update_resource("m-1", resource))

    assert _id == "m-1"
    assert data["id"] == "m-dst-new"
    # delete the old destination mapping, then create a new one
    dest.delete.assert_awaited_once_with("/api/v2/rum/config/teams-ownership/mappings/m-dst-old")
    dest.post.assert_awaited_once()
    assert "id" not in resource


def test_delete_resource_deletes_destination_id():
    m = RUMTeamsOwnershipMappings(MagicMock())
    dest = AsyncMock()
    m.config.destination_client = dest
    m.config.state = MagicMock()
    m.config.state.destination = defaultdict(dict)
    m.config.state.destination["rum_teams_ownership_mappings"]["m-1"] = {"id": "m-dst"}

    _run(m.delete_resource("m-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/config/teams-ownership/mappings/m-dst")


def test_connect_resources_remaps_application_id_only():
    m = RUMTeamsOwnershipMappings(MagicMock())
    m.config.state = MagicMock()
    m.config.state.destination = defaultdict(dict)
    m.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    m.config.skip_failed_resource_connections = False
    m.config.logger = MagicMock()

    resource = _m("m-1", app_id="app-src", handle="team-platform")
    m.connect_resources("m-1", resource)

    assert resource["attributes"]["application_id"] == "app-dst"
    # team_handle is a stable handle, NOT remapped
    assert resource["attributes"]["team_handle"] == "team-platform"
