# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for the case_management_types resource model.

Pins the model's documented behavior so the implementation lands against a
frozen contract. Design notes (see PR description for the schema-spike
summary):

- The types API has no per-id GET; ``GET /api/v2/cases/types`` is a single
  unpaginated list. Targeted import by id is therefore list-and-filter, which
  is also the path ``ResourcesHandler._force_missing_dep_import_cb`` takes
  when ``--force-missing-dependencies`` resolves a missing dependency.
- Tombstoned types (``attributes.deleted_at`` set) are skipped at import and
  excluded from destination mapping so a deleted destination type cannot
  shadow a same-named active source type.
- Identity/adoption is ``attributes.name`` (teams pattern): a name match
  against the destination hydrates ``state.destination`` and delegates to
  update; a miss POSTs a new type.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.resource_utils import SkipResource


def _run(coro):
    # Fresh loop per call: pytest-asyncio strict mode closes the ambient loop
    # between tests. See tests/unit/test_spans_metrics_409_fallback.py for the
    # pattern this mirrors.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_type(type_id, name, description="desc", emoji=":mag:", deleted_at=None):
    attributes = {"name": name, "description": description, "emoji": emoji}
    if deleted_at is not None:
        attributes["deleted_at"] = deleted_at
    return {"id": type_id, "type": "case_type", "attributes": attributes}


def _make_types_client(types):
    """AsyncMock CustomClient whose GET returns {"data": [...]} for any path."""
    client = MagicMock()
    client.get = AsyncMock(return_value={"data": list(types)})
    return client


# ---------------------------------------------------------------------------
# Structural config (green at skeleton stage)
# ---------------------------------------------------------------------------


def test_config():
    from datadog_sync.model.case_management_types import CaseManagementTypes
    from datadog_sync.utils.base_resource import ResourceConfig

    rc = CaseManagementTypes.resource_config
    assert isinstance(rc, ResourceConfig)
    assert CaseManagementTypes.resource_type == "case_management_types"
    assert rc.base_path == "/api/v2/cases/types"
    assert rc.resource_mapping_key == "attributes.name"
    assert not rc.skip_resource_mapping
    assert not rc.resource_connections
    # build_excluded_attributes rewrites dot paths to root['...'] subscript
    # form at construction (ResourceConfig.__post_init__). Precedent:
    # tests/unit/test_synthetics_tests.py::test_status_in_excluded_attributes.
    assert "root['id']" in rc.excluded_attributes
    assert "root['attributes']['deleted_at']" in rc.excluded_attributes


def test_not_id_file_eligible():
    """List-backed _id lookup exists for dependency resolution only; the
    user-facing --id-file allowlists must NOT gain this type without the
    per-model fan-out review the allowlist docstrings require."""
    from datadog_sync.utils.configuration import (
        _ID_FILE_IMPORT_SUPPORTED_TYPES,
        _ID_FILE_STATE_LOAD_SUPPORTED_TYPES,
    )

    assert "case_management_types" not in _ID_FILE_IMPORT_SUPPORTED_TYPES
    assert "case_management_types" not in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES


# ---------------------------------------------------------------------------
# Import / list / tombstone behavior group
# ---------------------------------------------------------------------------


def test_get_resources_plain_list_get(mock_config):
    """LIST is a single unpaginated GET /api/v2/cases/types."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    client = _make_types_client([_make_type("type-a", "Investigation")])

    resp = _run(cm.get_resources(client))

    client.get.assert_awaited_once_with("/api/v2/cases/types")
    assert resp == [
        {
            "id": "type-a",
            "type": "case_type",
            "attributes": {"name": "Investigation", "description": "desc", "emoji": ":mag:"},
        }
    ]


def test_import_resource_from_list_body(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    body = _make_type("type-a", "Investigation")

    _id, resource = _run(cm.import_resource(resource=body))

    assert _id == "type-a"
    assert resource == body


def test_import_resource_skips_tombstoned_type(mock_config):
    """A source type carrying attributes.deleted_at is a tombstone: skip it
    rather than syncing deleted state into the destination org."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    body = _make_type("type-a", "Investigation", deleted_at="2019-09-19T10:00:00.000Z")

    with pytest.raises(SkipResource):
        _run(cm.import_resource(resource=body))


# ---------------------------------------------------------------------------
# Targeted (_id) import group: list-backed lookup + cache
# ---------------------------------------------------------------------------


def test_import_resource_by_id_found_via_list(mock_config):
    """No per-id GET exists: _id mode lists the endpoint and returns the
    matching entry. ResourcesHandler._force_missing_dep_import_cb relies on
    this path for --force-missing-dependencies."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    client = _make_types_client([_make_type("type-src", "Investigation"), _make_type("type-other", "Other")])
    mock_config.source_client = client

    _id, resource = _run(cm.import_resource(_id="type-src"))

    assert _id == "type-src"
    assert resource["id"] == "type-src"
    assert resource["attributes"]["name"] == "Investigation"
    client.get.assert_awaited_once_with("/api/v2/cases/types")


def test_import_resource_by_id_missing_skips(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    mock_config.source_client = _make_types_client([_make_type("type-a", "Investigation")])

    with pytest.raises(SkipResource):
        _run(cm.import_resource(_id="type-gone"))


def test_import_resource_by_id_tombstoned_skips(mock_config):
    """A tombstoned entry found by id is skipped, not imported."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    mock_config.source_client = _make_types_client(
        [_make_type("type-src", "Investigation", deleted_at="2019-09-19T10:00:00.000Z")]
    )

    with pytest.raises(SkipResource):
        _run(cm.import_resource(_id="type-src"))


def test_import_resource_by_id_uses_cached_list(mock_config):
    """The list-backed lookup caches the source list (lock-guarded, built in
    init_async) so a worker-pool fan-out of force-missing-dep imports performs
    one GET, not one per id."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    client = _make_types_client([_make_type("type-src", "Investigation"), _make_type("type-b", "B")])
    mock_config.source_client = client

    async def scenario():
        await cm.init_async()
        first_id, _ = await cm.import_resource(_id="type-src")
        second_id, _ = await cm.import_resource(_id="type-b")
        return first_id, second_id

    first_id, second_id = _run(scenario())

    assert first_id == "type-src"
    assert second_id == "type-b"
    client.get.assert_awaited_once_with("/api/v2/cases/types")


def test_import_resource_by_id_without_resource_or_valid_data_raises(mock_config):
    """_id mode with no list data available at all (transport/parse anomaly)
    must fail loudly, not return (None, None) — the queue handler would
    otherwise write a None resource into source state."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)

    with pytest.raises(Exception) as excinfo:
        _run(cm.import_resource(_id="type-src"))
    assert not isinstance(excinfo.value, SkipResource)


# ---------------------------------------------------------------------------
# Force-missing-dependency handler path (handler-level, real model)
# ---------------------------------------------------------------------------


def _drive_force_missing_dep(mock_config, list_body, _id="type-src"):
    """Drive ResourcesHandler._force_missing_dep_import_cb against the REAL
    model with a mocked list endpoint. Returns the handler for assertions."""
    from datadog_sync.model.case_management_types import CaseManagementTypes
    from datadog_sync.utils.resources_handler import ResourcesHandler

    cm = CaseManagementTypes(mock_config)
    mock_config.source_client = _make_types_client(list_body)
    mock_config.resources = {"case_management_types": cm}

    handler = ResourcesHandler(mock_config)
    handler._emit = MagicMock()
    handler.worker = MagicMock()
    handler.worker.work_queue = MagicMock()
    handler._dependency_graph = {}
    handler._resource_connections = MagicMock(return_value=(set(), set()))

    _run(handler._force_missing_dep_import_cb(("case_management_types", _id)))
    return handler, cm


def test_force_missing_dep_import_cb_lands_in_source_state(mock_config):
    """sync --resources=case_management_attributes --force-missing-dependencies
    (PR 2) resolves the missing parent type through this callback; the dep
    must land in source state and be emitted as an import success."""
    handler, cm = _drive_force_missing_dep(mock_config, [_make_type("type-src", "Investigation")])

    mock_config.state.set_source.assert_called_once()
    args = mock_config.state.set_source.call_args[0]
    assert args[0] == "case_management_types"
    assert args[1] == "type-src"
    assert args[2]["id"] == "type-src"

    emit_args, emit_kwargs = handler._emit.call_args
    assert emit_args[:4] == ("case_management_types", "type-src", "import", "success")


def test_force_missing_dep_import_cb_missing_id_skips_and_continues(mock_config):
    """A dependency id absent from the source list is skipped (SkipResource
    branch): emitted as skipped, nothing written to source state, and the
    callback returns instead of raising."""
    handler, cm = _drive_force_missing_dep(mock_config, [_make_type("type-a", "Investigation")], _id="type-gone")

    mock_config.state.set_source.assert_not_called()
    emit_args, emit_kwargs = handler._emit.call_args
    assert emit_args[:4] == ("case_management_types", "type-gone", "import", "skipped")


# ---------------------------------------------------------------------------
# Create / update / delete group (teams pattern)
# ---------------------------------------------------------------------------


def test_create_resource_new_type_posts(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock(return_value={"data": _make_type("type-dst", "Investigation")})
    dest.put = AsyncMock()
    mock_config.destination_client = dest
    resource = _make_type("type-src", "Investigation")
    cm._existing_resources_map = {}

    _id, resp = _run(cm.create_resource("type-src", resource))

    dest.post.assert_awaited_once_with("/api/v2/cases/types", {"data": resource})
    dest.put.assert_not_awaited()
    assert _id == "type-src"
    assert resp["id"] == "type-dst"


def test_create_resource_existing_type_delegates_to_update(mock_config):
    """Name match against the destination mapping hydrates
    state.destination and delegates to update_resource (teams pattern)."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest = MagicMock()
    dest.post = AsyncMock()
    dest.put = AsyncMock(return_value={"data": _make_type("type-dst", "Investigation")})
    mock_config.destination_client = dest
    resource = _make_type("type-src", "Investigation")
    dest_type = _make_type("type-dst", "Investigation")
    cm._existing_resources_map = {"Investigation": dest_type}

    _id, resp = _run(cm.create_resource("type-src", resource))

    dest.post.assert_not_awaited()
    dest.put.assert_awaited_once_with("/api/v2/cases/types/type-dst", {"data": resource})
    assert mock_config.state.destination["case_management_types"]["type-src"] == dest_type
    assert _id == "type-src"
    assert resp["id"] == "type-dst"


def test_update_resource_targets_destination_id(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest = MagicMock()
    dest.put = AsyncMock(return_value={"data": _make_type("type-dst", "Investigation")})
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_types"]["type-src"] = _make_type("type-dst", "Investigation")
    resource = _make_type("type-src", "Investigation")

    _id, resp = _run(cm.update_resource("type-src", resource))

    dest.put.assert_awaited_once_with("/api/v2/cases/types/type-dst", {"data": resource})
    assert _id == "type-src"
    assert resp["id"] == "type-dst"


def test_delete_resource_uses_destination_id(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest = MagicMock()
    dest.delete = AsyncMock()
    mock_config.destination_client = dest
    mock_config.state.destination["case_management_types"]["type-src"] = _make_type("type-dst", "Investigation")

    _run(cm.delete_resource("type-src"))

    dest.delete.assert_awaited_once_with("/api/v2/cases/types/type-dst")


# ---------------------------------------------------------------------------
# Mapping group
# ---------------------------------------------------------------------------


def test_get_resource_mapping_key_name(mock_config):
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    resource = _make_type("type-a", "Investigation")

    assert cm.get_resource_mapping_key(resource) == "Investigation"
    assert cm.get_resource_mapping_key({"attributes": {}}) is None
    assert cm.get_resource_mapping_key({}) is None


def test_map_existing_resources_over_destination_list(mock_config):
    """Default map path exercised over a representative destination list:
    keyed by attributes.name."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest_list = [_make_type("type-dst-1", "Investigation"), _make_type("type-dst-2", "Bug Report")]
    mock_config.destination_client = _make_types_client(dest_list)

    _run(cm.map_existing_resources())

    assert set(cm._existing_resources_map.keys()) == {"Investigation", "Bug Report"}
    assert cm._existing_resources_map["Investigation"]["id"] == "type-dst-1"


def test_map_existing_resources_skips_tombstoned_destination_types(mock_config):
    """A deleted destination type must not shadow a same-named active source
    type: tombstones are excluded from _existing_resources_map so the source
    type is recreated instead of PUT against a tombstone."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest_list = [
        _make_type("type-dst-1", "Investigation"),
        _make_type("type-dst-deleted", "Old Investigation", deleted_at="2019-09-19T10:00:00.000Z"),
    ]
    mock_config.destination_client = _make_types_client(dest_list)

    _run(cm.map_existing_resources())

    assert set(cm._existing_resources_map.keys()) == {"Investigation"}
    assert "Old Investigation" not in cm._existing_resources_map


def test_map_existing_resources_refuses_ambiguous_names(mock_config):
    """Ambiguity-refused identity (implementation contract §3.1): two ACTIVE
    destination types sharing a name must not be adopted — the name is
    excluded from the mapping (never a silent data[0] pick), so the source
    type takes the create path and the API's duplicate handling surfaces
    per-resource."""
    from datadog_sync.model.case_management_types import CaseManagementTypes

    cm = CaseManagementTypes(mock_config)
    dest_list = [
        _make_type("type-dst-1", "Ambiguous"),
        _make_type("type-dst-2", "Ambiguous"),
        _make_type("type-dst-3", "Unique"),
    ]
    mock_config.destination_client = _make_types_client(dest_list)

    _run(cm.map_existing_resources())

    assert "Ambiguous" not in cm._existing_resources_map
    assert set(cm._existing_resources_map.keys()) == {"Unique"}
