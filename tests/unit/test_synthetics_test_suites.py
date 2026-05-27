# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for synthetics test suites resource handling.

These tests verify:
1. Excluded attributes are correctly configured
2. Create wraps payload in JSON:API envelope
3. Update uses destination public_id
4. Delete uses bulk-delete endpoint
5. connect_id maps test public_ids between orgs
"""

import asyncio
from collections import defaultdict

import pytest
from unittest.mock import AsyncMock, MagicMock
from datadog_sync.model.synthetics_test_suites import SyntheticsTestSuites
from datadog_sync.utils.configuration import Configuration


class TestSyntheticsTestSuitesConfig:
    """Test suite for resource configuration."""

    def test_excluded_attributes(self):
        """Verify auto-generated fields are excluded from sync."""
        excluded = SyntheticsTestSuites.resource_config.excluded_attributes
        for attr in [
            "root['id']",
            "root['attributes']['public_id']",
            "root['attributes']['created_at']",
            "root['attributes']['modified_at']",
            "root['attributes']['created_by']",
            "root['attributes']['modified_by']",
            "root['attributes']['monitor_id']",
            "root['attributes']['org_id']",
            "root['attributes']['version']",
            "root['attributes']['version_uuid']",
            "root['attributes']['overall_state']",
            "root['attributes']['overall_state_modified']",
            "root['attributes']['options']['slo_id']",
        ]:
            assert attr in excluded, f"{attr} should be in excluded_attributes"

    def test_resource_connections(self):
        """Verify test suites depend on synthetics_tests."""
        connections = SyntheticsTestSuites.resource_config.resource_connections
        assert "synthetics_tests" in connections
        assert "attributes.tests.public_id" in connections["synthetics_tests"]

    def test_tagging_config(self):
        """Verify tags path is correct."""
        assert SyntheticsTestSuites.resource_config.tagging_config.path == "attributes.tags"


class TestSyntheticsTestSuitesCRUD:
    """Test suite for CRUD operations."""

    def _make_instance(self):
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_config.destination_client = mock_client
        mock_config.state = MagicMock()
        mock_config.state.destination = defaultdict(dict)
        instance = SyntheticsTestSuites(mock_config)
        return instance, mock_config, mock_client

    def test_create_resource_wraps_envelope(self):
        """Verify create sends JSON:API data envelope."""
        instance, mock_config, mock_client = self._make_instance()
        mock_client.post = AsyncMock(
            return_value={
                "data": {
                    "type": "suites",
                    "id": "abc-def-ghi",
                    "attributes": {"name": "My Suite", "public_id": "abc-def-ghi"},
                }
            }
        )

        resource = {
            "type": "suites",
            "attributes": {
                "name": "My Suite",
                "tests": [],
                "tags": [],
                "type": "suite",
            },
        }

        _id, resp = asyncio.run(instance.create_resource("src-id", resource))

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/v2/synthetics/suites"
        assert call_args[0][1] == {"data": resource}
        assert _id == "src-id"
        assert resp["type"] == "suites"

    def test_update_resource_uses_dest_public_id(self):
        """Verify update targets destination public_id."""
        instance, mock_config, mock_client = self._make_instance()
        mock_config.state.destination["synthetics_test_suites"] = {
            "src-id": {"attributes": {"public_id": "dest-pub-id"}}
        }
        mock_client.put = AsyncMock(
            return_value={
                "data": {
                    "type": "suites",
                    "id": "dest-pub-id",
                    "attributes": {"name": "Updated", "public_id": "dest-pub-id"},
                }
            }
        )

        resource = {
            "type": "suites",
            "attributes": {"name": "Updated", "tests": [], "type": "suite"},
        }

        _id, resp = asyncio.run(instance.update_resource("src-id", resource))

        call_args = mock_client.put.call_args
        assert "/dest-pub-id" in call_args[0][0]
        assert call_args[0][1] == {"data": resource}

    def test_delete_resource_uses_bulk_delete(self):
        """Verify delete uses bulk-delete endpoint."""
        instance, mock_config, mock_client = self._make_instance()
        mock_config.state.destination["synthetics_test_suites"] = {
            "src-id": {"attributes": {"public_id": "dest-pub-id"}}
        }
        mock_client.post = AsyncMock(return_value={})

        asyncio.run(instance.delete_resource("src-id"))

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/v2/synthetics/suites/bulk-delete"
        expected = {"data": {"type": "delete_suites_request", "attributes": {"public_ids": ["dest-pub-id"]}}}
        assert call_args[0][1] == expected


class TestSyntheticsTestSuitesConnectId:
    """Test suite for test reference ID mapping."""

    def _make_instance_with_state(self, dest_tests_state):
        mock_config = MagicMock(spec=Configuration)
        mock_config.state = MagicMock()
        mock_config.state.destination = {"synthetics_tests": dest_tests_state}
        instance = SyntheticsTestSuites(mock_config)
        return instance

    def test_connect_id_maps_test_public_id(self):
        """Verify source test public_id is mapped to destination."""
        dest_state = {
            "src-abc-123#99999": {"public_id": "dest-xyz-789"},
        }
        instance = self._make_instance_with_state(dest_state)

        r_obj = {"public_id": "src-abc-123"}
        failed = instance.connect_id("public_id", r_obj, "synthetics_tests")

        assert r_obj["public_id"] == "dest-xyz-789"
        assert failed == []

    def test_connect_id_missing_test(self):
        """Verify failed connection when source test not found."""
        instance = self._make_instance_with_state({})

        r_obj = {"public_id": "nonexistent-id"}
        failed = instance.connect_id("public_id", r_obj, "synthetics_tests")

        assert failed == ["nonexistent-id"]
        assert r_obj["public_id"] == "nonexistent-id"  # unchanged

    def test_connect_id_multiple_tests_different_monitor_ids(self):
        """Verify correct match when multiple keys share a public_id prefix."""
        dest_state = {
            "src-abc-123#11111": {"public_id": "dest-xyz-789"},
            "src-abc-456#22222": {"public_id": "dest-xyz-000"},
        }
        instance = self._make_instance_with_state(dest_state)

        r_obj = {"public_id": "src-abc-456"}
        failed = instance.connect_id("public_id", r_obj, "synthetics_tests")

        assert r_obj["public_id"] == "dest-xyz-000"
        assert failed == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
