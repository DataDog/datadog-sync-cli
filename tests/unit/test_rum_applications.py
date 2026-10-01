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
the destination id is absent. Retention quota config (1:1 with application) is
embedded as _retention_quota and synced via a separate endpoint.
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


def _quota(app_id):
    return {
        "id": app_id,
        "type": "rum_quota_config",
        "attributes": {
            "mode": "custom",
            "custom": {"window_type": "daily", "session_limit": 1000000, "quota_reached_action": "stop"},
        },
    }


def test_get_resources_lists_then_gets_each():
    """The list endpoint returns partial resources; get_resources GETs each id
    and returns the whole bodies in order. Retention quota GETs return 404."""
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    client = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications":
            return {"data": [{"id": "a1"}, {"id": "a2"}]}
        elif url == "/api/v2/rum/applications/a1":
            return {"data": _app_resource("a1")}
        elif url == "/api/v2/rum/applications/a2":
            return {"data": _app_resource("a2")}
        elif url.startswith("/api/v2/rum/config/retention-quota/"):
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        raise Exception(f"unexpected URL: {url}")

    client.get = AsyncMock(side_effect=mock_get)

    resources = _run(rum.get_resources(client))

    assert [r["id"] for r in resources] == ["a1", "a2"]
    # First call is the list, then one GET per id, then quota GETs (404 -> None)
    assert client.get.await_count == 5
    assert client.get.await_args_list[0].args[0] == "/api/v2/rum/applications"
    assert client.get.await_args_list[1].args[0] == "/api/v2/rum/applications/a1"
    assert client.get.await_args_list[2].args[0] == "/api/v2/rum/config/retention-quota/application/a1"
    assert client.get.await_args_list[3].args[0] == "/api/v2/rum/applications/a2"
    assert client.get.await_args_list[4].args[0] == "/api/v2/rum/config/retention-quota/application/a2"


def test_get_resources_fetches_retention_quota():
    """When an app has a retention quota, get_resources embeds it as _retention_quota."""
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    client = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications":
            return {"data": [{"id": "a1"}]}
        elif url == "/api/v2/rum/applications/a1":
            return {"data": _app_resource("a1")}
        elif url == "/api/v2/rum/config/retention-quota/application/a1":
            return {"data": _quota("a1")}
        raise Exception(f"unexpected URL: {url}")

    client.get = AsyncMock(side_effect=mock_get)

    resources = _run(rum.get_resources(client))

    assert len(resources) == 1
    assert resources[0]["_retention_quota"]["id"] == "a1"


def test_get_resources_handles_404_for_retention_quota():
    """When an app has no retention quota (404), _retention_quota is absent."""
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    client = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications":
            return {"data": [{"id": "a1"}]}
        elif url == "/api/v2/rum/applications/a1":
            return {"data": _app_resource("a1")}
        elif url == "/api/v2/rum/config/retention-quota/application/a1":
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        raise Exception(f"unexpected URL: {url}")

    client.get = AsyncMock(side_effect=mock_get)

    resources = _run(rum.get_resources(client))

    assert len(resources) == 1
    assert "_retention_quota" not in resources[0]


def test_import_resource_by_id_gets_and_returns_id_and_data():
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    source = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications/a1":
            return {"data": _app_resource("a1")}
        elif url == "/api/v2/rum/config/retention-quota/application/a1":
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        raise Exception(f"unexpected URL: {url}")

    source.get = AsyncMock(side_effect=mock_get)
    rum.config.source_client = source

    _id, data = _run(rum.import_resource(_id="a1"))

    assert _id == "a1"
    assert data["id"] == "a1"
    assert "_retention_quota" not in data


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


def test_create_resource_with_quota_puts_quota_after_create():
    """When the source resource has _retention_quota, create_resource PUTs the
    quota to the retention-quota endpoint after creating the app."""
    rum = RUMApplications(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _app_resource("dst-1", name="rum-app-dst")})
    dest.put = AsyncMock(return_value={"data": _quota("dst-1")})
    rum.config.destination_client = dest

    resource = _app_resource("a1")
    resource["_retention_quota"] = _quota("a1")
    _id, data = _run(rum.create_resource("a1", resource))

    assert _id == "a1"
    # POST for the app, PUT for the quota
    dest.post.assert_awaited_once()
    dest.put.assert_awaited_once()
    put_url, put_payload = dest.put.await_args.args
    assert put_url == "/api/v2/rum/config/retention-quota/application/dst-1"
    assert put_payload["data"]["id"] == "dst-1"
    assert put_payload["data"]["type"] == "rum_quota_config"
    # Runtime fields stripped from quota payload
    assert "org_id" not in put_payload["data"]["attributes"]
    assert "updated_at" not in put_payload["data"]["attributes"]
    assert "updated_by" not in put_payload["data"]["attributes"]


def test_update_resource_patches_when_destination_id_exists():
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    dest = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications":
            return {"data": [{"id": "dst-1"}]}
        elif url == "/api/v2/rum/applications/dst-1":
            return {"data": _app_resource("dst-1", name="rum-app-dst")}
        elif url == "/api/v2/rum/config/retention-quota/application/dst-1":
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        raise Exception(f"unexpected URL: {url}")

    dest.get = AsyncMock(side_effect=mock_get)
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
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    dest = AsyncMock()

    def mock_get(url, **kwargs):
        if url == "/api/v2/rum/applications":
            return {"data": [{"id": "other"}]}
        elif url == "/api/v2/rum/applications/other":
            return {"data": _app_resource("other")}
        elif url.startswith("/api/v2/rum/config/retention-quota/"):
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        raise Exception(f"unexpected URL: {url}")

    dest.get = AsyncMock(side_effect=mock_get)
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


def test_delete_resource_deletes_quota_then_app():
    """delete_resource deletes the retention quota first (ignoring 404), then
    the application."""
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    rum = RUMApplications(MagicMock())
    dest = AsyncMock()

    def mock_delete(url, **kwargs):
        if url.startswith("/api/v2/rum/config/retention-quota/"):
            resp = MagicMock()
            resp.status = 404
            resp.message = "Not Found"
            raise CustomClientHTTPError(resp, message="not found")
        return None

    dest.delete = AsyncMock(side_effect=mock_delete)
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["a1"] = {"id": "dst-1"}

    _run(rum.delete_resource("a1"))

    # Two deletes: quota (404 ignored), then app
    assert dest.delete.await_count == 2
    assert dest.delete.await_args_list[0].args[0] == "/api/v2/rum/config/retention-quota/application/dst-1"
    assert dest.delete.await_args_list[1].args[0] == "/api/v2/rum/applications/dst-1"
