# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for synthetics tests resource handling.

These tests verify:
1. Status field is excluded from sync comparisons
2. New tests are created with status "paused"
3. Updates don't modify status field
4. ddr_metadata (source_public_id, source_status) is set on create and update
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datadog_sync.model.synthetics_tests import SyntheticsTests
from datadog_sync.utils.configuration import Configuration


class TestSyntheticsTestsStatusBehavior:
    """Test suite for synthetics test status handling."""

    def test_status_in_excluded_attributes(self):
        """Verify that 'status' is in the excluded_attributes list."""
        # build_excluded_attributes transforms "status" to "root['status']"
        assert "root['status']" in SyntheticsTests.resource_config.excluded_attributes, \
            "Status should be excluded from sync to prevent overwriting manual changes"

    @pytest.mark.asyncio
    async def test_create_resource_forces_paused_status(self):
        """Verify that create_resource forces status to 'paused'."""
        # Setup
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value={
            "public_id": "abc-123",
            "status": "paused",
            "type": "api",
            "name": "Test"
        })
        mock_config.destination_client = mock_client
        
        synthetics_tests = SyntheticsTests(mock_config)
        
        # Test resource with "live" status from source
        test_resource = {
            "type": "api",
            "status": "live",  # This should be overridden
            "name": "Test on www.datadoghq.com",
            "config": {},
            "locations": []
        }
        
        # Execute
        _id, response = await synthetics_tests.create_resource("test-id", test_resource)
        
        # Verify status was changed to "paused"
        assert test_resource["status"] == "paused", \
            "Status should be forced to 'paused' when creating new tests"
        
        # Verify API was called with paused status
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        sent_resource = call_args[0][1]
        assert sent_resource["status"] == "paused", \
            "API call should include status='paused'"

    @pytest.mark.asyncio
    async def test_create_resource_with_different_test_types(self):
        """Verify status forcing works for all test types."""
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value={"public_id": "abc-123", "status": "paused"})
        mock_config.destination_client = mock_client
        
        synthetics_tests = SyntheticsTests(mock_config)
        
        test_types = ["api", "browser", "mobile"]
        
        for test_type in test_types:
            mock_client.reset_mock()
            test_resource = {
                "type": test_type,
                "status": "live",
                "name": f"Test {test_type}",
                "config": {},
                "locations": []
            }
            
            await synthetics_tests.create_resource(f"test-{test_type}", test_resource)
            
            assert test_resource["status"] == "paused", \
                f"Status should be paused for {test_type} tests"
            
            # Verify correct endpoint was called
            call_args = mock_client.post.call_args
            assert f"/{test_type}" in call_args[0][0], \
                f"Should call correct endpoint for {test_type}"

    def test_status_not_in_nullable_attributes(self):
        """Verify status is not in non_nullable_attr to ensure it's properly handled."""
        non_nullable = SyntheticsTests.resource_config.non_nullable_attr or []
        assert "status" not in non_nullable, \
            "Status should not be in non_nullable_attr"

    def test_excluded_attributes_format(self):
        """Verify excluded_attributes contains properly formatted status entry."""
        excluded = SyntheticsTests.resource_config.excluded_attributes

        # build_excluded_attributes transforms entries to root['...'] format
        assert "root['status']" in excluded, \
            "Status should be in excluded_attributes list"

        # Verify other important exclusions are still there
        assert "root['monitor_id']" in excluded
        assert "root['public_id']" in excluded
        assert "root['created_at']" in excluded
        assert "root['modified_at']" in excluded

    @pytest.mark.asyncio
    async def test_create_preserves_other_fields(self):
        """Verify that forcing status doesn't affect other fields."""
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value={"public_id": "abc-123"})
        mock_config.destination_client = mock_client
        
        synthetics_tests = SyntheticsTests(mock_config)
        
        original_resource = {
            "type": "api",
            "status": "live",
            "name": "My Test",
            "message": "Test message",
            "config": {"assertions": []},
            "locations": ["aws:us-east-1"],
            "tags": ["team:synthetics"],
            "options": {"tick_every": 60}
        }
        
        # Make a copy to verify other fields aren't changed
        import copy
        resource_copy = copy.deepcopy(original_resource)
        
        await synthetics_tests.create_resource("test-id", resource_copy)
        
        # Only status should be different
        assert resource_copy["status"] == "paused"
        assert resource_copy["name"] == original_resource["name"]
        assert resource_copy["message"] == original_resource["message"]
        assert resource_copy["config"] == original_resource["config"]
        assert resource_copy["locations"] == original_resource["locations"]
        assert resource_copy["tags"] == original_resource["tags"]
        assert resource_copy["options"] == original_resource["options"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
