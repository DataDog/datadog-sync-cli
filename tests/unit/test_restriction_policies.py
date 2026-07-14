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
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.restriction_policies import RestrictionPolicies
from datadog_sync.utils.resource_utils import SkipResource


class TestRestrictionPoliciesOrgPrincipal:
    """Test suite for org: principal remapping in restriction_policies."""

    def _make_resource(self):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        return RestrictionPolicies(mock_config)

    def _seed_source_with_policy(self, resource):
        """Populate source state with one restriction policy resource."""
        resource.config.state.source = {"restriction_policies": {"some-id": {"attributes": {"bindings": []}}}}

    def _seed_source_without_policy(self, resource):
        """Populate source state with no restriction policy resources."""
        resource.config.state.source = {"restriction_policies": {}}

    def test_pre_apply_hook_sets_org_principal_on_success(self):
        """Successful GET /api/v2/current_user sets org_principal to 'org:{org_uuid}'."""
        resource = self._make_resource()
        self._seed_source_with_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={"data": {"relationships": {"org": {"data": {"id": "00000000-0000-beef-0000-000000000000"}}}}}
        )
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal == "org:00000000-0000-beef-0000-000000000000"
        mock_client.get.assert_awaited_once()

    def test_pre_apply_hook_leaves_org_principal_none_on_failure(self):
        """Failed GET /api/v2/current_user leaves org_principal as None and raises."""
        resource = self._make_resource()
        self._seed_source_with_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("403 Forbidden"))
        resource.config.destination_client = mock_client

        with pytest.raises(Exception, match="403 Forbidden"):
            asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None

    def test_pre_apply_hook_skips_current_user_when_source_empty(self):
        """Empty source state → GET is not called; org_principal stays None."""
        resource = self._make_resource()
        self._seed_source_without_policy(resource)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        resource.config.destination_client = mock_client

        asyncio.run(resource.pre_apply_hook())

        assert resource.org_principal is None
        mock_client.get.assert_not_awaited()

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


class TestRestrictionPoliciesBulkSource:
    """Verify --restriction-policies-bulk-source short-circuits per-ID GETs."""

    def _make_resource(self, bulk_source=None):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.restriction_policies_bulk_source = bulk_source
        return RestrictionPolicies(mock_config)

    def _body(self, policy_id, bindings=None):
        return {
            "id": policy_id,
            "type": "restriction_policy",
            "attributes": {
                "bindings": bindings
                if bindings is not None
                else [{"relation": "editor", "principals": ["role:r1"]}]
            },
        }

    def test_get_resources_flag_off_uses_per_dependency_path(self):
        """Flag unset = existing enumerate-from-dashboards/notebooks/SLOs behavior."""
        resource = self._make_resource(bulk_source=None)
        mock_dashboards = MagicMock()
        mock_dashboards.get_resources = AsyncMock(return_value=[{"id": "d1"}])
        mock_notebooks = MagicMock()
        mock_notebooks.get_resources = AsyncMock(return_value=[])
        mock_slos = MagicMock()
        mock_slos.get_resources = AsyncMock(return_value=[])
        resource.config.resources = {
            "dashboards": mock_dashboards,
            "notebooks": mock_notebooks,
            "service_level_objectives": mock_slos,
        }

        result = asyncio.run(resource.get_resources(MagicMock()))

        assert result == [{"id": "dashboard:d1"}]
        mock_dashboards.get_resources.assert_awaited_once()

    def test_get_resources_flag_on_reads_file_and_skips_enumeration(self, tmp_path):
        """Flag set = file is the source of truth; dependency models are not queried.

        Note: directly asserts the dependency `get_resources` awaitables are never
        awaited rather than relying on MagicMock side_effect on the parent container
        (which would not fire on __getitem__ access, only on __call__).
        """
        bodies = [
            self._body("dashboard:abc"),
            self._body("notebook:def"),
            self._body("slo:ghi"),
        ]
        path = tmp_path / "policies.json"
        path.write_text(json.dumps(bodies))

        resource = self._make_resource(bulk_source=str(path))
        mock_dashboards = MagicMock()
        mock_dashboards.get_resources = AsyncMock(side_effect=AssertionError("must not enumerate dashboards"))
        mock_notebooks = MagicMock()
        mock_notebooks.get_resources = AsyncMock(side_effect=AssertionError("must not enumerate notebooks"))
        mock_slos = MagicMock()
        mock_slos.get_resources = AsyncMock(side_effect=AssertionError("must not enumerate slos"))
        resource.config.resources = {
            "dashboards": mock_dashboards,
            "notebooks": mock_notebooks,
            "service_level_objectives": mock_slos,
        }

        result = asyncio.run(resource.get_resources(MagicMock()))

        assert result == bodies
        mock_dashboards.get_resources.assert_not_awaited()
        mock_notebooks.get_resources.assert_not_awaited()
        mock_slos.get_resources.assert_not_awaited()

    def test_import_resource_short_circuits_on_prefetched_body(self):
        """resource arg with `attributes` returns as-is; source_client.get is not called."""
        resource = self._make_resource()
        mock_client = AsyncMock()
        resource.config.source_client = mock_client

        body = self._body("dashboard:abc")
        import_id, returned = asyncio.run(resource.import_resource(resource=body))

        assert import_id == "dashboard:abc"
        assert returned is body
        mock_client.get.assert_not_called()

    def test_import_resource_prefetched_empty_bindings_raises_skip(self):
        """Empty-bindings SkipResource semantics match the per-ID GET path."""
        resource = self._make_resource()
        body = self._body("dashboard:abc", bindings=[])

        with pytest.raises(SkipResource):
            asyncio.run(resource.import_resource(resource=body))

    def test_import_resource_no_attributes_falls_through_to_get(self):
        """Stub-only resource (no `attributes`) goes through the existing GET path."""
        resource = self._make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={
                "data": {
                    "id": "dashboard:abc",
                    "type": "restriction_policy",
                    "attributes": {"bindings": [{"relation": "editor", "principals": ["role:r1"]}]},
                }
            }
        )
        resource.config.source_client = mock_client

        import_id, returned = asyncio.run(resource.import_resource(resource={"id": "dashboard:abc"}))

        assert import_id == "dashboard:abc"
        mock_client.get.assert_awaited_once()
        assert returned["attributes"]["bindings"][0]["relation"] == "editor"

    def test_import_resource_attributes_without_bindings_falls_through_to_get(self):
        """Defensive: if a future legacy LIST stub ever carried `attributes` without a
        `bindings` list, the bulk short-circuit must not engage. The stronger discriminator
        (bindings is a list) keeps the short-circuit pinned to the validator's exact shape.
        """
        resource = self._make_resource()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value={
                "data": {
                    "id": "dashboard:abc",
                    "type": "restriction_policy",
                    "attributes": {"bindings": [{"relation": "editor", "principals": ["role:r1"]}]},
                }
            }
        )
        resource.config.source_client = mock_client

        # Stub-with-attributes-but-no-bindings (hypothetical future enriched LIST stub):
        # discriminator must not engage and code must GET the real body.
        stub_with_partial_attributes = {"id": "dashboard:abc", "attributes": {"type_hint": "dashboard"}}
        import_id, returned = asyncio.run(resource.import_resource(resource=stub_with_partial_attributes))

        assert import_id == "dashboard:abc"
        mock_client.get.assert_awaited_once()
        assert returned["attributes"]["bindings"][0]["relation"] == "editor"

    def test_load_bulk_source_missing_file_raises(self, tmp_path):
        resource = self._make_resource(bulk_source=str(tmp_path / "absent.json"))
        with pytest.raises(RuntimeError, match="failed to load"):
            resource._load_bulk_source(str(tmp_path / "absent.json"))

    def test_load_bulk_source_non_list_raises(self, tmp_path):
        path = tmp_path / "object.json"
        path.write_text(json.dumps({"id": "dashboard:abc"}))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match="expected JSON array"):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_missing_attributes_raises(self, tmp_path):
        path = tmp_path / "malformed.json"
        path.write_text(json.dumps([{"id": "dashboard:abc", "type": "restriction_policy"}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='"attributes" as an object'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_empty_list_is_valid(self, tmp_path):
        """An empty prefetch is legitimate (org has no restriction policies)."""
        path = tmp_path / "empty.json"
        path.write_text("[]")
        resource = self._make_resource(bulk_source=str(path))
        assert resource._load_bulk_source(str(path)) == []

    def test_load_bulk_source_non_dict_entry_raises(self, tmp_path):
        path = tmp_path / "bad_entry.json"
        path.write_text(json.dumps(["not-a-dict"]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match="must be a JSON object"):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_non_string_id_raises(self, tmp_path):
        path = tmp_path / "bad_id.json"
        path.write_text(json.dumps([{"id": 123, "type": "restriction_policy", "attributes": {"bindings": []}}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='non-empty string "id"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_empty_id_raises(self, tmp_path):
        path = tmp_path / "empty_id.json"
        path.write_text(json.dumps([{"id": "", "type": "restriction_policy", "attributes": {"bindings": []}}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='non-empty string "id"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_id_missing_separator_raises(self, tmp_path):
        path = tmp_path / "no_colon.json"
        path.write_text(json.dumps([{"id": "garbage", "type": "restriction_policy", "attributes": {"bindings": []}}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='malformed "id"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_id_empty_type_side_raises(self, tmp_path):
        """An id like ':abc' has an empty type prefix and must be rejected."""
        path = tmp_path / "empty_type.json"
        path.write_text(json.dumps([{"id": ":abc", "type": "restriction_policy", "attributes": {"bindings": []}}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='malformed "id"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_id_empty_resource_side_raises(self, tmp_path):
        """An id like 'dashboard:' has an empty resource-id side and must be rejected."""
        path = tmp_path / "empty_resource.json"
        path.write_text(
            json.dumps([{"id": "dashboard:", "type": "restriction_policy", "attributes": {"bindings": []}}])
        )
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='malformed "id"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_unsupported_id_prefix_raises(self, tmp_path):
        """An id with a prefix outside the legacy supported set must be rejected."""
        path = tmp_path / "unsupported_prefix.json"
        path.write_text(
            json.dumps(
                [{"id": "security-rule:abc", "type": "restriction_policy", "attributes": {"bindings": []}}]
            )
        )
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='unsupported "id" prefix'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_wrong_type_raises(self, tmp_path):
        path = tmp_path / "wrong_type.json"
        path.write_text(
            json.dumps([{"id": "dashboard:abc", "type": "something_else", "attributes": {"bindings": []}}])
        )
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='"type" == "restriction_policy"'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_attributes_null_raises(self, tmp_path):
        path = tmp_path / "null_attrs.json"
        path.write_text(json.dumps([{"id": "dashboard:abc", "type": "restriction_policy", "attributes": None}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='"attributes" as an object'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_bindings_missing_raises(self, tmp_path):
        path = tmp_path / "no_bindings.json"
        path.write_text(json.dumps([{"id": "dashboard:abc", "type": "restriction_policy", "attributes": {}}]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='"attributes.bindings" as an array'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_bindings_non_list_raises(self, tmp_path):
        path = tmp_path / "bad_bindings.json"
        path.write_text(
            json.dumps([{"id": "dashboard:abc", "type": "restriction_policy", "attributes": {"bindings": "x"}}])
        )
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match='"attributes.bindings" as an array'):
            resource._load_bulk_source(str(path))

    def test_load_bulk_source_empty_bindings_list_is_valid(self, tmp_path):
        """Empty bindings list is allowed at load time; SkipResource fires later in import_resource."""
        path = tmp_path / "empty_bindings.json"
        body = {"id": "dashboard:abc", "type": "restriction_policy", "attributes": {"bindings": []}}
        path.write_text(json.dumps([body]))
        resource = self._make_resource(bulk_source=str(path))
        assert resource._load_bulk_source(str(path)) == [body]

    def test_load_bulk_source_duplicate_id_raises(self, tmp_path):
        """Duplicate ids would be last-wins overwrites in state — surface them at load time."""
        path = tmp_path / "duplicate_id.json"
        body = {"id": "dashboard:abc", "type": "restriction_policy", "attributes": {"bindings": []}}
        path.write_text(json.dumps([body, body]))
        resource = self._make_resource(bulk_source=str(path))
        with pytest.raises(RuntimeError, match="duplicate \"id\""):
            resource._load_bulk_source(str(path))
