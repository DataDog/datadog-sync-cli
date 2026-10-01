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


def test_update_resource_strips_trace_fields_when_not_editable():
    """When editability.trace_editable is false, the API rejects PATCHes that
    include cross_product_sampling.trace_sample_rate or trace_enabled. The
    model must strip those fields from the PATCH body."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "rum_apm_flat_sampling", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "rum_apm_flat_sampling",
        "type": "permanent_retention_filters",
        "attributes": {
            "cross_product_sampling": {"trace_sample_rate": 100, "trace_enabled": True},
            "editability": {"trace_editable": False},
            "name": "RUM APM Flat Sampling",
        },
        "_application_id": "app-dst",
    }
    composite = "app-src:rum_apm_flat_sampling"
    _run(rum.update_resource(composite, resource))

    patch_payload = dest.patch.await_args.args[1]
    # trace fields must be stripped from cross_product_sampling
    cps = patch_payload["data"]["attributes"].get("cross_product_sampling", {})
    assert "trace_sample_rate" not in cps
    assert "trace_enabled" not in cps


def test_update_resource_keeps_trace_fields_when_editable():
    """When editability.trace_editable is true (or absent), trace fields are
    kept in the PATCH body."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(
        return_value={"data": {"id": "synthetics_sessions", "type": "permanent_retention_filters", "attributes": {}}}
    )
    rum.config.destination_client = dest

    resource = {
        "id": "synthetics_sessions",
        "type": "permanent_retention_filters",
        "attributes": {
            "cross_product_sampling": {"trace_sample_rate": 50, "trace_enabled": True},
            "editability": {"trace_editable": True},
            "name": "Synthetics Sessions",
        },
        "_application_id": "app-dst",
    }
    _run(rum.update_resource("app-src:synthetics_sessions", resource))

    patch_payload = dest.patch.await_args.args[1]
    cps = patch_payload["data"]["attributes"].get("cross_product_sampling", {})
    assert cps.get("trace_sample_rate") == 50
    assert cps.get("trace_enabled") is True


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


def test_application_id_not_excluded_from_diff():
    """Regression test: _application_id must participate in the diff so a
    changed parent mapping (e.g. destination app deleted and recreated) forces
    a PATCH rather than being silently skipped."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    exclude_paths = rum.resource_config.deep_diff_config.get("exclude_regex_paths", [])
    assert not any("_application_id" in p for p in exclude_paths), (
        "_application_id must NOT be in deep_diff_config.exclude_regex_paths "
        "so a changed parent mapping forces a PATCH"
    )


def test_editability_not_in_excluded_attributes():
    """Regression test: attributes.editability must NOT be in
    excluded_attributes because update_resource needs to read
    editability.trace_editable to decide whether to strip trace fields.
    If excluded, prep_resource strips it before update_resource runs."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    excluded = rum.resource_config.excluded_attributes or []
    assert not any("editability" in a for a in excluded), (
        "attributes.editability must NOT be in excluded_attributes so it "
        "survives prep_resource and is available in update_resource"
    )


def test_editability_excluded_from_diff():
    """editability is read-only (returned by the API but not updatable), so it
    must be excluded from diffs to avoid a perpetual diff loop. It must survive
    prep_resource (not in excluded_attributes) but be excluded from
    comparison (in deep_diff_config.exclude_regex_paths)."""
    rum = RUMPermanentRetentionFilters(MagicMock())
    exclude_paths = rum.resource_config.deep_diff_config.get("exclude_regex_paths", [])
    assert any("editability" in p for p in exclude_paths), (
        "editability must be in deep_diff_config.exclude_regex_paths to avoid " "a perpetual diff loop (it's read-only)"
    )
