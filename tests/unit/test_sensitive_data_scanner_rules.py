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
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.constants import Metrics
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
                "standard_pattern": {
                    "data": {"id": "Visa Card Scanner (4x4 digits)", "type": "sensitive_data_scanner_standard_pattern"}
                }
            },
        }
        with pytest.raises(SkipResource) as exc_info:
            asyncio.run(rules.pre_resource_action_hook("OfoIOTrPSw6Dix6xmeKUaA", resource))
        assert "Visa Card Scanner (4x4 digits)" in str(exc_info.value)

    def test_standard_pattern_found_in_destination_updates_id(self):
        """Rule with a standard pattern found in destination mapping should have ID replaced."""
        rules = self._make_rules(destination_mapping={"Visa Card Scanner (4x4 digits)": "dest-uuid-1234"})
        resource = {
            "id": "OfoIOTrPSw6Dix6xmeKUaA",
            "type": "sensitive_data_scanner_rule",
            "relationships": {
                "standard_pattern": {
                    "data": {"id": "Visa Card Scanner (4x4 digits)", "type": "sensitive_data_scanner_standard_pattern"}
                }
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
            "relationships": {"group": {"data": {"id": "some-group-id", "type": "sensitive_data_scanner_group"}}},
        }
        # Should not raise
        asyncio.run(rules.pre_resource_action_hook("3gZ518MZSUi5Xqb6dANefQ", resource))

    def test_standard_pattern_data_none_does_not_crash(self):
        """Rule with standard_pattern.data=None should not crash."""
        rules = self._make_rules()
        resource = {
            "id": "nulldata",
            "type": "sensitive_data_scanner_rule",
            "relationships": {"standard_pattern": {"data": None}},
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
                    "standard_pattern": {
                        "data": {"id": pattern_name, "type": "sensitive_data_scanner_standard_pattern"}
                    }
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


class TestSensitiveDataScannerRulesCanonicalNameRewrite:
    """Rewrites attributes.name to the linked standard pattern's canonical
    name on write, since the destination CREATE validator rejects a
    non-matching name. Emits a metric per rewrite for audit. Applied only
    on create/update (not diffs/import) so source state stays untouched."""

    # By the time _align_name_with_standard_pattern runs (from create/update),
    # pre_resource_action_hook has already replaced data.id with the destination
    # pattern uuid. So test inputs use the destination uuid and rely on
    # destination_standard_pattern_mapping (name -> id) for reverse lookup.
    VISA_NAME = "Visa Card Scanner (4x4 digits)"
    VISA_DEST_ID = "dest-visa-uuid"
    MC_NAME = "MasterCard Scanner (4x4 digits)"
    MC_DEST_ID = "dest-mc-uuid"
    EMAIL_NAME = "Email Address Scanner"
    EMAIL_DEST_ID = "dest-email-uuid"

    def _make_rules(self, mapping=None):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.destination_client = MagicMock()
        mock_config.destination_client.send_metric = AsyncMock()
        rules = SensitiveDataScannerRules(mock_config)
        rules.destination_standard_pattern_mapping = (
            mapping
            if mapping is not None
            else {
                self.VISA_NAME: self.VISA_DEST_ID,
                self.MC_NAME: self.MC_DEST_ID,
                self.EMAIL_NAME: self.EMAIL_DEST_ID,
            }
        )
        return rules

    def _resource(self, _id, name, pattern_dest_id):
        return {
            "id": _id,
            "type": "sensitive_data_scanner_rule",
            "attributes": {"name": name},
            "relationships": {
                "standard_pattern": {"data": {"id": pattern_dest_id, "type": "sensitive_data_scanner_standard_pattern"}}
            },
        }

    def test_align_rewrites_name_when_mismatched(self):
        rules = self._make_rules()
        resource = self._resource("rule-1", "Custom Visa Scanner", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-1", resource))
        assert resource["attributes"]["name"] == self.VISA_NAME

    def test_align_noop_when_name_matches(self):
        rules = self._make_rules()
        resource = self._resource("rule-2", self.EMAIL_NAME, self.EMAIL_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-2", resource))
        assert resource["attributes"]["name"] == self.EMAIL_NAME
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_source_name_is_empty(self):
        rules = self._make_rules()
        resource = self._resource("rule-3", "", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-3", resource))
        assert resource["attributes"]["name"] == ""
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_no_standard_pattern(self):
        rules = self._make_rules()
        resource = {
            "id": "custom",
            "type": "sensitive_data_scanner_rule",
            "attributes": {"name": "Custom SSN"},
            "relationships": {"group": {"data": {"id": "grp"}}},
        }
        asyncio.run(rules._align_with_standard_pattern("custom", resource))
        assert resource["attributes"]["name"] == "Custom SSN"
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_pattern_id_not_in_destination_mapping(self):
        # If we cannot resolve the canonical name, do not touch the name —
        # do not rewrite it to the raw destination uuid.
        rules = self._make_rules(mapping={})
        resource = self._resource("rule-x", "Custom Visa", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-x", resource))
        assert resource["attributes"]["name"] == "Custom Visa"
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_emits_metric_with_expected_tags(self):
        rules = self._make_rules()
        resource = self._resource("rule-4", "Custom MC", self.MC_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-4", resource))
        rules.config.destination_client.send_metric.assert_awaited_once()
        metric_name, tags = rules.config.destination_client.send_metric.await_args.args
        assert metric_name == Metrics.ACTION.value
        assert "id:rule-4" in tags
        assert "resource_type:sensitive_data_scanner_rules" in tags
        assert "action_type:sync" in tags
        assert "action_sub_type:standard_pattern_name_rewrite" in tags
        assert "status:success" in tags
        assert "client_type:destination" in tags
        assert f"pattern:{self.MC_NAME}" in tags

    def test_align_tolerates_metric_failure(self):
        rules = self._make_rules()
        rules.config.destination_client.send_metric = AsyncMock(side_effect=Exception("metric down"))
        resource = self._resource("rule-5", "Custom Visa", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-5", resource))
        assert resource["attributes"]["name"] == self.VISA_NAME

    def test_pre_resource_action_hook_does_not_rewrite_name(self):
        # The hook runs on the diffs path against a live reference into
        # state.source (resources_handler.py:571 — no deepcopy). It must
        # not mutate attributes.name; the rewrite happens in create/update.
        rules = self._make_rules()
        # In the diffs path the pattern data.id is still the source-side name.
        resource = self._resource("rule-6", "Custom Visa", self.VISA_NAME)
        asyncio.run(rules.pre_resource_action_hook("rule-6", resource))
        assert resource["attributes"]["name"] == "Custom Visa"
        assert resource["relationships"]["standard_pattern"]["data"]["id"] == self.VISA_DEST_ID
        rules.config.destination_client.send_metric.assert_not_called()

    def test_create_path_yields_canonical_pattern_name_not_uuid(self):
        # End-to-end orchestration: pre_resource_action_hook translates
        # data.id source-name -> destination-uuid, then create_resource
        # calls _align_name_with_standard_pattern which must resolve
        # canonical NAME (not the uuid) as the target attributes.name.
        rules = self._make_rules()
        rules.config.destination_client.post = AsyncMock(return_value={"data": {"id": "created"}})
        # Post-import shape: data.id holds the source pattern's canonical name.
        resource = self._resource("rule-e2e", "Custom Visa Scanner", self.VISA_NAME)
        asyncio.run(rules.pre_resource_action_hook("rule-e2e", resource))
        asyncio.run(rules.create_resource("rule-e2e", resource))
        # attributes.name must be the canonical pattern NAME, not the destination uuid.
        assert resource["attributes"]["name"] == self.VISA_NAME
        assert resource["attributes"]["name"] != self.VISA_DEST_ID


class TestSensitiveDataScannerRulesCanonicalDescriptionRewrite:
    """Rewrites attributes.description to the linked standard pattern's
    canonical description on write, since the destination API rejects a
    standard-pattern-linked rule whose description does not match the
    linked destination pattern's description (HTTP 400 'description of the
    standard rule and the rule must match'). Mirrors the name-rewrite
    behavior. Emits a metric per rewrite for audit. Applied only on
    create/update (not diffs/import) so source state stays untouched."""

    # By the time _align_with_standard_pattern runs (from create/update),
    # pre_resource_action_hook has already replaced data.id with the
    # destination pattern uuid. So test inputs use the destination uuid and
    # rely on destination_standard_pattern_mapping (name -> id) for reverse
    # lookup of the name, and destination_standard_pattern_description_mapping
    # (id -> description) for the canonical description.
    VISA_NAME = "Visa Card Scanner (4x4 digits)"
    VISA_DEST_ID = "dest-visa-uuid"
    VISA_DESC = "Matches a sequence of characters representing a Visa card number."
    MC_NAME = "MasterCard Scanner (4x4 digits)"
    MC_DEST_ID = "dest-mc-uuid"
    MC_DESC = "Matches a sequence of characters representing a MasterCard number."
    EMAIL_NAME = "Email Address Scanner"
    EMAIL_DEST_ID = "dest-email-uuid"
    EMAIL_DESC = "Matches a sequence of characters representing an email address."

    def _make_rules(self, name_mapping=None, desc_mapping=None):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.destination_client = MagicMock()
        mock_config.destination_client.send_metric = AsyncMock()
        rules = SensitiveDataScannerRules(mock_config)
        rules.destination_standard_pattern_mapping = (
            name_mapping
            if name_mapping is not None
            else {
                self.VISA_NAME: self.VISA_DEST_ID,
                self.MC_NAME: self.MC_DEST_ID,
                self.EMAIL_NAME: self.EMAIL_DEST_ID,
            }
        )
        rules.destination_standard_pattern_description_mapping = (
            desc_mapping
            if desc_mapping is not None
            else {
                self.VISA_DEST_ID: self.VISA_DESC,
                self.MC_DEST_ID: self.MC_DESC,
                self.EMAIL_DEST_ID: self.EMAIL_DESC,
            }
        )
        return rules

    def _resource(self, _id, description, pattern_dest_id, name="Custom Rule"):
        return {
            "id": _id,
            "type": "sensitive_data_scanner_rule",
            "attributes": {"name": name, "description": description},
            "relationships": {
                "standard_pattern": {"data": {"id": pattern_dest_id, "type": "sensitive_data_scanner_standard_pattern"}}
            },
        }

    def test_align_rewrites_description_when_mismatched(self):
        rules = self._make_rules()
        resource = self._resource("rule-1", "custom desc", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-1", resource))
        assert resource["attributes"]["description"] == self.VISA_DESC

    def test_align_noop_when_description_matches(self):
        rules = self._make_rules()
        resource = self._resource("rule-2", self.EMAIL_DESC, self.EMAIL_DEST_ID, name=self.EMAIL_NAME)
        asyncio.run(rules._align_with_standard_pattern("rule-2", resource))
        assert resource["attributes"]["description"] == self.EMAIL_DESC
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_description_empty_and_pattern_empty(self):
        rules = self._make_rules(
            desc_mapping={self.VISA_DEST_ID: ""},
        )
        resource = self._resource("rule-3", "", self.VISA_DEST_ID, name=self.VISA_NAME)
        asyncio.run(rules._align_with_standard_pattern("rule-3", resource))
        assert resource["attributes"]["description"] == ""
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_no_standard_pattern(self):
        rules = self._make_rules()
        resource = {
            "id": "custom",
            "type": "sensitive_data_scanner_rule",
            "attributes": {"name": "Custom SSN", "description": "custom ssn desc"},
            "relationships": {"group": {"data": {"id": "grp"}}},
        }
        asyncio.run(rules._align_with_standard_pattern("custom", resource))
        assert resource["attributes"]["description"] == "custom ssn desc"
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_noop_when_pattern_id_not_in_destination_mapping(self):
        # If we cannot resolve the canonical description, do not touch the
        # description — do not rewrite it to None.
        rules = self._make_rules(name_mapping={self.VISA_NAME: self.VISA_DEST_ID}, desc_mapping={})
        resource = self._resource("rule-x", "Custom Visa desc", self.VISA_DEST_ID, name=self.VISA_NAME)
        asyncio.run(rules._align_with_standard_pattern("rule-x", resource))
        assert resource["attributes"]["description"] == "Custom Visa desc"
        rules.config.destination_client.send_metric.assert_not_called()

    def test_align_emits_description_metric_with_expected_tags(self):
        rules = self._make_rules()
        resource = self._resource("rule-4", "custom mc desc", self.MC_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-4", resource))
        rules.config.destination_client.send_metric.assert_awaited()
        # Both name (if mismatched) and description metrics may fire; find the
        # description one.
        calls = rules.config.destination_client.send_metric.await_args_list
        desc_calls = [c for c in calls if "action_sub_type:standard_pattern_description_rewrite" in c.args[1]]
        assert len(desc_calls) == 1
        metric_name, tags = desc_calls[0].args
        assert metric_name == Metrics.ACTION.value
        assert "id:rule-4" in tags
        assert "resource_type:sensitive_data_scanner_rules" in tags
        assert "action_type:sync" in tags
        assert "action_sub_type:standard_pattern_description_rewrite" in tags
        assert "status:success" in tags
        assert "client_type:destination" in tags
        assert f"pattern:{self.MC_NAME}" in tags

    def test_align_tolerates_description_metric_failure(self):
        rules = self._make_rules()
        rules.config.destination_client.send_metric = AsyncMock(side_effect=Exception("metric down"))
        resource = self._resource("rule-5", "custom visa desc", self.VISA_DEST_ID)
        asyncio.run(rules._align_with_standard_pattern("rule-5", resource))
        assert resource["attributes"]["description"] == self.VISA_DESC

    def test_create_path_yields_canonical_description(self):
        # End-to-end: pre_resource_action_hook translates data.id
        # source-name -> destination-uuid, then create_resource calls
        # _align_with_standard_pattern which must set attributes.description
        # to the destination pattern's canonical description.
        rules = self._make_rules()
        rules.config.destination_client.post = AsyncMock(return_value={"data": {"id": "created"}})
        # Post-import shape: data.id holds the source pattern's canonical name.
        resource = self._resource("rule-e2e", "source-side visa desc", self.VISA_NAME, name=self.VISA_NAME)
        asyncio.run(rules.pre_resource_action_hook("rule-e2e", resource))
        asyncio.run(rules.create_resource("rule-e2e", resource))
        assert resource["attributes"]["description"] == self.VISA_DESC

    def test_align_rewrites_both_name_and_description_when_both_mismatched(self):
        rules = self._make_rules()
        resource = self._resource("rule-both", "custom desc", self.MC_DEST_ID, name="Custom MC")
        asyncio.run(rules._align_with_standard_pattern("rule-both", resource))
        assert resource["attributes"]["name"] == self.MC_NAME
        assert resource["attributes"]["description"] == self.MC_DESC
        calls = rules.config.destination_client.send_metric.await_args_list
        sub_types = {tag for c in calls for tag in c.args[1] if tag.startswith("action_sub_type:")}
        assert "action_sub_type:standard_pattern_name_rewrite" in sub_types
        assert "action_sub_type:standard_pattern_description_rewrite" in sub_types


class TestSensitiveDataScannerRulesPreApplyHookPartialCache:
    """pre_apply_hook must repopulate when EITHER mapping is empty, so a
    partial-cache state (name mapping present, description mapping empty)
    cannot skip description initialization."""

    VISA_NAME = "Visa Card Scanner (4x4 digits)"
    VISA_DEST_ID = "dest-visa-uuid"
    VISA_DESC = "Matches a sequence of characters representing a Visa card number."

    def _make_rules(self, name_mapping=None, desc_mapping=None):
        mock_config = MagicMock()
        mock_config.state = MagicMock()
        mock_config.destination_client = MagicMock()
        mock_config.destination_client.get = AsyncMock(
            return_value={
                "data": [
                    {
                        "id": self.VISA_DEST_ID,
                        "type": "sensitive_data_scanner_standard_pattern",
                        "attributes": {"name": self.VISA_NAME, "description": self.VISA_DESC},
                    }
                ]
            }
        )
        rules = SensitiveDataScannerRules(mock_config)
        rules.destination_standard_pattern_mapping = name_mapping if name_mapping is not None else {}
        rules.destination_standard_pattern_description_mapping = desc_mapping if desc_mapping is not None else {}
        return rules

    def test_repopulates_when_both_mappings_empty(self):
        rules = self._make_rules(name_mapping={}, desc_mapping={})
        asyncio.run(rules.pre_apply_hook())
        assert rules.destination_standard_pattern_mapping == {self.VISA_NAME: self.VISA_DEST_ID}
        assert rules.destination_standard_pattern_description_mapping == {self.VISA_DEST_ID: self.VISA_DESC}

    def test_repopulates_when_only_name_mapping_present(self):
        # Partial cache: name mapping populated, description mapping empty.
        # Must still re-fetch so description mapping is initialized.
        rules = self._make_rules(
            name_mapping={self.VISA_NAME: self.VISA_DEST_ID},
            desc_mapping={},
        )
        asyncio.run(rules.pre_apply_hook())
        assert rules.destination_standard_pattern_description_mapping == {self.VISA_DEST_ID: self.VISA_DESC}

    def test_repopulates_when_only_description_mapping_present(self):
        # Partial cache: description mapping populated, name mapping empty.
        rules = self._make_rules(
            name_mapping={},
            desc_mapping={self.VISA_DEST_ID: self.VISA_DESC},
        )
        asyncio.run(rules.pre_apply_hook())
        assert rules.destination_standard_pattern_mapping == {self.VISA_NAME: self.VISA_DEST_ID}

    def test_skips_refetch_when_both_mappings_populated(self):
        rules = self._make_rules(
            name_mapping={self.VISA_NAME: self.VISA_DEST_ID},
            desc_mapping={self.VISA_DEST_ID: self.VISA_DESC},
        )
        asyncio.run(rules.pre_apply_hook())
        rules.config.destination_client.get.assert_not_called()
