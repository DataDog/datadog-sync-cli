# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Framework-level tests for destination-state reconciliation on skip-without-write.

When a resource's ``create_resource``/``update_resource`` discovers (via
``_existing_resources_map``) that the resource already exists on the destination
and raises ``SkipResource`` without writing ``state.destination``, the bucket
view diverges from destination truth: no state file is persisted, so downstream
consumers that trust the bucket (e.g. a per-id presence summary) count the id as
failed even though the resource is confirmed present via the live API.

The ``_create_resource``/``_update_resource`` wrappers in ``base_resource.py``
reconcile this gap: on ``SkipResource`` they look up the resource's mapping key
in ``_existing_resources_map`` and, if found, write the discovered destination
resource into ``state.destination`` (insert-if-absent) before re-raising. These
tests pin that contract and its boundaries.

All identifiers are obviously synthetic (``team-src``, ``team-dst`` ...).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resource_class(
    resource_config, resource_type_name="test_resource", create_side_effect=None, update_side_effect=None
):
    """Create a concrete BaseResource subclass for testing.

    ``create_side_effect``/``update_side_effect`` may be callables returning
    ``(_id, resource)`` or raising, mirroring real create/update methods.
    """

    async def get_resources(self, client):
        return []

    async def import_resource(self, _id=None, resource=None):
        return _id, resource

    async def pre_resource_action_hook(self, _id, resource):
        pass

    async def pre_apply_hook(self):
        pass

    async def create_resource(self, _id, resource):
        if create_side_effect is not None:
            return create_side_effect(_id, resource)
        return _id, resource

    async def update_resource(self, _id, resource):
        if update_side_effect is not None:
            return update_side_effect(_id, resource)
        return _id, resource

    async def delete_resource(self, _id):
        pass

    return type(
        "ConcreteResource",
        (BaseResource,),
        {
            "resource_type": resource_type_name,
            "resource_config": resource_config,
            "get_resources": get_resources,
            "import_resource": import_resource,
            "pre_resource_action_hook": pre_resource_action_hook,
            "pre_apply_hook": pre_apply_hook,
            "create_resource": create_resource,
            "update_resource": update_resource,
            "delete_resource": delete_resource,
        },
    )


def _make_instance(mock_config, resource_config, resource_type="test_resource", **kw):
    cls = _make_resource_class(resource_config, resource_type, **kw)
    inst = cls(mock_config)
    return inst


def _raise_skip(_id, resource):
    raise SkipResource(_id, "test_resource", "already exists")


def _return_resource(_id, resource):
    return _id, resource


# ===========================================================================
# Cycle 1: core framework wrapper
# ===========================================================================


class TestCreateWrapperReconcile:
    def test_create_wrapper_reconciles_when_skip_and_key_in_map(self, mock_config):
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        dest_resource = {"name": "dest-name", "id": "dest-id"}
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": dest_resource}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"]["src-id"] == dest_resource

    def test_update_wrapper_reconciles_when_skip_and_key_in_map(self, mock_config):
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        dest_resource = {"name": "dest-name", "id": "dest-id"}
        inst = _make_instance(mock_config, rc, "test_resource", update_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": dest_resource}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._update_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"]["src-id"] == dest_resource

    def test_wrapper_reconcile_uses_source_id_as_state_key(self, mock_config):
        # The mapping key resolves to a *destination* id, but state.destination
        # must be keyed by the *source* _id (the wrapper's _id argument), per the
        # "destination state files are keyed by source IDs" convention.
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        dest_resource = {"name": "dest-name", "id": "dest-id"}
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": dest_resource}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert "src-id" in mock_config.state.destination["test_resource"]
        assert "dest-id" not in mock_config.state.destination["test_resource"]

    def test_wrapper_does_not_reconcile_when_key_not_in_map(self, mock_config):
        # Genuine non-existence skip: key absent from the map -> no write.
        # This is the critical false-positive guard.
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"other-name": {"name": "other-name"}}
        source_resource = {"name": "ghost-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"] == {}

    def test_wrapper_does_not_reconcile_when_mapping_key_is_none(self, mock_config):
        # Opt-out resource (skip_resource_mapping=True, resource_mapping_key=None):
        # no write, no crash, re-raises.
        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=True)
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {}
        source_resource = {"id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"] == {}

    def test_wrapper_reconciles_empty_string_key_consistent_with_map_existing(self, mock_config):
        # map_existing_resources() keeps entries when `key is not None`, so an
        # empty-string key IS mappable during discovery. The reconcile must use
        # the same `is not None` gate (not truthiness) so an empty-string key
        # present in the map is reconciled here too. Pins the consistency fix.
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        dest_resource = {"name": "", "id": "dest-id"}
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"": dest_resource}
        source_resource = {"name": "", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"]["src-id"] == dest_resource

    def test_wrapper_does_not_reconcile_when_entry_already_present(self, mock_config):
        # Insert-if-absent: a pre-existing entry must NOT be overwritten on skip.
        # Preserves delegate-then-skip resources that wrote state.destination
        # before delegating to update_resource.
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        sentinel = {"name": "dest-name", "id": "dest-id", "marker": "pre-existing"}
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": {"name": "dest-name", "id": "dest-id"}}
        mock_config.state.destination["test_resource"]["src-id"] = sentinel
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"]["src-id"] == sentinel

    def test_wrapper_does_not_reconcile_on_non_skip_exception(self, mock_config):
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)

        def raise_valueerror(_id, resource):
            raise ValueError("boom")

        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=raise_valueerror)
        inst._existing_resources_map = {"dest-name": {"name": "dest-name"}}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(ValueError, match="boom"):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"] == {}

    def test_wrapper_reconcile_is_best_effort_does_not_break_skip(self, mock_config):
        # If get_resource_mapping_key raises an unexpected error, the reconcile
        # must not break the skip's accounting — swallow and re-raise SkipResource.
        rc = ResourceConfig(
            base_path="/test",
            resource_mapping_key=lambda r: (_ for _ in ()).throw(RuntimeError("key extraction broke")),
            skip_resource_mapping=False,
        )
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": {"name": "dest-name"}}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        assert mock_config.state.destination["test_resource"] == {}

    def test_wrapper_reconcile_debug_log_is_preformatted(self, mock_config):
        # The NDJSON log backend (utils/log.py Log.debug) does not interpolate
        # positional %s args in JSON mode, so the reconcile diagnostic must be
        # pre-formatted (f-string), not passed as positional args. Verify the
        # logged message contains the literal values, not %s placeholders.
        rc = ResourceConfig(
            base_path="/test",
            resource_mapping_key=lambda r: (_ for _ in ()).throw(RuntimeError("key extraction broke")),
            skip_resource_mapping=False,
        )
        inst = _make_instance(mock_config, rc, "test_resource", create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": {"name": "dest-name"}}
        source_resource = {"name": "dest-name", "id": "src-id"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("src-id", source_resource))

        mock_config.logger.debug.assert_called_once()
        logged_msg = mock_config.logger.debug.call_args.args[0]
        assert "%s" not in logged_msg
        assert "test_resource" in logged_msg
        assert "src-id" in logged_msg
        assert "key extraction broke" in logged_msg


# ===========================================================================
# Cycle 12: opt-out resources -- no false reconcile
# ===========================================================================


class TestOptOutResourceSkip:
    def test_opt_out_resource_skip_no_write_no_crash(self, mock_config):
        # Opt-out resource (skip_resource_mapping=True, resource_mapping_key=None):
        # get_resource_mapping_key returns None -> no write, no crash, re-raises.
        # Confirms the framework is inert for the non-mapping resources.
        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=True)
        inst = _make_instance(mock_config, rc, "dashboards", create_side_effect=_raise_skip)
        inst._existing_resources_map = {}
        source_resource = {"id": "dashboard-test"}

        with pytest.raises(SkipResource):
            asyncio.run(inst._create_resource("dashboard-test", source_resource))

        assert mock_config.state.destination["dashboards"] == {}


# ===========================================================================
# Cycle 1 case 9: handler accounting unchanged after framework reconcile
# ===========================================================================


class TestHandlerAccountingUnchanged:
    def test_handler_accounting_unchanged_after_framework_reconcile(self, mock_config):
        from datadog_sync.utils.resources_handler import ResourcesHandler

        resource_type = "test_resource"
        _id = "src-id"
        dest_resource = {"name": "dest-name", "id": "dest-id"}

        r_class = MagicMock()
        r_class.resource_config = ResourceConfig(
            base_path="/test", resource_mapping_key="name", skip_resource_mapping=False
        )
        r_class.connect_resources = MagicMock(return_value=MagicMock(empty_binding_escalation=False))
        r_class._pre_resource_action_hook = AsyncMock()
        r_class._send_action_metrics = AsyncMock()
        # create_resource raises SkipResource (exists-path skip-without-write)
        r_class._create_resource = AsyncMock(side_effect=SkipResource(_id, resource_type, "already exists"))
        r_class._existing_resources_map = {"dest-name": dest_resource}
        # The wrapper reads _existing_resources_map off the *instance*, but here
        # r_class is a MagicMock standing in for the class; _create_resource is
        # stubbed directly, so the framework reconcile runs against r_class's
        # own _existing_resources_map via the real BaseResource method. To
        # exercise the real wrapper, drive it through a real instance instead.

        # Use a real instance so the framework wrapper actually runs.
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        inst = _make_instance(mock_config, rc, resource_type, create_side_effect=_raise_skip)
        inst._existing_resources_map = {"dest-name": dest_resource}
        mock_config.resources = {resource_type: inst}
        mock_config.state.source[resource_type][_id] = {"name": "dest-name", "id": _id}

        handler = ResourcesHandler(mock_config)
        handler.worker = MagicMock()
        handler.worker.counter = MagicMock()
        handler.sorter = MagicMock()
        handler._emit = MagicMock()

        asyncio.run(handler._apply_resource_cb([resource_type, _id]))

        handler.worker.counter.increment_skipped.assert_called_once()
        handler.worker.counter.increment_failure.assert_not_called()
        handler.worker.counter.increment_success.assert_not_called()
        # _emit called with skipped status
        emit_args, emit_kwargs = handler._emit.call_args
        assert emit_args[:4] == (resource_type, _id, "sync", "skipped")
        # And the framework reconciled state.destination
        assert mock_config.state.destination[resource_type][_id] == dest_resource
