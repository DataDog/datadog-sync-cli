# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMOperationStrongLinks resource model.

Strong links are keyed by the composite (operation_id, feature_id). The create
payload requires ``application_id`` and ``operation_name`` which are NOT in the
response, so ``pre_resource_action_hook`` derives them from the parent source
operation (it runs before ``connect_resources`` remaps ``operation_id``). Only
``status`` is updatable, so update sends only status and the composite key is
read from state.destination.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.rum_operation_strong_links import RUMOperationStrongLinks


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sl(_id, op_id="op-src", feature_id="feat-1", status="enabled"):
    return {
        "id": _id,
        "type": "rum_operation_strong_links",
        "attributes": {
            "operation_id": op_id,
            "feature_id": feature_id,
            "status": status,
            "description": "link desc",
            "tags": ["env:prod"],
        },
    }


def test_get_resources_hits_list_endpoint():
    sl = RUMOperationStrongLinks(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": [_sl("sl-1")], "meta": {}})

    resources = _run(sl.get_resources(client))

    assert resources == [_sl("sl-1")]
    client.get.assert_awaited_once_with("/api/v2/rum/operations/strong_links")


def test_import_resource_by_id_passthrough():
    sl = RUMOperationStrongLinks(MagicMock())
    sl.config.source_client = AsyncMock()
    resource = _sl("sl-1")
    _id, data = _run(sl.import_resource(resource=resource))
    assert _id == "sl-1"
    assert data is resource


def test_pre_resource_action_hook_derives_application_id_and_operation_name():
    sl = RUMOperationStrongLinks(MagicMock())
    sl.config.state = MagicMock()
    sl.config.state.source = defaultdict(dict)
    sl.config.state.source["rum_operations"] = {
        "op-src": {
            "id": "op-src",
            "attributes": {"application_id": "app-src", "name": "checkout-flow"},
        }
    }

    resource = _sl("sl-1", op_id="op-src")
    _run(sl.pre_resource_action_hook("sl-1", resource))

    assert resource["attributes"]["application_id"] == "app-src"
    assert resource["attributes"]["operation_name"] == "checkout-flow"


def test_pre_resource_action_hook_raises_skip_when_operation_not_found():
    """When the parent operation is missing from source state, raise
    SkipResource so the operator gets an explicit error instead of a
    silent no-op that produces an incomplete create payload."""
    from datadog_sync.utils.resource_utils import SkipResource

    sl = RUMOperationStrongLinks(MagicMock())
    sl.config.state = MagicMock()
    sl.config.state.source = defaultdict(dict)
    sl.config.state.source["rum_operations"] = {}

    resource = _sl("sl-1", op_id="op-missing")
    with pytest.raises(SkipResource):
        _run(sl.pre_resource_action_hook("sl-1", resource))


def test_pre_resource_action_hook_raises_skip_when_operation_id_missing():
    """When the resource has no operation_id at all, raise SkipResource."""
    from datadog_sync.utils.resource_utils import SkipResource

    sl = RUMOperationStrongLinks(MagicMock())
    sl.config.state = MagicMock()
    sl.config.state.source = defaultdict(dict)

    resource = _sl("sl-1", op_id="op-src")
    resource["attributes"].pop("operation_id")
    with pytest.raises(SkipResource):
        _run(sl.pre_resource_action_hook("sl-1", resource))


def test_create_resource_posts_with_derived_fields():
    sl = RUMOperationStrongLinks(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(return_value={"data": _sl("sl-dst", op_id="op-dst")})
    sl.config.destination_client = dest

    resource = _sl("sl-1", op_id="op-dst")
    resource["attributes"]["application_id"] = "app-dst"
    resource["attributes"]["operation_name"] = "checkout-flow"
    _id, data = _run(sl.create_resource("sl-1", resource))

    assert _id == "sl-1"
    assert data["id"] == "sl-dst"
    assert "id" not in resource
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/operations/strong_links"
    assert post_payload == {"data": resource}


def test_update_resource_puts_composite_key_and_status_only():
    sl = RUMOperationStrongLinks(MagicMock())
    dest = AsyncMock()
    dest.put = AsyncMock(return_value={"data": _sl("sl-dst", op_id="op-dst", status="disabled")})
    sl.config.destination_client = dest
    sl.config.state = MagicMock()
    sl.config.state.destination = defaultdict(dict)
    sl.config.state.destination["rum_operation_strong_links"]["sl-1"] = {
        "id": "sl-dst",
        "attributes": {"operation_id": "op-dst", "feature_id": "feat-1", "status": "enabled"},
    }

    resource = _sl("sl-1", op_id="op-dst", status="disabled")
    _id, data = _run(sl.update_resource("sl-1", resource))

    assert _id == "sl-1"
    dest.put.assert_awaited_once()
    put_url, put_payload = dest.put.await_args.args
    assert put_url == "/api/v2/rum/operations/strong_links/op-dst/feat-1"
    # update sends only the updatable field (status)
    assert put_payload == {"data": {"type": "rum_operation_strong_links", "attributes": {"status": "disabled"}}}


def test_delete_resource_deletes_composite_key():
    sl = RUMOperationStrongLinks(MagicMock())
    dest = AsyncMock()
    sl.config.destination_client = dest
    sl.config.state = MagicMock()
    sl.config.state.destination = defaultdict(dict)
    sl.config.state.destination["rum_operation_strong_links"]["sl-1"] = {
        "id": "sl-dst",
        "attributes": {"operation_id": "op-dst", "feature_id": "feat-1"},
    }

    _run(sl.delete_resource("sl-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/operations/strong_links/op-dst/feat-1")


def test_connect_resources_remaps_operation_id():
    sl = RUMOperationStrongLinks(MagicMock())
    sl.config.state = MagicMock()
    sl.config.state.destination = defaultdict(dict)
    sl.config.state.destination["rum_operations"]["op-src"] = {"id": "op-dst"}
    sl.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    sl.config.skip_failed_resource_connections = False
    sl.config.logger = MagicMock()

    resource = _sl("sl-1", op_id="op-src")
    resource["attributes"]["application_id"] = "app-src"
    sl.connect_resources("sl-1", resource)

    assert resource["attributes"]["operation_id"] == "op-dst"
    assert resource["attributes"]["application_id"] == "app-dst"
