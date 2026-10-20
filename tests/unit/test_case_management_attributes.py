# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for the case_management_attributes resource model.

Pins the model's documented behavior. Design notes (schema verified against
the published API client models):

- Discovery is the org-wide list ``GET /api/v2/cases/types/custom_attributes``
  (single response; items carry ``attributes.case_type_id``). The CRUD paths
  are scoped under the parent case type.
- Both documented read endpoints return only
  ``{case_type_id, description, display_name, is_multi, key, type}`` — the
  update-only fields ``map_from`` / ``type_data`` are NOT readable, so they
  are never fabricated into payloads, and the resource ships with a README
  limitation note.
- Identity/adoption is ``attributes.key`` (org-wide unique — pinned contract;
  see the model docstring). Every map hit additionally validates the parent:
  a destination attribute with the same key but a different mapped case type
  is treated as no-match (create path) so a duplicate-key anomaly can never
  cross-parent update.
- Malformed source data at create raises ``SkipResource`` (per-resource skip,
  sync continues — disaster-recovery intent); a missing ``state.destination``
  entry in update/delete raises ``KeyError`` (framework invariant violation:
  loud, counted per-resource failure).
"""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.resource_utils import SkipResource


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests. See tests/unit/test_spans_metrics_409_fallback.py.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


TYPE_ID_SRC = "type-src-0000"
TYPE_ID_DST = "type-dst-0000"
ATTR_ID_SRC = "attr-src-0000"
ATTR_ID_DST = "attr-dst-0000"
ATTR_KEY = "attr_key_test_src"


def _make_attribute(attr_id, case_type_id, key=ATTR_KEY, display_name="Test Attribute", **extra_attributes):
    attributes = {
        "case_type_id": case_type_id,
        "key": key,
        "display_name": display_name,
        "is_multi": False,
        "type": "TEXT",
    }
    attributes.update(extra_attributes)
    return {"id": attr_id, "type": "custom_attribute", "attributes": attributes}


def _make_attrs_client(attributes):
    """AsyncMock CustomClient whose GET returns the org-wide list."""
    client = MagicMock()
    client.get = AsyncMock(return_value={"data": list(attributes)})
    return client


# ---------------------------------------------------------------------------
# Structural config (green at skeleton stage)
# ---------------------------------------------------------------------------


def test_config():
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes
    from datadog_sync.utils.base_resource import ResourceConfig

    rc = CaseManagementAttributes.resource_config
    assert isinstance(rc, ResourceConfig)
    assert CaseManagementAttributes.resource_type == "case_management_attributes"
    assert rc.base_path == "/api/v2/cases/types/custom_attributes"
    assert rc.resource_connections == {"case_management_types": ["attributes.case_type_id"]}
    assert rc.resource_mapping_key == "attributes.key"
    assert not rc.skip_resource_mapping
    # build_excluded_attributes rewrites "id" to root['id'] at construction
    # (ResourceConfig.__post_init__).
    assert rc.excluded_attributes == ["root['id']"]


def test_not_id_file_eligible():
    from datadog_sync.utils.configuration import (
        _ID_FILE_IMPORT_SUPPORTED_TYPES,
        _ID_FILE_STATE_LOAD_SUPPORTED_TYPES,
    )

    assert "case_management_attributes" not in _ID_FILE_IMPORT_SUPPORTED_TYPES
    assert "case_management_attributes" not in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES


# ---------------------------------------------------------------------------
# List / import group
# ---------------------------------------------------------------------------


def test_get_resources_lists_all_attributes(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    client = _make_attrs_client([_make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)])

    resp = _run(cm.get_resources(client))

    client.get.assert_awaited_once_with("/api/v2/cases/types/custom_attributes")
    assert resp == [
        {
            "id": ATTR_ID_SRC,
            "type": "custom_attribute",
            "attributes": {
                "case_type_id": TYPE_ID_SRC,
                "key": ATTR_KEY,
                "display_name": "Test Attribute",
                "is_multi": False,
                "type": "TEXT",
            },
        }
    ]


def test_import_resource_from_list_body(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    body = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    _id, resource = _run(cm.import_resource(resource=body))

    assert _id == ATTR_ID_SRC
    assert resource == body


def test_import_resource_by_id_rejected(mock_config):
    """Nothing depends on case_management_attributes, so the force-missing
    dependency import never targets them and no per-id GET exists; _id-only
    import raises NotImplementedError (exact contract — a generic raise would
    be classified as a silent "permanent" errored id by get_resources_by_ids).
    The user-facing --id-file path is unreachable (allowlist guard above)."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)

    with pytest.raises(NotImplementedError):
        _run(cm.import_resource(_id=ATTR_ID_SRC))


# ---------------------------------------------------------------------------
# Mapping / parent-check group
# ---------------------------------------------------------------------------


def test_mapping_key_plain(mock_config):
    """Identity is attributes.key — org-wide unique per the pinned contract
    (user-confirmed; see the model docstring)."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    assert cm.get_resource_mapping_key(resource) == ATTR_KEY
    assert cm.get_resource_mapping_key({"attributes": {}}) is None
    assert cm.get_resource_mapping_key({}) is None


def test_map_existing_resources_over_destination_list(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest_list = [
        _make_attribute(ATTR_ID_DST, TYPE_ID_DST),
        _make_attribute("attr-dst-other", TYPE_ID_DST, key="other_key"),
    ]
    mock_config.destination_client = _make_attrs_client(dest_list)

    _run(cm.map_existing_resources())

    assert set(cm._existing_resources_map.keys()) == {ATTR_KEY, "other_key"}


def test_map_hit_wrong_parent_is_not_mapped(mock_config):
    """A destination attribute with the same key but a DIFFERENT mapped parent
    type must be treated as no-match (create path) — no cross-parent update,
    even under a duplicate-key org anomaly."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    dest.put = AsyncMock()
    mock_config.destination_client = dest

    # The connected source attribute references destination type type-dst-0000;
    # the destination map holds key attr_key_test_src under a DIFFERENT type.
    cm._existing_resources_map = {ATTR_KEY: _make_attribute("attr-wrong-parent", "type-other-0000")}
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_DST)

    _id, resp = _run(cm.create_resource(ATTR_ID_SRC, resource))

    # No match → create path: POST under the correct parent type, no PUT.
    dest.post.assert_awaited_once_with(
        f"/api/v2/cases/types/{TYPE_ID_DST}/custom_attributes",
        {
            "data": {
                "type": "custom_attribute",
                "attributes": {"key": ATTR_KEY, "display_name": "Test Attribute", "is_multi": False, "type": "TEXT"},
            }
        },
    )
    dest.put.assert_not_awaited()


# ---------------------------------------------------------------------------
# Connection group (inherited default connect_id / extract_source_ids)
# ---------------------------------------------------------------------------


def test_connect_id_remaps_case_type_id(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    mock_config.state.destination["case_management_types"][TYPE_ID_SRC] = {"id": TYPE_ID_DST}
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    failed = cm.connect_id("case_type_id", resource["attributes"], "case_management_types")

    assert failed == []
    assert resource["attributes"]["case_type_id"] == TYPE_ID_DST


def test_connect_id_failure_leaves_source_id_unmodified(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    # Destination has no mapping for the source type id.
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    failed = cm.connect_id("case_type_id", resource["attributes"], "case_management_types")

    assert failed == [TYPE_ID_SRC]
    assert resource["attributes"]["case_type_id"] == TYPE_ID_SRC, "a failed lookup must not half-rewrite the payload"


def test_extract_source_ids_default(mock_config):
    """Pins --minimize-reads lazy-load support: the default extract_source_ids
    surfaces the referenced source type id."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    ids = cm.extract_source_ids("case_type_id", resource["attributes"], "case_management_types")

    assert ids == [TYPE_ID_SRC]


# ---------------------------------------------------------------------------
# Create-payload group (strict allowlists)
# ---------------------------------------------------------------------------


def test_create_resource_new_attribute_posts_to_type_path(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    # Post-connect resource: case_type_id already remapped to the destination id.
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_DST)
    cm._existing_resources_map = {}

    _id, resp = _run(cm.create_resource(ATTR_ID_SRC, resource))

    dest.post.assert_awaited_once_with(
        f"/api/v2/cases/types/{TYPE_ID_DST}/custom_attributes",
        {
            "data": {
                "type": "custom_attribute",
                "attributes": {"key": ATTR_KEY, "display_name": "Test Attribute", "is_multi": False, "type": "TEXT"},
            }
        },
    )
    assert _id == ATTR_ID_SRC
    assert resp["id"] == ATTR_ID_DST


def test_create_body_is_strict_allowlist(mock_config):
    """The POST body carries EXACTLY the create-model fields present in the
    source resource — id and attributes.case_type_id are path/identity
    concerns, and update-only fields (map_from, type_data) are never sent."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    # Even if update-only fields somehow appear in source state, they must be
    # stripped from the create payload (defense in depth against hand-edited
    # or future-API state).
    resource = _make_attribute(
        ATTR_ID_SRC,
        TYPE_ID_DST,
        description="desc",
        map_from="external_field",
        type_data={"options": [{"value": "us-east-1"}]},
    )
    cm._existing_resources_map = {}

    _run(cm.create_resource(ATTR_ID_SRC, resource))

    sent = dest.post.await_args.args[1]["data"]
    assert sent["type"] == "custom_attribute"
    assert set(sent["attributes"].keys()) == {"key", "display_name", "is_multi", "type", "description"}
    assert "case_type_id" not in sent["attributes"]
    assert "id" not in sent


def test_update_body_is_strict_allowlist(mock_config):
    """The PUT body carries only documented-updatable fields present in the
    source resource; key / is_multi / case_type_id are never sent."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.put = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_attributes"][ATTR_ID_SRC] = _make_attribute(
        ATTR_ID_DST, TYPE_ID_DST, description="old"
    )
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC, description="new description")

    _run(cm.update_resource(ATTR_ID_SRC, resource))

    sent = dest.put.await_args.args[1]["data"]
    assert sent["type"] == "custom_attribute"
    assert set(sent["attributes"].keys()) == {"description", "display_name", "type"}
    assert "key" not in sent["attributes"]
    assert "is_multi" not in sent["attributes"]
    assert "case_type_id" not in sent["attributes"]


def test_unreadable_update_only_fields_documented(mock_config):
    """map_from / type_data are not exposed by either documented read
    endpoint (per the published API client models), so the model must never
    fabricate them: they are stripped from create payloads and absent from
    update payloads unless present in source state. The README carries the
    limitation note."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    # Update payload includes update-only fields ONLY when present in state.
    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.put = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_attributes"][ATTR_ID_SRC] = _make_attribute(ATTR_ID_DST, TYPE_ID_DST)
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC, description="d")

    _run(cm.update_resource(ATTR_ID_SRC, resource))

    sent = dest.put.await_args.args[1]["data"]
    assert "map_from" not in sent["attributes"]
    assert "type_data" not in sent["attributes"]

    # README limitation note exists.
    import os

    readme = open(os.path.join(os.path.dirname(__file__), "..", "..", "README.md"), encoding="utf-8").read()
    assert "case_management_attributes" in readme
    assert (
        "map_from" in readme and "type_data" in readme
    ), "README must document the map_from/type_data sync limitation (not readable via the list API)"


def test_create_resource_existing_attribute_delegates_to_update(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock()
    dest.put = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    # Correct-parent map hit: destination attribute for the SAME mapped type.
    dest_attribute = _make_attribute(ATTR_ID_DST, TYPE_ID_DST)
    cm._existing_resources_map = {ATTR_KEY: dest_attribute}
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_DST)

    _id, resp = _run(cm.create_resource(ATTR_ID_SRC, resource))

    dest.post.assert_not_awaited()
    dest.put.assert_awaited_once_with(
        f"/api/v2/cases/types/{TYPE_ID_DST}/custom_attributes/{ATTR_ID_DST}",
        {
            "data": {
                "type": "custom_attribute",
                "attributes": {"display_name": "Test Attribute", "type": "TEXT"},
            }
        },
    )
    assert mock_config.state.destination["case_management_attributes"][ATTR_ID_SRC] == dest_attribute
    assert _id == ATTR_ID_SRC


# ---------------------------------------------------------------------------
# Update / delete group
# ---------------------------------------------------------------------------


def test_update_resource_targets_destination_ids(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.put = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_attributes"][ATTR_ID_SRC] = _make_attribute(ATTR_ID_DST, TYPE_ID_DST)
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC, description="updated")

    _id, resp = _run(cm.update_resource(ATTR_ID_SRC, resource))

    dest.put.assert_awaited_once_with(
        f"/api/v2/cases/types/{TYPE_ID_DST}/custom_attributes/{ATTR_ID_DST}",
        {
            "data": {
                "type": "custom_attribute",
                "attributes": {"description": "updated", "display_name": "Test Attribute", "type": "TEXT"},
            }
        },
    )
    assert _id == ATTR_ID_SRC
    assert resp["id"] == ATTR_ID_DST


def test_update_resource_missing_destination_entry_raises(mock_config):
    """Framework invariant: the apply flow only routes to update when the
    state.destination entry exists. A missing entry is a loud KeyError —
    counted as a per-resource failure, run continues."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    resource = _make_attribute(ATTR_ID_SRC, TYPE_ID_SRC)

    with pytest.raises(KeyError):
        _run(cm.update_resource(ATTR_ID_SRC, resource))


def test_delete_resource_uses_destination_ids(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    dest = MagicMock()
    dest.delete = AsyncMock()
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_attributes"][ATTR_ID_SRC] = _make_attribute(ATTR_ID_DST, TYPE_ID_DST)

    _run(cm.delete_resource(ATTR_ID_SRC))

    dest.delete.assert_awaited_once_with(f"/api/v2/cases/types/{TYPE_ID_DST}/custom_attributes/{ATTR_ID_DST}")


def test_delete_resource_missing_destination_entry_raises(mock_config):
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)

    with pytest.raises(KeyError):
        _run(cm.delete_resource(ATTR_ID_SRC))


# ---------------------------------------------------------------------------
# Malformed-state contract (parametrized over malformed shapes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resource",
    [
        # attributes missing entirely
        {"id": ATTR_ID_SRC, "type": "custom_attribute"},
        # attributes is None
        {"id": ATTR_ID_SRC, "type": "custom_attribute", "attributes": None},
        # case_type_id missing
        {"id": ATTR_ID_SRC, "type": "custom_attribute", "attributes": {"key": ATTR_KEY}},
        # case_type_id None
        {
            "id": ATTR_ID_SRC,
            "type": "custom_attribute",
            "attributes": {"key": ATTR_KEY, "case_type_id": None},
        },
        # case_type_id empty string
        {
            "id": ATTR_ID_SRC,
            "type": "custom_attribute",
            "attributes": {"key": ATTR_KEY, "case_type_id": ""},
        },
    ],
    ids=["no-attributes", "attributes-none", "no-case-type-id", "case-type-id-none", "case-type-id-empty"],
)
def test_create_resource_malformed_case_type_id_skips(mock_config, resource):
    """Malformed source/API data at create is a per-resource condition:
    SkipResource with a clear message — the handler records a skip and the
    sync continues (disaster-recovery intent)."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes

    cm = CaseManagementAttributes(mock_config)
    cm._existing_resources_map = {}

    with pytest.raises(SkipResource) as excinfo:
        _run(cm.create_resource(ATTR_ID_SRC, resource))
    assert "case_type_id" in str(excinfo.value) or "case type" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Handler-level continuation proof
# ---------------------------------------------------------------------------


def test_apply_cb_continues_past_malformed_resource(mock_config):
    """One malformed + one healthy attribute through _apply_resource_cb:
    one skip + one success, counters incremented — the DR continuation the
    model-level tests alone cannot establish."""
    from datadog_sync.model.case_management_attributes import CaseManagementAttributes
    from datadog_sync.utils.resources_handler import ResourcesHandler

    cm = CaseManagementAttributes(mock_config)
    # Parent type mapped in destination state so the healthy attribute's
    # connection resolves.
    mock_config.state.destination["case_management_types"][TYPE_ID_SRC] = {"id": TYPE_ID_DST}
    dest = MagicMock()
    dest.post = AsyncMock(return_value={"data": _make_attribute(ATTR_ID_DST, TYPE_ID_DST)})
    mock_config.destination_client = dest
    mock_config.resources = {"case_management_attributes": cm}

    malformed = {"id": "attr-bad", "type": "custom_attribute", "attributes": {"key": "k"}}
    healthy = _make_attribute("attr-ok", TYPE_ID_SRC)
    mock_config.state.source["case_management_attributes"]["attr-bad"] = deepcopy(malformed)
    mock_config.state.source["case_management_attributes"]["attr-ok"] = deepcopy(healthy)

    handler = ResourcesHandler(mock_config)
    handler.worker = MagicMock()
    handler.worker.counter = MagicMock()
    handler.sorter = MagicMock()
    handler._emit = MagicMock()

    _run(handler._apply_resource_cb(("case_management_attributes", "attr-bad")))
    _run(handler._apply_resource_cb(("case_management_attributes", "attr-ok")))

    handler.worker.counter.increment_skipped.assert_called_once()
    handler.worker.counter.increment_success.assert_called_once()
