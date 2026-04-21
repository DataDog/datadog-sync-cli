# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for restriction_policies resource handling.

These tests verify that the org: principal remapping in pre_apply_hook and
pre_resource_action_hook handles both success and failure paths correctly.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.restriction_policies import RestrictionPolicies


class TestRestrictionPoliciesOrgPrincipal:
    """Test suite for org: principal remapping in restriction_policies."""

    def _make_resource(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return RestrictionPolicies(mock_config)

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Successful GET /api/v2/current_user sets org_principal to 'org:{org_uuid}'."""
        resource = self._make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={"data": {"relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}}}}
        )
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal == "org:00000000-0000-beef-0000-000000000000"

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Failed GET /api/v2/current_user leaves org_principal as None and raises."""
        resource = self._make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        resource.config.destination_client = mock_client

        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None

    def test_pre_resource_action_hook_replaces_org_when_principal_set(self):
        """When org_principal is set, org: entries in bindings are replaced."""
        resource = self._make_resource()
        resource.org_principal = "org:dest-pub-id"

        r = {
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]}
        }
        asyncio.run(resource.pre_resource_action_hook("some-id", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:dest-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"

    def test_pre_resource_action_hook_skips_when_org_principal_none(self):
        """When org_principal is None, org: principals are left unchanged."""
        resource = self._make_resource()
        assert resource.org_principal is None

        r = {
            "attributes": {"bindings": [{"principals": ["org:source-pub-id", "user:some-user"], "relation": "editor"}]}
        }
        asyncio.run(resource.pre_resource_action_hook("some-id", r))

        assert r["attributes"]["bindings"][0]["principals"][0] == "org:source-pub-id"
        assert r["attributes"]["bindings"][0]["principals"][1] == "user:some-user"
