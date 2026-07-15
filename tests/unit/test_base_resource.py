# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for BaseResource._resolve_or_drop.

_resolve_or_drop is the shared three-way resolver behind --drop-unresolvable-principals:
it decides whether a plain dependency id resolves against destination state, is a
"not yet synced" source-present miss, or is a permanently-gone (source-absent) stale
reference. It reads only self.config, so we exercise it as an unbound method with a
stub self rather than instantiating the abstract BaseResource.
"""

from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from datadog_sync.utils.base_resource import BaseResource, ResourceConnectionResult


def _make_config():
    config = MagicMock()
    state = MagicMock()
    state.source = defaultdict(dict)
    state.destination = defaultdict(dict)
    state.ensure_resource_loaded = MagicMock()
    config.state = state
    return config


def _call(config, plain_id, resource_to_connect):
    stub = SimpleNamespace(config=config)
    return BaseResource._resolve_or_drop(stub, plain_id, resource_to_connect)


def test_connect_resources_returns_named_default_result_when_no_connections():
    stub = SimpleNamespace(resource_config=SimpleNamespace(resource_connections=None))

    result = BaseResource.connect_resources(stub, "resource-id", {})

    assert result == ResourceConnectionResult()
    assert result.empty_binding_escalation is False


def test_resolve_or_drop_destination_present_returns_dest_id_not_stale():
    config = _make_config()
    config.state.destination["roles"]["src-role"] = {"id": "dst-role"}

    resolved, stale = _call(config, "src-role", "roles")

    assert resolved == "dst-role"
    assert stale is False
    # No need to consult source when destination already has it.
    config.state.ensure_resource_loaded.assert_not_called()


def test_resolve_or_drop_rechecks_destination_after_lazy_load_populates_both_states():
    config = _make_config()

    def load_role(resource_type, plain_id):
        config.state.source[resource_type][plain_id] = {"id": plain_id}
        config.state.destination[resource_type][plain_id] = {"id": "dst-role"}

    config.state.ensure_resource_loaded.side_effect = load_role

    resolved, stale = _call(config, "src-role", "roles")

    assert resolved == "dst-role"
    assert stale is False
    config.state.ensure_resource_loaded.assert_called_once_with("roles", "src-role")


def test_resolve_or_drop_rechecks_destination_after_lazy_load_populates_destination_only():
    config = _make_config()

    def load_role(resource_type, plain_id):
        config.state.destination[resource_type][plain_id] = {"id": "dst-role"}

    config.state.ensure_resource_loaded.side_effect = load_role

    resolved, stale = _call(config, "src-role", "roles")

    assert resolved == "dst-role"
    assert stale is False
    config.state.ensure_resource_loaded.assert_called_once_with("roles", "src-role")


def test_resolve_or_drop_source_present_not_synced_returns_none_not_stale():
    config = _make_config()
    config.state.source["roles"]["src-role"] = {"id": "src-role"}

    resolved, stale = _call(config, "src-role", "roles")

    assert resolved is None
    assert stale is False  # legitimate "not yet synced" -> caller hard-fails, does NOT drop
    config.state.ensure_resource_loaded.assert_called_once_with("roles", "src-role")


def test_resolve_or_drop_absent_from_both_is_permanently_stale():
    config = _make_config()

    resolved, stale = _call(config, "ghost-role", "roles")

    assert resolved is None
    assert stale is True  # absent from destination AND source -> permanently gone
    config.state.ensure_resource_loaded.assert_called_once_with("roles", "ghost-role")


def test_resolve_or_drop_unknown_resource_type_does_not_raise():
    # state.destination/source are defaultdict(dict); an unknown type yields {} not KeyError.
    config = _make_config()

    resolved, stale = _call(config, "whatever", "never_seen_type")

    assert resolved is None
    assert stale is True


def test_resolve_or_drop_propagates_ensure_resource_loaded_exception():
    config = _make_config()
    config.state.ensure_resource_loaded.side_effect = RuntimeError("storage down")

    # Not in destination -> ensure_resource_loaded is called -> its error must propagate,
    # matching every other ensure_resource_loaded call site (none catch it).
    with pytest.raises(RuntimeError, match="storage down"):
        _call(config, "src-role", "roles")


def test_empty_binding_risk_continues_with_explicit_escalation_warning_when_connection_failures_are_skipped():
    config = _make_config()
    config.skip_failed_resource_connections = True
    config.logger = MagicMock()
    stub = SimpleNamespace(config=config, resource_type="restriction_policies")

    escalation_risk = BaseResource._raise_connection_error_if_any(stub, "dashboard:abc", {}, True)

    assert escalation_risk is True
    message = config.logger.error.call_args.args[0]
    assert "continuing sync" in message
    assert "DESTINATION RESOURCE MAY BE UNRESTRICTED" in message
    assert "refusing to sync" not in message


def test_empty_binding_risk_refuses_to_sync_when_connection_failures_are_not_skipped():
    from datadog_sync.utils.resource_utils import ResourceConnectionError

    config = _make_config()
    config.skip_failed_resource_connections = False
    config.logger = MagicMock()
    stub = SimpleNamespace(config=config, resource_type="restriction_policies")

    with pytest.raises(ResourceConnectionError):
        BaseResource._raise_connection_error_if_any(stub, "dashboard:abc", {}, True)

    message = config.logger.error.call_args.args[0]
    assert "refusing to sync" in message
    assert "DESTINATION RESOURCE MAY BE UNRESTRICTED" not in message
