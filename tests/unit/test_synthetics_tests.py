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

import asyncio
import copy

import pytest
from unittest.mock import AsyncMock, MagicMock
from datadog_sync.model.synthetics_tests import SyntheticsTests
from datadog_sync.utils.configuration import Configuration


class TestSyntheticsTestsStatusBehavior:
    """Test suite for synthetics test status handling."""

    def test_status_in_excluded_attributes(self):
        """Verify that 'status' is in the excluded_attributes list."""
        # build_excluded_attributes transforms "status" to "root['status']"
        assert (
            "root['status']" in SyntheticsTests.resource_config.excluded_attributes
        ), "Status should be excluded from sync to prevent overwriting manual changes"

    def test_create_resource_forces_paused_status(self):
        """Verify that create_resource forces status to 'paused'."""
        # Setup
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value={"public_id": "abc-123", "status": "paused", "type": "api", "name": "Test"}
        )
        mock_config.destination_client = mock_client

        synthetics_tests = SyntheticsTests(mock_config)

        # Test resource with "live" status from source
        test_resource = {
            "type": "api",
            "status": "live",  # This should be overridden
            "name": "Test on www.datadoghq.com",
            "config": {},
            "locations": [],
        }

        # Execute
        _id, response = asyncio.run(synthetics_tests.create_resource("src-pub-id#12345", test_resource))

        # Verify status was changed to "paused"
        assert test_resource["status"] == "paused", "Status should be forced to 'paused' when creating new tests"

        # Verify API was called with paused status
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        sent_resource = call_args[0][1]
        assert sent_resource["status"] == "paused", "API call should include status='paused'"

    def test_create_resource_with_different_test_types(self):
        """Verify status forcing works for all test types."""
        mock_config = MagicMock(spec=Configuration)
        mock_client = AsyncMock()
        mock_config.destination_client = mock_client

        synthetics_tests = SyntheticsTests(mock_config)

        # v1 test types return flat responses
        v1_test_types = ["api", "browser", "mobile"]
        for test_type in v1_test_types:
            mock_client.reset_mock()
            mock_client.post = AsyncMock(return_value={"public_id": "abc-123", "status": "paused"})
            test_resource = {
                "type": test_type,
                "status": "live",
                "name": f"Test {test_type}",
                "config": {},
                "locations": [],
            }

            asyncio.run(synthetics_tests.create_resource(f"src-pub-id#{test_type}", test_resource))

            assert test_resource["status"] == "paused", f"Status should be paused for {test_type} tests"

            # Verify correct endpoint was called
            call_args = mock_client.post.call_args
            assert f"/{test_type}" in call_args[0][0], f"Should call correct endpoint for {test_type}"

        # network test type returns v2 wrapped response
        mock_client.reset_mock()
        mock_client.post = AsyncMock(
            return_value={"data": {"id": "abc-123", "type": "network_test", "attributes": {"status": "paused"}}}
        )
        test_resource = {
            "type": "network",
            "status": "live",
            "name": "Test network",
            "config": {},
            "locations": [],
        }

        asyncio.run(synthetics_tests.create_resource("src-pub-id#network", test_resource))

        assert test_resource["status"] == "paused", "Status should be paused for network tests"

        call_args = mock_client.post.call_args
        assert "/api/v2/synthetics/tests/network" == call_args[0][0], "Should call v2 endpoint for network"
        # Verify v2 wrapped body
        sent_body = call_args[0][1]
        assert "data" in sent_body, "Network test should use v2 wrapped body"
        assert sent_body["data"]["type"] == "network"

    def test_status_not_in_nullable_attributes(self):
        """Verify status is not in non_nullable_attr to ensure it's properly handled."""
        non_nullable = SyntheticsTests.resource_config.non_nullable_attr or []
        assert "status" not in non_nullable, "Status should not be in non_nullable_attr"

    def test_excluded_attributes_format(self):
        """Verify excluded_attributes contains properly formatted status entry."""
        excluded = SyntheticsTests.resource_config.excluded_attributes

        # build_excluded_attributes transforms entries to root['...'] format
        assert "root['status']" in excluded, "Status should be in excluded_attributes list"

        # Verify other important exclusions are still there
        assert "root['monitor_id']" in excluded
        assert "root['public_id']" in excluded
        assert "root['created_at']" in excluded
        assert "root['modified_at']" in excluded

    def test_create_preserves_other_fields(self):
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
            "options": {"tick_every": 60},
        }

        resource_copy = copy.deepcopy(original_resource)

        asyncio.run(synthetics_tests.create_resource("src-pub-id#12345", resource_copy))

        # Only status should be different
        assert resource_copy["status"] == "paused"
        assert resource_copy["name"] == original_resource["name"]
        assert resource_copy["message"] == original_resource["message"]
        assert resource_copy["config"] == original_resource["config"]
        assert resource_copy["locations"] == original_resource["locations"]
        assert resource_copy["tags"] == original_resource["tags"]
        assert resource_copy["options"] == original_resource["options"]


class TestSyntheticsTestsRumConnectionBehavior:
    """Test suite for RUM application ID and clientTokenId remapping."""

    def _make_synthetics_tests(self):
        mock_config = MagicMock(spec=Configuration)
        mock_config.state = MagicMock()
        mock_config.state.destination = {
            "rum_applications": {
                "source-rum-app-id": {
                    "id": "dest-rum-app-id",
                    "type": "rum_application",
                    "attributes": {
                        "name": "My RUM App",
                        "api_key_id": 999888,
                        "client_token": "pubdest1234567890abcdef",
                    },
                }
            }
        }
        return SyntheticsTests(mock_config)

    def test_connect_id_remaps_application_id(self):
        """Verify applicationId is remapped to destination RUM app ID."""
        synthetics_tests = self._make_synthetics_tests()
        rum_settings = {
            "applicationId": "source-rum-app-id",
            "clientTokenId": 111222,
            "isEnabled": True,
        }

        failed = synthetics_tests.connect_id("applicationId", rum_settings, "rum_applications")

        assert failed == []
        assert rum_settings["applicationId"] == "dest-rum-app-id"

    def test_connect_id_remaps_client_token_id(self):
        """Verify clientTokenId is remapped to destination RUM app's api_key_id."""
        synthetics_tests = self._make_synthetics_tests()
        rum_settings = {
            "applicationId": "source-rum-app-id",
            "clientTokenId": 111222,
            "isEnabled": True,
        }

        synthetics_tests.connect_id("applicationId", rum_settings, "rum_applications")

        assert rum_settings["clientTokenId"] == 999888

    def test_connect_id_without_client_token_id(self):
        """Verify connect_id works when clientTokenId is absent from rumSettings."""
        synthetics_tests = self._make_synthetics_tests()
        rum_settings = {
            "applicationId": "source-rum-app-id",
            "isEnabled": True,
        }

        failed = synthetics_tests.connect_id("applicationId", rum_settings, "rum_applications")

        assert failed == []
        assert rum_settings["applicationId"] == "dest-rum-app-id"
        assert "clientTokenId" not in rum_settings

    def test_connect_id_rum_app_not_found(self):
        """Verify failed connection is reported when RUM app is missing."""
        synthetics_tests = self._make_synthetics_tests()
        rum_settings = {
            "applicationId": "nonexistent-rum-app-id",
            "clientTokenId": 111222,
            "isEnabled": True,
        }

        failed = synthetics_tests.connect_id("applicationId", rum_settings, "rum_applications")

        assert "nonexistent-rum-app-id" in failed
        # Original values should be unchanged
        assert rum_settings["applicationId"] == "nonexistent-rum-app-id"
        assert rum_settings["clientTokenId"] == 111222


class TestSyntheticsTestsOrgPrincipalRemap:
    """Test suite for restriction_policy org: principal remapping in synthetics_tests.

    Mirrors the tests in test_monitors.py for the same feature. See HAMR-392 Jul8-T15.
    """

    def _make_synthetics_tests(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.state.source = {"synthetics_tests": {}}
        return SyntheticsTests(mock_config)

    def test_pre_resource_action_hook_replaces_org_principal(self):
        """org: principal in restriction_policy bindings is replaced when org_principal is set."""
        synthetics_tests = self._make_synthetics_tests()
        synthetics_tests.org_principal = "org:dest-pub-id"
        resource = {
            "type": "api",
            "public_id": "abc-123",
            "status": "live",
            "restriction_policy": {
                "bindings": [{"principals": ["org:src-pub-id", "user:some-user"], "relation": "editor"}]
            },
        }
        asyncio.run(synthetics_tests.pre_resource_action_hook("abc-123#12345", resource))
        assert resource["restriction_policy"]["bindings"][0]["principals"][0] == "org:dest-pub-id"
        assert resource["restriction_policy"]["bindings"][0]["principals"][1] == "user:some-user"

    def test_pre_resource_action_hook_skips_org_when_no_org_principal(self):
        """org: principal is left unchanged when org_principal is None."""
        synthetics_tests = self._make_synthetics_tests()
        assert synthetics_tests.org_principal is None
        resource = {
            "type": "api",
            "public_id": "abc-123",
            "status": "live",
            "restriction_policy": {"bindings": [{"principals": ["org:src-pub-id"], "relation": "editor"}]},
        }
        asyncio.run(synthetics_tests.pre_resource_action_hook("abc-123#12345", resource))
        assert resource["restriction_policy"]["bindings"][0]["principals"][0] == "org:src-pub-id"

    def test_pre_resource_action_hook_no_restriction_policy_is_noop(self):
        """Resources without restriction_policy are unaffected by the remap step."""
        synthetics_tests = self._make_synthetics_tests()
        synthetics_tests.org_principal = "org:dest-pub-id"
        resource = {"type": "api", "public_id": "abc-123", "status": "live"}
        asyncio.run(synthetics_tests.pre_resource_action_hook("abc-123#12345", resource))
        # DR metadata is still injected by the existing hook path.
        assert resource["metadata"]["disaster_recovery"]["source_public_id"] == "abc-123"

    def _seed_source_with_policy(self, synthetics_tests):
        """Populate source state with one test that carries a restriction_policy."""
        synthetics_tests.config.state.source = {
            "synthetics_tests": {
                "abc-123#1": {"public_id": "abc-123", "restriction_policy": {"bindings": [{"principals": ["org:src"]}]}}
            }
        }

    def _seed_source_without_policy(self, synthetics_tests):
        """Populate source state with one test lacking a restriction_policy."""
        synthetics_tests.config.state.source = {"synthetics_tests": {"abc-123#1": {"public_id": "abc-123"}}}

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Source carries a restriction_policy → GET fires → org_principal set."""
        synthetics_tests = self._make_synthetics_tests()
        self._seed_source_with_policy(synthetics_tests)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={"data": {"relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}}}}
        )
        synthetics_tests.config.destination_client = mock_client
        asyncio.run(synthetics_tests.pre_apply_hook())
        assert synthetics_tests.org_principal == "org:00000000-0000-beef-0000-000000000000"
        mock_client.get.assert_awaited_once()

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Source carries a policy but GET fails → org_principal stays None and error re-raised."""
        synthetics_tests = self._make_synthetics_tests()
        self._seed_source_with_policy(synthetics_tests)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        synthetics_tests.config.destination_client = mock_client
        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(synthetics_tests.pre_apply_hook())
        assert synthetics_tests.org_principal is None

    def test_pre_apply_hook_skips_current_user_when_no_policy(self):
        """No source test carries a restriction_policy → GET is not called; org_principal stays None."""
        synthetics_tests = self._make_synthetics_tests()
        self._seed_source_without_policy(synthetics_tests)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        synthetics_tests.config.destination_client = mock_client
        asyncio.run(synthetics_tests.pre_apply_hook())
        assert synthetics_tests.org_principal is None
        mock_client.get.assert_not_awaited()

    def test_pre_apply_hook_skips_current_user_when_source_empty(self):
        """Empty source state → GET is not called; org_principal stays None."""
        synthetics_tests = self._make_synthetics_tests()
        synthetics_tests.config.state.source = {"synthetics_tests": {}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        synthetics_tests.config.destination_client = mock_client
        asyncio.run(synthetics_tests.pre_apply_hook())
        assert synthetics_tests.org_principal is None
        mock_client.get.assert_not_awaited()


class TestSyntheticsTestsRestrictionPolicyPrincipals:
    """Test suite for restriction_policy user:/role:/team: principal remapping.

    Mirrors TestMonitorsRestrictionPolicyPrincipals — synthetics_tests must
    remap prefixed principals via connect_id when resource_connections routes
    them here per resource type.
    """

    def _make_synthetics_tests(self, destination_state=None):
        from collections import defaultdict

        mock_config = MagicMock()
        mock_config.state = MagicMock()
        state = defaultdict(dict)
        if destination_state:
            state.update(destination_state)
        mock_config.state.destination = state
        return SyntheticsTests(mock_config)

    def test_connect_id_remaps_user_principal(self):
        """user: principal is remapped when user exists in destination state."""
        state = {
            "users": {"src-user-id": {"id": "dst-user-id"}},
            "roles": {},
            "teams": {},
        }
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["user:src-user-id", "role:src-role-id"], "relation": "editor"}
        result = synthetics_tests.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "user:dst-user-id"
        assert binding["principals"][1] == "role:src-role-id"
        assert result == []

    def test_connect_id_remaps_role_principal(self):
        """role: principal is remapped when role exists in destination state."""
        state = {
            "users": {},
            "roles": {"src-role-id": {"id": "dst-role-id"}},
            "teams": {},
        }
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["role:src-role-id"], "relation": "viewer"}
        result = synthetics_tests.connect_id("principals", binding, "roles")
        assert binding["principals"][0] == "role:dst-role-id"
        assert result == []

    def test_connect_id_remaps_team_principal(self):
        """team: principal is remapped when team exists in destination state."""
        state = {
            "users": {},
            "roles": {},
            "teams": {"src-team-id": {"id": "dst-team-id"}},
        }
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["team:src-team-id"], "relation": "editor"}
        result = synthetics_tests.connect_id("principals", binding, "teams")
        assert binding["principals"][0] == "team:dst-team-id"
        assert result == []

    def test_connect_id_missing_user_returns_failed(self):
        """user: principal not in destination state is added to failed_connections."""
        state = {"users": {}, "roles": {}, "teams": {}}
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["user:missing-user-id"], "relation": "editor"}
        result = synthetics_tests.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "user:missing-user-id"
        assert result == ["missing-user-id"]

    def test_connect_id_skips_non_matching_type(self):
        """user: principal is not modified when resource_to_connect is roles."""
        state = {"users": {}, "roles": {}, "teams": {}}
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["user:some-user-id"], "relation": "editor"}
        result = synthetics_tests.connect_id("principals", binding, "roles")
        assert binding["principals"][0] == "user:some-user-id"
        assert result == []

    def test_connect_id_org_principal_passes_through_silently(self):
        """org: principal is not modified by connect_id and not added to failed_connections."""
        state = {"users": {}, "roles": {}, "teams": {}}
        synthetics_tests = self._make_synthetics_tests(state)
        binding = {"principals": ["org:some-org-id"], "relation": "editor"}
        result = synthetics_tests.connect_id("principals", binding, "users")
        assert binding["principals"][0] == "org:some-org-id"
        assert result == []

    def test_extract_source_ids_users(self):
        """extract_source_ids returns only user: IDs when resource_to_connect is users."""
        synthetics_tests = self._make_synthetics_tests()
        binding = {"principals": ["user:u1", "role:r1", "team:t1", "org:o1"], "relation": "editor"}
        ids = synthetics_tests.extract_source_ids("principals", binding, "users")
        assert ids == ["u1"]

    def test_extract_source_ids_roles(self):
        """extract_source_ids returns only role: IDs when resource_to_connect is roles."""
        synthetics_tests = self._make_synthetics_tests()
        binding = {"principals": ["user:u1", "role:r1", "role:r2", "team:t1"], "relation": "editor"}
        ids = synthetics_tests.extract_source_ids("principals", binding, "roles")
        assert ids == ["r1", "r2"]

    def test_extract_source_ids_teams(self):
        """extract_source_ids returns only team: IDs when resource_to_connect is teams."""
        synthetics_tests = self._make_synthetics_tests()
        binding = {"principals": ["user:u1", "team:t1"], "relation": "editor"}
        ids = synthetics_tests.extract_source_ids("principals", binding, "teams")
        assert ids == ["t1"]

    def test_extract_source_ids_org_excluded(self):
        """extract_source_ids never returns org: IDs — they're handled by pre_resource_action_hook."""
        synthetics_tests = self._make_synthetics_tests()
        binding = {"principals": ["org:src-org", "user:u1"], "relation": "editor"}
        assert synthetics_tests.extract_source_ids("principals", binding, "users") == ["u1"]
        assert synthetics_tests.extract_source_ids("principals", binding, "roles") == []
        assert synthetics_tests.extract_source_ids("principals", binding, "teams") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
