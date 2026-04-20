# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for sensitive_data_scanner_rules resource handling.

These tests cover two bugs fixed in DRALLSTSBX-53:
1. Standard pattern not found in destination → SkipResource instead of sending bad ID
2. Null included_keywords → stripped via non_nullable_attr before API call
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from datadog_sync.model.sensitive_data_scanner_rules import SensitiveDataScannerRules
from datadog_sync.utils.resource_utils import SkipResource, prep_resource


class TestSensitiveDataScannerRulesPreResourceActionHook:
    """Tests for Bug 1: standard pattern resolution in pre_resource_action_hook."""

    def _make_rules(self, destination_mapping=None):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        rules = SensitiveDataScannerRules(mock_config)
        rules.destination_standard_pattern_mapping = destination_mapping or {}
        return rules

    def test_standard_pattern_not_in_destination_raises_skip(self):
        """Rule referencing a standard pattern missing from destination should be skipped."""
        rules = self._make_rules(destination_mapping={})
        resource = {
            "id": "OfoIOTrPSw6Dix6xmeKUaA",
            "type": "sensitive_data_scanner_rule",
            "relationships": {
                "standard_pattern": {"data": {"id": "Visa Card Scanner (4x4 digits)", "type": "sensitive_data_scanner_standard_pattern"}}
            },
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(rules.pre_resource_action_hook("OfoIOTrPSw6Dix6xmeKUaA", resource))
        assert "Visa Card Scanner (4x4 digits)" in str(exc_info.value)

    def test_standard_pattern_found_in_destination_updates_id(self):
        """Rule with a standard pattern found in destination mapping should have ID replaced."""
        rules = self._make_rules(
            destination_mapping={"Visa Card Scanner (4x4 digits)": "dest-uuid-1234"}
        )
        resource = {
            "id": "OfoIOTrPSw6Dix6xmeKUaA",
            "type": "sensitive_data_scanner_rule",
            "relationships": {
                "standard_pattern": {"data": {"id": "Visa Card Scanner (4x4 digits)", "type": "sensitive_data_scanner_standard_pattern"}}
            },
        }
        asyncio.run(rules.pre_resource_action_hook("OfoIOTrPSw6Dix6xmeKUaA", resource))
        assert resource["relationships"]["standard_pattern"]["data"]["id"] == "dest-uuid-1234"

    def test_no_standard_pattern_relationship_does_not_skip(self):
        """Rule without a standard_pattern relationship should pass through unmodified."""
        rules = self._make_rules()
        resource = {
            "id": "3gZ518MZSUi5Xqb6dANefQ",
            "type": "sensitive_data_scanner_rule",
            "relationships": {
                "group": {"data": {"id": "some-group-id", "type": "sensitive_data_scanner_group"}}
            },
        }
        # Should not raise
        asyncio.run(rules.pre_resource_action_hook("3gZ518MZSUi5Xqb6dANefQ", resource))

    def test_standard_pattern_data_none_does_not_crash(self):
        """Rule with standard_pattern.data=None should not crash."""
        rules = self._make_rules()
        resource = {
            "id": "nulldata",
            "type": "sensitive_data_scanner_rule",
            "relationships": {
                "standard_pattern": {"data": None}
            },
        }
        # Should not raise (walrus operator on None.get() is guarded by dict chain)
        asyncio.run(rules.pre_resource_action_hook("nulldata", resource))

    def test_import_resource_standard_pattern_data_none_does_not_crash(self):
        """import_resource with standard_pattern.data=None should not crash with AttributeError."""
        rules = self._make_rules()
        rules.source_standard_pattern_mapping = {"some-id": "Some Pattern"}
        resource = {
            "id": "importnulldata",
            "type": "sensitive_data_scanner_rule",
            "attributes": {"name": "My Rule"},
            "relationships": {"standard_pattern": {"data": None}},
        }
        _id, result = asyncio.run(rules.import_resource(resource=resource))
        assert _id == "importnulldata"

    def test_multiple_standard_patterns_missing_raises_skip(self):
        """Ensures each rule independently raises SkipResource when its pattern is missing."""
        rules = self._make_rules(destination_mapping={"Present Pattern": "dest-id"})
        for rule_id, pattern_name in [
            ("rule1", "MasterCard Scanner (4x4 digits)"),
            ("rule2", "Standard Email Address Scanner"),
            ("rule3", "American Express Card Scanner (4+6+5 digits)"),
        ]:
            resource = {
                "id": rule_id,
                "type": "sensitive_data_scanner_rule",
                "relationships": {
                    "standard_pattern": {"data": {"id": pattern_name, "type": "sensitive_data_scanner_standard_pattern"}}
                },
            }
            with pytest.raises(SkipResource):
                asyncio.run(rules.pre_resource_action_hook(rule_id, resource))


class TestSensitiveDataScannerRulesNonNullableAttr:
    """Tests for Bug 2: null included_keywords is stripped via non_nullable_attr."""

    def test_non_nullable_attr_includes_included_keywords(self):
        """resource_config.non_nullable_attr should include 'attributes.included_keywords'."""
        assert "attributes.included_keywords" in (SensitiveDataScannerRules.resource_config.non_nullable_attr or [])

    def test_prep_resource_strips_null_included_keywords(self):
        """prep_resource() should remove included_keywords when null."""
        resource = {
            "id": "Av3sSWVPSY2gLNh_8tN9TA",
            "type": "sensitive_data_scanner_rule",
            "attributes": {
                "name": "My Rule",
                "included_keywords": None,
            },
        }
        prep_resource(SensitiveDataScannerRules.resource_config, resource)
        assert "included_keywords" not in resource["attributes"]

    def test_prep_resource_preserves_non_null_included_keywords(self):
        """prep_resource() should leave included_keywords intact when not null."""
        resource = {
            "id": "validrule",
            "type": "sensitive_data_scanner_rule",
            "attributes": {
                "name": "My Rule",
                "included_keywords": {"keywords": ["password", "secret"], "character_count": 10},
            },
        }
        prep_resource(SensitiveDataScannerRules.resource_config, resource)
        assert "included_keywords" in resource["attributes"]
        assert resource["attributes"]["included_keywords"]["keywords"] == ["password", "secret"]

    def test_prep_resource_handles_missing_attributes_key(self):
        """prep_resource() should not crash if 'attributes' key is missing."""
        resource = {
            "id": "noattrs",
            "type": "sensitive_data_scanner_rule",
        }
        # Should not raise
        prep_resource(SensitiveDataScannerRules.resource_config, resource)
