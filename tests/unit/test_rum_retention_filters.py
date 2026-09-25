# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMRetentionFilters resource model.

RUM retention filters are parent-scoped under a RUM application
(``/api/v2/rum/applications/{app_id}/retention_filters``). The application id is
not part of the filter body, so the model injects a synthetic ``_application_id``
during enumeration and remaps it (source app id -> destination app id) via
``resource_connections`` before apply. ``prep_resource`` runs after
``connect_resources`` and removes ``excluded_attributes``, so ``_application_id``
is kept out of ``excluded_attributes`` (it must survive prep so create/update can
read it) and is instead excluded from diffs via ``deep_diff_config``.

The model unifies the generic ``retention_filters`` type and the
``exclusion_filters`` type, which live on separate sub-paths
(``/retention_filters`` vs ``/retention_filters/exclusion``) and are
distinguished by ``data.type``.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_retention_filters import RUMRetentionFilters


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_APPS = {"data": [{"id": "app-src"}, {"id": "app-other"}]}

_RETENTION = {
    "data": [
        {
            "id": "rf-1",
            "type": "retention_filters",
            "attributes": {"name": "keep-views", "query": "@type:view", "enabled": True, "sample_rate": 1.0},
        }
    ]
}

_EXCLUSION = {
    "data": [
        {
            "id": "ef-1",
            "type": "exclusion_filters",
            "attributes": {"name": "drop-errors", "query": "@type:error", "enabled": True},
            "meta": {},
        }
    ]
}


def _client_with_apps_and_filters():
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            _APPS,  # list apps
            _RETENTION,  # app-src retention filters
            _EXCLUSION,  # app-src exclusion filters
            {"data": []},  # app-other retention filters
            {"data": []},  # app-other exclusion filters
        ]
    )
    return client


def test_get_resources_iterates_apps_and_merges_retention_and_exclusion():
    rum = RUMRetentionFilters(MagicMock())
    client = _client_with_apps_and_filters()

    resources = _run(rum.get_resources(client))

    ids = [(r["id"], r["type"], r["_application_id"]) for r in resources]
    assert ("rf-1", "retention_filters", "app-src") in ids
    assert ("ef-1", "exclusion_filters", "app-src") in ids
    # 5 GETs: 1 apps list + 2 per app (retention + exclusion) for 2 apps
    assert client.get.await_count == 5


def test_import_resource_passthrough_when_resource_supplied():
    rum = RUMRetentionFilters(MagicMock())
    rum.config.source_client = AsyncMock()

    resource = {
        "id": "rf-1",
        "type": "retention_filters",
        "attributes": {"name": "keep-views"},
        "_application_id": "app-src",
    }
    _id, data = _run(rum.import_resource(resource=resource))

    assert _id == "rf-1"
    assert data is resource
    rum.config.source_client.get.assert_not_awaited()


def test_create_resource_retention_type_posts_to_generic_path():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(
        return_value={"data": {"id": "rf-dst", "type": "retention_filters", "attributes": {"name": "keep-views"}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "rf-1",
        "type": "retention_filters",
        "attributes": {"name": "keep-views"},
        "_application_id": "app-dst",
    }
    _id, data = _run(rum.create_resource("rf-1", resource))

    assert _id == "rf-1"
    assert data["id"] == "rf-dst"
    # _application_id is re-attached to the response so state can build future URLs
    assert data["_application_id"] == "app-dst"
    # create data has no id; it must be popped before POST
    assert "id" not in resource
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/applications/app-dst/retention_filters"
    assert post_payload == {"data": {"type": "retention_filters", "attributes": {"name": "keep-views"}}}


def test_create_resource_exclusion_type_posts_to_exclusion_subpath():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.post = AsyncMock(
        return_value={"data": {"id": "ef-dst", "type": "exclusion_filters", "attributes": {"name": "drop-errors"}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "ef-1",
        "type": "exclusion_filters",
        "attributes": {"name": "drop-errors"},
        "_application_id": "app-dst",
    }
    _id, data = _run(rum.create_resource("ef-1", resource))

    assert data["id"] == "ef-dst"
    assert data["_application_id"] == "app-dst"
    post_url = dest.post.await_args.args[0]
    assert post_url == "/api/v2/rum/applications/app-dst/retention_filters/exclusion"


def test_update_resource_patches_destination_id_on_correct_subpath():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "rf-dst", "type": "retention_filters", "attributes": {"name": "keep-views"}}}
    )
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_retention_filters"]["rf-1"] = {
        "id": "rf-dst",
        "_application_id": "app-dst",
    }

    resource = {
        "id": "rf-1",
        "type": "retention_filters",
        "attributes": {"name": "keep-views-updated"},
        "_application_id": "app-dst",
    }
    _id, data = _run(rum.update_resource("rf-1", resource))

    assert _id == "rf-1"
    # update sets the body id to the destination id and uses the destination app id in the URL
    assert resource["id"] == "rf-dst"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/applications/app-dst/retention_filters/rf-dst"
    assert patch_payload == {
        "data": {"type": "retention_filters", "attributes": {"name": "keep-views-updated"}, "id": "rf-dst"}
    }


def test_update_resource_exclusion_uses_exclusion_subpath():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "ef-dst", "type": "exclusion_filters", "attributes": {"name": "drop-errors"}}}
    )
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_retention_filters"]["ef-1"] = {
        "id": "ef-dst",
        "_application_id": "app-dst",
    }

    resource = {
        "id": "ef-1",
        "type": "exclusion_filters",
        "attributes": {"name": "drop-errors-updated"},
        "_application_id": "app-dst",
    }
    _run(rum.update_resource("ef-1", resource))

    patch_url = dest.patch.await_args.args[0]
    assert patch_url == "/api/v2/rum/applications/app-dst/retention_filters/exclusion/ef-dst"


def test_delete_resource_deletes_destination_id_on_correct_subpath():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_retention_filters"]["rf-1"] = {
        "id": "rf-dst",
        "type": "retention_filters",
        "_application_id": "app-dst",
    }

    _run(rum.delete_resource("rf-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/applications/app-dst/retention_filters/rf-dst")


def test_delete_resource_exclusion_uses_exclusion_subpath():
    rum = RUMRetentionFilters(MagicMock())
    dest = AsyncMock()
    rum.config.destination_client = dest
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_retention_filters"]["ef-1"] = {
        "id": "ef-dst",
        "type": "exclusion_filters",
        "_application_id": "app-dst",
    }

    _run(rum.delete_resource("ef-1"))

    dest.delete.assert_awaited_once_with("/api/v2/rum/applications/app-dst/retention_filters/exclusion/ef-dst")


def test_connect_resources_remaps_application_id_to_destination():
    """connect_resources remaps the synthetic _application_id from the source app
    id to the destination app id using state.destination['rum_applications']."""
    rum = RUMRetentionFilters(MagicMock())
    rum.config.state = MagicMock()
    rum.config.state.destination = defaultdict(dict)
    rum.config.state.destination["rum_applications"]["app-src"] = {"id": "app-dst"}
    rum.config.skip_failed_resource_connections = False
    rum.config.logger = MagicMock()
    # BaseResource.__init__ sets skip_resource_mapping; connect_resources uses
    # resource_connections declared on the config.
    resource = {
        "id": "rf-1",
        "type": "retention_filters",
        "attributes": {"name": "keep-views"},
        "_application_id": "app-src",
    }

    result = rum.connect_resources("rf-1", resource)

    assert resource["_application_id"] == "app-dst"
    # no failed connections -> empty result
    assert result.empty_binding_escalation is False
