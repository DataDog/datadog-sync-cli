# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMOperations resource model.

A RUM operation is a static definition (name, display_name, category, query,
journey rules) tied to a RUM application via ``attributes.application_id`` (a
real body field, remapped to the destination app id via ``resource_connections``).
The ``query`` fields in the journey are RUM query filters (e.g. ``@type:view``),
not session ids, so no session-id stripping is required. List is via the
``/rum/operations/search`` GET endpoint.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_operations import RUMOperations


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _op(_id, app_id="app-src", name="checkout-flow"):
    return {
        "id": _id,
        "type": "rum_operations",
        "attributes": {
            "application_id": app_id,
            "name": name,
            "display_name": "Checkout Flow",
            "category": "ux",
            "description": "checkout journey",
            "tags": ["env:prod"],
            "feature_ids": [],
            "journey_rum": {"rum_steps": []},
        },
    }


def test_get_resources_hits_search_endpoint():
    ops = RUMOperations(MagicMock())
    client = AsyncMock()
    # paginated_request(func) returns a wrapper coroutine; mock the wrapper
    wrapper_mock = AsyncMock(return_value=[_op("op-1"), _op("op-2")])
    client.paginated_request = MagicMock(return_value=wrapper_mock)

    resources = _run(ops.get_resources(client))

    assert resources == [_op("op-1"), _op("op-2")]
    # Verify paginated_request was called with client.get
    client.paginated_request.assert_called_once_with(client.get)
    # Verify the wrapper was called with the search path and pagination config
    wrapper_mock.assert_called_once_with(
        "/api/v2/rum/operations/search",
        pagination_config=ops.pagination_config,
    )


def test_import_resource_by_id_gets_and_returns():
    ops = RUMOperations(MagicMock())
    source = AsyncMock()
    source.get = AsyncMock(return_value={"data": _op("op-1")})
    ops.config.source_client = source

    _id, data = _run(ops.import_resource(_id="op-1"))

    assert _id == "op-1"
    source.get.assert_awaited_once_with("/api/v2/rum/operations/op-1")


def test_import_resource_passthrough():
    ops = RUMOperations(MagicMock())
    ops.config.source_client = AsyncMock()
    resource = _op("op-1")
    _id, data = _run(ops.import_resource(resource=resource))
    assert _id == "op-1"
    assert data is resource
    ops.config.source_client.get.assert_not_awaited()


def test_create_resource_posts_without_id():
    ops = RUMOperations(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _op("op-dst", app_id="app-dst")})
    ops.config.destination_client = dest

    resource = _op("op-1", app_id="app-dst")
    _id, data = _run(ops.create_resource("op-1", resource))

    assert _id == "op-1"
    assert data["id"] == "op-dst"
    # create data has no id (server-assigned); it must be popped before POST
    assert "id" not in resource
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/operations"
    assert post_payload == {"data": resource}


def test_update_resource_puts_destination_id():
    ops = RUMOperations(MagicMock())
    dest = AsyncMock()
    dest.put = AsyncMock(return_value={"data": _op("op-dst", app_id="app-dst", name="updated")})
    ops.config.destination_client = dest
    ops.config.state = MagicMock()
    ops.config.state.destination = defaultdict(dict)
    ops.config.state.destination["rum_operations"]["op-1"] = {"id": "op-dst"}

    resource = _op("op-1", app_id="app-dst", name="updated")
    _id, data = _run(ops.update_resource("op-1", resource))

    assert _id == "op-1"
    assert resource["id"] == "op-dst"
    dest.put.assert_awaited_once()
    put_url, put_payload = dest.put.await_args.args
    assert put_url == "/api/v2/rum/operations/op-dst"
    assert put_payload == {"data": resource}


def test_delete_resource_deletes_destination_id():
    ops = RUMOperations(MagicMock())
    dest = AsyncMock()
    ops.config.destination_client = dest
    ops.config.state = MagicMock()
    ops.config.state.destination = defaultdict(dict)
    ops.config.state.destination["rum_operations"]["op-1"] = {"id": "op-dst"}

    _run(ops.delete_resource("op-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/operations/op-dst")


def test_connect_resources_remaps_application_id():
    ops = RUMOperations(MagicMock())
    ops.config.state = MagicMock()
    ops.config.state.destination = defaultdict(dict)
    ops.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    ops.config.skip_failed_resource_connections = False
    ops.config.logger = MagicMock()

    resource = _op("op-1", app_id="app-src")
    ops.connect_resources("op-1", resource)

    assert resource["attributes"]["application_id"] == "app-dst"
