# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the existing RUMApplications resource model.

Pins the documented behavior of rum_applications so the upcoming RUM resource
PRs can rely on it as a dependency without regressing the parent. The list
endpoint returns partial resources, so get_resources follows list-then-GET-each;
update_resource re-fetches the destination list and falls back to create when
the destination id is absent.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_applications import RUMApplications


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests. See other test files in this suite for the pattern.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _app_resource(_id, name="rum-app-src"):
    return {
        "id": _id,
        "type": "rum_application",
        "attributes": {"name": name},
    }


def test_get_resources_lists_then_gets_each():
    """The list endpoint returns partial resources; get_resources GETs each id
    and returns the whole bodies in order."""
    rum = RUMApplications(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            {"data": [{"id": "a1"}, {"id": "a2"}]},
            {"data": _app_resource("a1")},
            {"data": _app_resource("a2")},
        ]
    )

    resources = _run(rum.get_resources(client))

    assert [r["id"] for r in resources] == ["a1", "a2"]
    # First call is the list, then one GET per id.
    assert client.get.await_count == 3
    assert client.get.await_args_list[0].args[0] == "/api/v2/rum/applications"
    assert client.get.await_args_list[1].args[0] == "/api/v2/rum/applications/a1"
    assert client.get.await_args_list[2].args[0] == "/api/v2/rum/applications/a2"


def test_import_resource_by_id_gets_and_returns_id_and_data():
    rum = RUMApplications(MagicMock())
    source = AsyncMock()
    source.get = AsyncMock(return_value={"data": _app_resource("a1")})
    rum.config.source_client = source

    _id, data = _run(rum.import_resource(_id="a1"))

    assert _id == "a1"
    assert data["id"] == "a1"
    source.get.assert_awaited_once_with("/api/v2/rum/applications/a1")


def test_import_resource_passthrough_when_resource_supplied():
    """When a full resource is supplied (no _id), no GET is performed."""
    rum = RUMApplications(MagicMock())
    rum.config.source_client = AsyncMock()

    resource = _app_resource("a1")
    _id, data = _run(rum.import_resource(resource=resource))

    assert _id == "a1"
    assert data is resource
    rum.config.source_client.get.assert_not_awaited()


def test_create_resource_sets_create_type_and_posts():
    rum = RUMApplications(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _app_resource("dst-1", name="rum-app-dst")})
    rum.config.destination_client = dest

    resource = _app_resource("a1")
    _id, data = _run(rum.create_resource("a1", resource))

    assert _id == "a1"
    assert data["id"] == "dst-1"
    # create mutates the type to the create-specific type and wraps in {"data": ...}
    assert resource["type"] == "rum_application_create"
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/applications"
    assert post_payload == {"data": resource}


def test_update_resource_patches_when_destination_id_exists():
    rum = RUMApplications(MagicMock())
    dest = AsyncMock()
    # get_resources (list + 1 GET) then the PATCH
    dest.get = AsyncMock(
        side_effect=[
            {"data": [{"id": "dst-1"}]},
            {"data": _app_resource("dst-1", name="rum-app-dst")},
        ]
    )
    dest.patch = AsyncMock(return_value={"data": _app_resource("dst-1", name="rum-app-dst-updated")})
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["a1"] = {"id": "dst-1"}

    resource = _app_resource("a1")
    _id, data = _run(rum.update_resource("a1", resource))

    assert _id == "a1"
    assert data["id"] == "dst-1"
    # update mutates the type to the update-specific type and rewrites the id.
    assert resource["type"] == "rum_application_update"
    assert resource["id"] == "dst-1"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/applications/dst-1"
    assert patch_payload == {"data": resource}


def test_update_resource_falls_back_to_create_when_destination_id_absent():
    """When the destination id is not present in a live re-fetch, update_resource
    delegates to create_resource instead of PATCHing a stale id."""
    rum = RUMApplications(MagicMock())
    dest = AsyncMock()
    # list returns a different app; the configured destination id is not among them
    dest.get = AsyncMock(
        side_effect=[
            {"data": [{"id": "other"}]},
            {"data": _app_resource("other")},
        ]
    )
    dest.post = AsyncMock(return_value={"data": _app_resource("new-dst")})
    dest.patch = AsyncMock()
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["a1"] = {"id": "stale-dst"}

    resource = _app_resource("a1")
    _id, data = _run(rum.update_resource("a1", resource))

    assert _id == "a1"
    assert data["id"] == "new-dst"
    dest.post.assert_awaited_once()
    dest.patch.assert_not_awaited()
    assert resource["type"] == "rum_application_create"


def test_delete_resource_deletes_destination_id():
    rum = RUMApplications(MagicMock())
    dest = AsyncMock()
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["a1"] = {"id": "dst-1"}

    _run(rum.delete_resource("a1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/applications/dst-1")
