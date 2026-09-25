# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMRetentionFiltersOrder resource model.

RUM retention filter order is per-application and PATCH-only (no GET/DELETE).
The source order is captured from the ordered list returned by
``/api/v2/rum/applications/{app_id}/retention_filters``. The order resource is
keyed by application id; ``id`` (the app id) and ``data[*].id`` (filter ids) are
remapped via ``resource_connections`` before apply. Both must survive
``prep_resource`` (so create/update can read them), so they are excluded from
diffs via ``deep_diff_config.exclude_regex_paths`` rather than
``excluded_attributes``.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_retention_filters_order import RUMRetentionFiltersOrder


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_APPS = {"data": [{"id": "app-src"}, {"id": "app-other"}]}
_APP_FILTERS = {
    "data": [
        {"id": "rf-1", "type": "retention_filters", "attributes": {"name": "a"}},
        {"id": "rf-2", "type": "retention_filters", "attributes": {"name": "b"}},
    ]
}


def test_get_resources_builds_per_app_order_from_list_response():
    order = RUMRetentionFiltersOrder(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[_APPS, _APP_FILTERS, {"data": []}])

    resources = _run(order.get_resources(client))

    assert len(resources) == 2
    app_src_order = [r for r in resources if r["id"] == "app-src"][0]
    assert app_src_order["data"] == [
        {"id": "rf-1", "type": "retention_filters"},
        {"id": "rf-2", "type": "retention_filters"},
    ]
    # app-other has no filters -> empty order list, still emitted
    app_other_order = [r for r in resources if r["id"] == "app-other"][0]
    assert app_other_order["data"] == []


def test_import_resource_passthrough():
    order = RUMRetentionFiltersOrder(MagicMock())
    order.config.source_client = AsyncMock()
    resource = {"id": "app-src", "data": [{"id": "rf-1", "type": "retention_filters"}]}
    _id, data = _run(order.import_resource(resource=resource))
    assert _id == "app-src"
    assert data is resource


def test_create_resource_patches_order_endpoint():
    order = RUMRetentionFiltersOrder(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(return_value={"data": [{"id": "rf-dst", "type": "retention_filters"}]})
    order.config.destination_client = dest

    resource = {"id": "app-dst", "data": [{"id": "rf-dst", "type": "retention_filters"}]}
    _id, data = _run(order.create_resource("app-src", resource))

    assert _id == "app-src"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/applications/app-dst/relationships/retention_filters"
    assert patch_payload == {"data": [{"id": "rf-dst", "type": "retention_filters"}]}


def test_update_resource_patches_order_endpoint():
    order = RUMRetentionFiltersOrder(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(return_value={"data": [{"id": "rf-dst", "type": "retention_filters"}]})
    order.config.destination_client = dest
    order.config.state = MagicMock()
    order.config.state.destination = defaultdict(dict)
    order.config.state.destination["rum_retention_filters_order"]["app-src"] = {"id": "app-dst"}

    resource = {"id": "app-dst", "data": [{"id": "rf-dst", "type": "retention_filters"}]}
    _id, data = _run(order.update_resource("app-src", resource))

    assert _id == "app-src"
    dest.patch.assert_awaited_once()
    assert dest.patch.await_args.args[0] == "/api/v2/rum/applications/app-dst/relationships/retention_filters"


def test_delete_resource_is_noop():
    order = RUMRetentionFiltersOrder(MagicMock())
    order.config.destination_client = AsyncMock()
    order.config.logger = MagicMock()
    _run(order.delete_resource("app-src"))
    order.config.destination_client.delete.assert_not_awaited()


def test_connect_resources_remaps_app_id_and_filter_ids():
    order = RUMRetentionFiltersOrder(MagicMock())
    order.config.state = MagicMock()
    order.config.state.destination = defaultdict(dict)
    order.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    order.config.state.destination["rum_retention_filters"]["rf-1"] = {"id": "rf-dst-1"}
    order.config.state.destination["rum_retention_filters"]["rf-2"] = {"id": "rf-dst-2"}
    order.config.skip_failed_resource_connections = False
    order.config.logger = MagicMock()

    resource = {
        "id": "app-src",
        "data": [
            {"id": "rf-1", "type": "retention_filters"},
            {"id": "rf-2", "type": "retention_filters"},
        ],
    }
    order.connect_resources("app-src", resource)

    assert resource["id"] == "app-dst"
    assert resource["data"] == [
        {"id": "rf-dst-1", "type": "retention_filters"},
        {"id": "rf-dst-2", "type": "retention_filters"},
    ]
