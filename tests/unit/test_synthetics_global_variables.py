# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the synthetics_global_variables composite (name, type) mapping key.

The Datadog API enforces uniqueness on the (name, type) tuple — two global
variables can share a name as long as their types differ (e.g. "variable" vs
"secret_token"). Keying ``_existing_resources_map`` by ``name`` alone caused
last-write-wins map collisions and a production 409 Conflict on sync.

Tests 1, 2, 3a, 5 are red against ``resource_mapping_key="name"`` and green
after the fix to ``lambda r: f"{r['name']}:{r['type']}"``.
Tests 3b and 4 are green/green guards.
"""

import asyncio
from unittest.mock import AsyncMock

from datadog_sync.model.synthetics_global_variables import SyntheticsGlobalVariables


def _make_variable(name, type_, var_id, description="d", value_value="v"):
    """Build a synthetics global variable dict shaped like the GET response.

    Pre-populates ``value.value`` so ``_inject_secret_value`` short-circuits
    on the ``if "value" not in resource.get("value", {})`` guard at
    synthetics_global_variables.py:62 — no source_client.get call.
    """
    return {
        "id": var_id,
        "name": name,
        "type": type_,
        "description": description,
        "tags": [],
        "value": {"value": value_value, "secure": False, "options": {}},
    }


class TestCompositeKeyResolution:
    """3a: composite key resolves to "<name>:<type>" string.
    3b: missing type returns None silently (green/green guard)."""

    def test_composite_key_resolves_to_string(self, mock_config):
        """3a (red→green): full input yields composite "X:variable"."""
        instance = SyntheticsGlobalVariables(mock_config)
        resource = _make_variable("X", "variable", "abc")
        assert instance.get_resource_mapping_key(resource) == "X:variable"

    def test_missing_type_returns_none(self, mock_config):
        """3b (green/green): missing ``type`` returns None — pins the silent
        KeyError handling at base_resource.py:111-115. Behavior unchanged by
        the fix; guards against future refactors dropping the try/except or
        an API rename moving ``type`` under ``attributes``.
        """
        instance = SyntheticsGlobalVariables(mock_config)
        resource = {"name": "X"}
        assert instance.get_resource_mapping_key(resource) is None


class TestMapExistingResourcesRetainsBothTypes:
    """1: ``map_existing_resources()`` keeps both same-name variants under
    distinct composite keys."""

    def test_two_same_name_different_type_both_in_map(self, mock_config):
        """1 (red→green): destination has X/variable and X/secret_token.
        Under old ``resource_mapping_key="name"`` the map collapses to one
        entry. Under the composite key both entries are retained.
        """
        instance = SyntheticsGlobalVariables(mock_config)
        dest_resources = [
            _make_variable("X", "variable", "dest-1"),
            _make_variable("X", "secret_token", "dest-2"),
        ]
        instance.get_resources = AsyncMock(return_value=dest_resources)

        asyncio.run(instance.map_existing_resources())

        assert len(instance._existing_resources_map) == 2
        assert instance._existing_resources_map["X:variable"]["id"] == "dest-1"
        assert instance._existing_resources_map["X:secret_token"]["id"] == "dest-2"


class TestRoutingByCompositeKey:
    """2: routing picks the right destination entry by (name, type)."""

    def test_source_routes_to_matching_type_destination(self, mock_config):
        """2 (red→green): ``_existing_resources_map`` has both entries, source
        is X/secret_token — must remap to dest-2 and PUT, not POST.
        """
        instance = SyntheticsGlobalVariables(mock_config)
        dest_variable = _make_variable("X", "variable", "dest-1")
        dest_secret = _make_variable("X", "secret_token", "dest-2")
        instance._existing_resources_map = {
            "X:variable": dest_variable,
            "X:secret_token": dest_secret,
        }

        mock_config.destination_client.post = AsyncMock(return_value=dest_secret)
        mock_config.destination_client.put = AsyncMock(return_value=dest_secret)

        source = _make_variable("X", "secret_token", "src-A")
        asyncio.run(instance.create_resource("src-A", source))

        assert mock_config.state.destination["synthetics_global_variables"]["src-A"]["id"] == "dest-2"
        mock_config.destination_client.post.assert_not_called()
        mock_config.destination_client.put.assert_called_once()
        put_path = mock_config.destination_client.put.call_args[0][0]
        assert put_path == "/api/v1/synthetics/variables/dest-2"


class TestUniqueNameStillPosts:
    """4: green/green regression — a name not in the map still POSTs."""

    def test_unique_name_routes_to_post(self, mock_config):
        """4 (green/green): source name "Y" has no match — POST is called
        once and the response lands in state. Behavior unchanged by the fix
        when there is no type collision.
        """
        instance = SyntheticsGlobalVariables(mock_config)
        instance._existing_resources_map = {}

        source = _make_variable("Y", "variable", "src-Y")
        response = _make_variable("Y", "variable", "dest-Y")
        mock_config.destination_client.post = AsyncMock(return_value=response)
        mock_config.destination_client.put = AsyncMock()

        _, resp = asyncio.run(instance.create_resource("src-Y", source))

        mock_config.destination_client.post.assert_called_once()
        mock_config.destination_client.put.assert_not_called()
        assert resp["id"] == "dest-Y"


class TestPartialCollisionPosts:
    """5: the literal production bug — destination has X/variable, source is
    X/secret_token. Under old name-only key this incorrectly hit PUT on the
    wrong type and the API returned 409. Under the composite key the source
    correctly falls through to POST as a new variable."""

    def test_partial_collision_routes_to_post(self, mock_config):
        """5 (red→green): destination only has the X/variable entry. Source
        is X/secret_token. Must POST (new variable), not PUT (wrong type).
        """
        instance = SyntheticsGlobalVariables(mock_config)
        instance._existing_resources_map = {
            "X:variable": _make_variable("X", "variable", "dest-1"),
        }

        source = _make_variable("X", "secret_token", "src-A")
        response = _make_variable("X", "secret_token", "dest-new")
        mock_config.destination_client.post = AsyncMock(return_value=response)
        mock_config.destination_client.put = AsyncMock()

        _, resp = asyncio.run(instance.create_resource("src-A", source))

        mock_config.destination_client.post.assert_called_once()
        mock_config.destination_client.put.assert_not_called()
        assert resp["id"] == "dest-new"
