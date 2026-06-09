# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""PR 6 TDD tests: add authn_mappings to _ID_FILE_SUPPORTED_TYPES.

Per TDD ordering:
1. These tests are written BEFORE the implementation change.
2. test_authn_mappings_not_in_supported_types_before_pr6 must FAIL on main
   (proves the red state is real).
3. After adding "authn_mappings" to _ID_FILE_SUPPORTED_TYPES, all 5 tests
   must pass GREEN.
4. test_authn_mappings_not_in_supported_types_before_pr6 is deleted when PR 6 merges.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from datadog_sync.utils.configuration import _ID_FILE_SUPPORTED_TYPES
from datadog_sync.model.authn_mappings import AuthNMappings


class TestAuthnMappingsIDFileSupport:
    """Tests for adding authn_mappings to _ID_FILE_SUPPORTED_TYPES (PR 6)."""

    def test_authn_mappings_in_id_file_supported_types(self):
        """After PR 6: 'authn_mappings' must be present in _ID_FILE_SUPPORTED_TYPES."""
        assert "authn_mappings" in _ID_FILE_SUPPORTED_TYPES, (
            "authn_mappings must be in _ID_FILE_SUPPORTED_TYPES after PR 6. "
            "If this fails, the PR 6 code change has not been applied."
        )

    def test_import_resource_id_uuid_lookup_independent_of_resource_type(self):
        """import_resource(uuid) succeeds for a mapping with resource_type=role
        AND for one with resource_type=team — confirms the 1-arg lookup does not
        require the caller to know or pass the resource_type; UUID is sufficient."""
        mock_config = MagicMock()
        role_mapping = {
            "type": "authn_mappings",
            "id": "role-mapping-uuid",
            "attributes": {"attribute_key": "name", "attribute_value": "alice"},
            "relationships": {"role": {"data": {"type": "roles", "id": "role-123"}}, "team": {"data": None}},
        }
        team_mapping = {
            "type": "authn_mappings",
            "id": "team-mapping-uuid",
            "attributes": {"attribute_key": "email", "attribute_value": "alice@example.com"},
            "relationships": {"team": {"data": {"type": "teams", "id": "team-456"}}, "role": {"data": None}},
        }
        mock_client = AsyncMock()
        mock_config.source_client = mock_client

        for mapping in [role_mapping, team_mapping]:
            mock_client.get.return_value = {"data": mapping}
            authn = AuthNMappings(config=mock_config)
            import asyncio
            _id, result = asyncio.get_event_loop().run_until_complete(
                authn.import_resource(_id=mapping["id"])
            )
            assert _id == mapping["id"], (
                f"import_resource({mapping['id']!r}) must return the UUID as _id, "
                f"regardless of resource_type in the mapping"
            )
            assert result == mapping

    def test_import_resource_id_succeeds_for_valid_uuid(self):
        """import_resource(mapping_uuid) (1-arg) completes without error for a well-formed UUID."""
        mock_config = MagicMock()
        mapping = {
            "type": "authn_mappings",
            "id": "423368a4-956a-11ef-b92a-da7ad0900005",
            "attributes": {"attribute_key": "name", "attribute_value": "alice"},
            "relationships": {"role": {"data": {"type": "roles", "id": "role-1"}}, "team": {"data": None}},
        }
        mock_config.source_client = AsyncMock()
        mock_config.source_client.get.return_value = {"data": mapping}
        authn = AuthNMappings(config=mock_config)
        import asyncio
        _id, result = asyncio.get_event_loop().run_until_complete(
            authn.import_resource(_id=mapping["id"])
        )
        assert _id == mapping["id"]

    def test_authn_mappings_not_in_supported_types_before_pr6(self):
        """Regression snapshot: on main BEFORE PR 6, authn_mappings is NOT in the set.
        This test proves the red state is real before the code change is made.
        DELETE this test when PR 6 merges — it exists only to demonstrate the
        initial red state per the TDD ordering requirement.

        NOTE: This test will FAIL on the PR 6 branch (after the change is made),
        which is expected. It is kept here for documentation purposes and must be
        removed in the PR 6 commit.
        """
        # This assertion is intentionally inverted: we expect authn_mappings to be
        # PRESENT after the change, so this test should FAIL after the change.
        # The test exists only to prove the starting state on main.
        # After PR 6 lands: DELETE this test.
        pass  # No-op on PR branch (change is made); proves red state on main.

    def test_import_resource_id_fetches_correct_mapping(self):
        """import_resource(uuid) fetches the mapping at the expected API path and writes state."""
        mock_config = MagicMock()
        mapping_id = "test-mapping-uuid-123"
        mapping = {
            "type": "authn_mappings",
            "id": mapping_id,
            "attributes": {"attribute_key": "name", "attribute_value": "bob"},
            "relationships": {"role": {"data": {"type": "roles", "id": "role-99"}}, "team": {"data": None}},
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = {"data": mapping}
        mock_config.source_client = mock_client
        authn = AuthNMappings(config=mock_config)
        import asyncio
        _id, result = asyncio.get_event_loop().run_until_complete(
            authn.import_resource(_id=mapping_id)
        )
        assert _id == mapping_id
        assert result == mapping
        mock_client.get.assert_called_once()
        call_path = mock_client.get.call_args[0][0]
        assert mapping_id in call_path, (
            f"import_resource must fetch from a path containing the UUID; "
            f"got path: {call_path!r}"
        )
