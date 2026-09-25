# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMPermanentRetentionFilters resource model.

Permanent RUM retention filters are system-defined with fixed ids
(``rum_apm_flat_sampling``, ``synthetics_sessions``, ``forced_replay_sessions``)
that are identical across orgs, so the filter id needs no remapping — only the
parent application id does. Because the filter ids are fixed and repeat under
every application, the state key is a **composite**
``"{application_id}:{filter_id}"`` to avoid collisions when multiple
applications are synced. The endpoint set is PATCH-only (no POST/DELETE), so
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


_APPS = {"data": [{"id": "app-src"}, {"id": "app-other"}]}
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
    import copy

    rum = RUMPermanentRetentionFilters(MagicMock())
    client = AsyncMock()
    # Two apps, each returning the same permanent filter (separate copies so
    # _application_id injection doesn't overwrite the same dict)
    client.get = AsyncMock(side_effect=[_APPS, copy.deepcopy(_PERM), copy.deepcopy(_PERM)])

    resources = _run(rum.get_resources(client))

    # Both apps have the same filter id, but different _application_id
    assert len(resources) == 2
    assert resources[0]["id"] == "synthetics_sessions"
    assert resources[0]["_application_id"] == "app-src"
    assert resources[1]["id"] == "synthetics_sessions"
    assert resources[1]["_application_id"] == "app-other"


def test_import_resource_uses_composite_key():
    """import_resource returns a composite key '{app_id}:{filter_id}' to avoid
    collisions when multiple apps have the same fixed filter id."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.source_client = AsyncMock()
    resource = _PERM["data"][0] | {"_application_id": "app-src"}
    _id, data = _run(rum.import_resource(resource=resource))
    assert _id == "app-src:synthetics_sessions"
    assert data is resource


def test_import_resource_composite_key_distinguishes_apps():
    """Two resources with the same filter id but different app ids produce
    different composite keys."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.source_client = AsyncMock()

    r1 = _PERM["data"][0] | {"_application_id": "app-a"}
    r2 = _PERM["data"][0] | {"_application_id": "app-b"}

    id1, _ = _run(rum.import_resource(resource=r1))
    id2, _ = _run(rum.import_resource(resource=r2))

    assert id1 != id2
    assert id1 == "app-a:synthetics_sessions"
    assert id2 == "app-b:synthetics_sessions"


def test_create_resource_delegates_to_update():
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "synthetics_sessions", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {"cross_product_sampling": {"enabled": True, "sample_rate": 0.5}},
        "_application_id": "app-dst",
    }
    composite = "app-src:synthetics_sessions"
    _id, data = _run(rum.create_resource(composite, resource))

    # create delegates to update (no POST endpoint)
    dest.patch.assert_awaited_once()
    assert (
        dest.patch.await_args.args[0]
        == "/api/v2/rum/applications/app-dst/retention_filters/permanent/synthetics_sessions"
    )
    assert _id == composite


def test_update_resource_patches_permanent_subpath():
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "synthetics_sessions", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {"cross_product_sampling": {"enabled": True, "sample_rate": 0.5}},
        "_application_id": "app-dst",
    }
    composite = "app-src:synthetics_sessions"
    _id, data = _run(rum.update_resource(composite, resource))

    assert _id == composite
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/applications/app-dst/retention_filters/permanent/synthetics_sessions"
    assert patch_payload["data"]["id"] == "synthetics_sessions"
    assert patch_payload["data"]["type"] == "permanent_retention_filters"


def test_delete_resource_is_noop():
    rum = RUMPermanentRetentionFilters(MagicMock())
    rum.config.destination_client = AsyncMock()
    rum.config.logger = MagicMock()
    _run(rum.delete_resource("app-src:synthetics_sessions"))
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
    rum.connect_resources("app-src:synthetics_sessions", resource)
    assert resource["_application_id"] == "app-dst"
