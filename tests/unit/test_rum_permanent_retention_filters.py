# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMPermanentRetentionFilters resource model.

Permanent RUM retention filters are system-defined with fixed ids
(``rum_apm_flat_sampling``, ``synthetics_sessions``, ``forced_replay_sessions``)
that are identical across orgs, so the filter id needs no remapping — only the
parent application id does. The endpoint set is PATCH-only (no POST/DELETE), so
``create_resource`` delegates to ``update_resource`` and ``delete_resource`` is
a no-op, mirroring ``logs_archives_order``.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_permanent_retention_filters import RUMPermanentRetentionFilters


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_APPS = {"data": [{"id": "app-src"}]}
_PERM = {
    "data": [
        {
            "id": "synthetics_sessions",
            "type": "permanent_retention_filters",
            "attributes": {
                "name": "Synthetics Sessions",
                "description": "system",
                "editability": "editable",
                "cross_product_sampling": {"enabled": False, "sample_rate": 1.0},
            },
        }
    ]
}


def test_get_resources_iterates_apps_and_injects_application_id():
    rum = RUMPermanentRetentionFilters(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[_APPS, _PERM])

    resources = _run(rum.get_resources(client))

    assert len(resources) == 1
    assert resources[0]["id"] == "synthetics_sessions"
    assert resources[0]["_application_id"] == "app-src"
    assert client.get.await_count == 2


def test_import_resource_passthrough():
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.source_client = AsyncMock()
    resource = _PERM["data"][0] | {"_application_id": "app-src"}
    _id, data = _run(rum.import_resource(resource=resource))
    assert _id == "synthetics_sessions"
    assert data is resource


def test_create_resource_delegates_to_update():
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "synthetics_sessions", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_permanent_retention_filters"]["synthetics_sessions"] = {
        "id": "synthetics_sessions",
        "_application_id": "app-dst",
    }

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {"cross_product_sampling": {"enabled": True, "sample_rate": 0.5}},
        "_application_id": "app-dst",
    }
    _id, data = _run(rum.create_resource("synthetics_sessions", resource))

    # create delegates to update (no POST endpoint)
    dest.patch.assert_awaited_once()
    assert (
        dest.patch.await_args.args[0]
        == "/api/v2/rum/applications/app-dst/retention_filters/permanent/synthetics_sessions"
    )


def test_update_resource_patches_permanent_subpath():
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "synthetics_sessions", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_permanent_retention_filters"]["synthetics_sessions"] = {
        "id": "synthetics_sessions",
        "_application_id": "app-dst",
    }

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {"cross_product_sampling": {"enabled": True, "sample_rate": 0.5}},
        "_application_id": "app-dst",
    }
    _id, data = _run(rum.update_resource("synthetics_sessions", resource))

    assert _id == "synthetics_sessions"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/applications/app-dst/retention_filters/permanent/synthetics_sessions"
    assert patch_payload["data"]["id"] == "synthetics_sessions"
    assert patch_payload["data"]["type"] == "permanent_retention_filters"


def test_delete_resource_is_noop():
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.destination_client = AsyncMock()
    rum.config.logger = MagicMock()
    _run(rum.delete_resource("synthetics_sessions"))
    rum.config.destination_client.delete.assert_not_awaited()


def test_connect_resources_remaps_application_id():
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    rum.config.skip_failed_resource_connections = False
    rum.config.logger = MagicMock()

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {"cross_product_sampling": {"enabled": True, "sample_rate": 0.5}},
        "_application_id": "app-src",
    }
    rum.connect_resources("synthetics_sessions", resource)
    assert resource["_application_id"] == "app-dst"
