# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""security_monitoring_rules: no-false-positive guards for skip reconciliation.

Two SkipResource classes here must NOT trigger a destination-state write:
  (a) genuine non-existence: a non-default rule POSTs, gets a 400 with a
      skip-able "Invalid rule configuration" error, and raises SkipResource
      while the rule name is NOT in _existing_resources_map -> framework writes
      nothing (the rule genuinely does not exist at the destination);
  (b) pre-hook skip: "Default rule does not exist at destination" is raised in
      pre_resource_action_hook (before the _create_resource/_update_resource
      wrappers), so the framework never sees it -- documented as a boundary.

The immutable-rule update skip (inside update_resource) is also pinned:
insert-if-absent must preserve a pre-existing state.destination entry.

All identifiers are obviously synthetic (``rule-src``, ``rule-dst``).
"""

import asyncio
import json
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.security_monitoring_rules import SecurityMonitoringRules
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


class _FakeResponse:
    def __init__(self, status, message):
        self.status = status
        self.message = message


def _rule(name, is_default=False, rule_id=None, deprecated=False):
    return {
        "id": rule_id or f"rule-id-{name}",
        "name": name,
        "isDefault": is_default,
        "isDeprecated": deprecated,
        "version": 1,
        "queries": [],
        "cases": [],
        "options": {},
        "message": "synthetic test rule",
        "tags": [],
    }


def _make_rules(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    rules = SecurityMonitoringRules(config=config)
    rules._existing_resources_map = existing_map or {}
    return rules, config


class TestSecurityMonitoringSkipReconcile:
    def test_create_400_invalid_config_skip_does_not_write(self):
        # Non-default rule not in map -> POST -> 400 "Invalid rule configuration"
        # -> SkipResource. Rule name NOT in map -> framework must NOT write
        # (genuine non-existence; no false positive).
        _id = "rule-src"
        err = CustomClientHTTPError(
            _FakeResponse(400, "Bad Request"),
            json.dumps({"errors": ["Invalid rule configuration"]}),
        )
        destination_client = MagicMock()
        destination_client.post = AsyncMock(side_effect=err)
        rules, config = _make_rules(existing_map={}, destination_client=destination_client)
        resource = _rule("rule-src", is_default=False, rule_id=_id)

        with pytest.raises(SkipResource, match="Invalid rule configuration"):
            asyncio.run(rules._create_resource(_id, resource))

        assert config.state.destination["security_monitoring_rules"] == {}

    def test_pre_hook_default_not_exist_skip_not_reconciled(self):
        # "Default rule does not exist at destination" is raised in the pre-hook.
        # The handler now reconciles pre-hook skips, but only writes when the key
        # is in _existing_resources_map. Here the default rule is NOT in the map
        # (it genuinely doesn't exist at the destination), so reconcile no-ops
        # and state.destination stays empty. Pins the no-false-positive boundary.
        from datadog_sync.utils.resources_handler import ResourcesHandler

        _id = "rule-src"
        rules, config = _make_rules(existing_map={})
        resource = _rule("rule-src", is_default=True, rule_id=_id)
        config.resources = {"security_monitoring_rules": rules}
        config.state.source["security_monitoring_rules"][_id] = resource

        handler = ResourcesHandler(config)
        handler.worker = MagicMock()
        handler.worker.counter = MagicMock()
        handler.sorter = MagicMock()
        handler._emit = MagicMock()

        asyncio.run(handler._apply_resource_cb(["security_monitoring_rules", _id]))

        handler.worker.counter.increment_skipped.assert_called_once()
        # Key not in map -> reconcile no-ops -> state.destination stays empty.
        assert config.state.destination["security_monitoring_rules"] == {}

    def test_pre_hook_immutable_skip_reconciles_when_rule_in_map(self):
        # Immutable rule IS present in _existing_resources_map. The pre-hook raises
        # "This rule is immutable", but the rule exists on the destination, so the
        # handler-level reconcile must record it in state.destination (insert-if-
        # absent) to avoid the bucket-view false negative. This is the matching-map
        # regression case for the pre-hook skip path.
        from datadog_sync.utils.resources_handler import ResourcesHandler

        _id = "rule-src"
        dest_rule = _rule(
            "Impossible travel event leads to permission enumeration",
            is_default=True,
            rule_id="rule-dst",
        )
        rules, config = _make_rules(existing_map={dest_rule["name"]: dest_rule})
        resource = _rule(
            "Impossible travel event leads to permission enumeration",
            is_default=True,
            rule_id=_id,
        )
        config.resources = {"security_monitoring_rules": rules}
        config.state.source["security_monitoring_rules"][_id] = resource

        handler = ResourcesHandler(config)
        handler.worker = MagicMock()
        handler.worker.counter = MagicMock()
        handler.sorter = MagicMock()
        handler._emit = MagicMock()

        asyncio.run(handler._apply_resource_cb(["security_monitoring_rules", _id]))

        handler.worker.counter.increment_skipped.assert_called_once()
        handler.worker.counter.increment_failure.assert_not_called()
        # Rule was in the map -> reconciled into state.destination under source id.
        assert config.state.destination["security_monitoring_rules"][_id] == dest_rule

    def test_pre_hook_deprecated_skip_reconciles_when_rule_in_map(self):
        # Deprecated destination rule IS in the map. The pre-hook raises
        # "Cannot update deprecated rules", but the rule exists on the destination,
        # so the handler-level reconcile must record it in state.destination.
        from datadog_sync.utils.resources_handler import ResourcesHandler

        _id = "rule-src"
        dest_rule = _rule("rule-deprecated-test", is_default=False, rule_id="rule-dst", deprecated=True)
        rules, config = _make_rules(existing_map={dest_rule["name"]: dest_rule})
        resource = _rule("rule-deprecated-test", is_default=False, rule_id=_id)
        config.resources = {"security_monitoring_rules": rules}
        config.state.source["security_monitoring_rules"][_id] = resource

        handler = ResourcesHandler(config)
        handler.worker = MagicMock()
        handler.worker.counter = MagicMock()
        handler.sorter = MagicMock()
        handler._emit = MagicMock()

        asyncio.run(handler._apply_resource_cb(["security_monitoring_rules", _id]))

        handler.worker.counter.increment_skipped.assert_called_once()
        assert config.state.destination["security_monitoring_rules"][_id] == dest_rule

    def test_update_immutable_skip_preserves_state_destination(self):
        # Immutable rule is in the map; update_resource raises "This rule is
        # immutable". Insert-if-absent must leave the pre-existing entry untouched.
        _id = "rule-src"
        dest_rule = _rule(
            "Impossible travel event leads to permission enumeration", is_default=True, rule_id="rule-dst"
        )
        sentinel = _rule("Impossible travel event leads to permission enumeration", is_default=True, rule_id="rule-dst")
        sentinel["marker"] = "pre-existing"
        rules, config = _make_rules(existing_map={dest_rule["name"]: dest_rule})
        config.state.destination["security_monitoring_rules"][_id] = sentinel
        resource = _rule(dest_rule["name"], is_default=True, rule_id=_id)

        with pytest.raises(SkipResource, match="immutable"):
            asyncio.run(rules._update_resource(_id, resource))

        assert config.state.destination["security_monitoring_rules"][_id] is sentinel

    def test_create_non_existing_rule_still_posts(self):
        _id = "rule-src"
        posted = _rule("rule-src", is_default=False, rule_id="rule-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value=posted)
        rules, config = _make_rules(existing_map={}, destination_client=destination_client)
        resource = _rule("rule-src", is_default=False, rule_id=_id)

        out_id, out_r = asyncio.run(rules.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted

    def test_framework_reconciles_if_create_raised_skip(self):
        # Safety-net proof: if create_resource ever regressed into a
        # skip-without-write with the rule present in the map, the framework
        # wrapper would reconcile state.destination.
        _id = "rule-src"
        dest_rule = _rule("rule-src", is_default=False, rule_id="rule-dst")
        rules, config = _make_rules(existing_map={"rule-src": dest_rule})
        rules.create_resource = AsyncMock(side_effect=SkipResource(_id, "security_monitoring_rules", "exists"))
        resource = _rule("rule-src", is_default=False, rule_id=_id)

        with pytest.raises(SkipResource):
            asyncio.run(rules._create_resource(_id, resource))

        assert config.state.destination["security_monitoring_rules"][_id] == dest_rule
