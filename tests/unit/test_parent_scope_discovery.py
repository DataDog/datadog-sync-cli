# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Parent-scope discovery and the --id-file dispatch contract.

Case Management child models (project-scoped rules/views, case-scoped
comments/links/watchers) discover their resources by enumerating children per
parent. The --id-file path dispatches by model metadata
(`ResourceConfig.id_file_namespace`):

- ``"resource"`` (default): the payload contains resource ids; dispatch routes
  to the existing ``get_resources_by_ids`` (semantics unchanged for every
  existing type).
- ``"parent"``: the payload contains PARENT ids; dispatch routes to the new
  ``get_resources_by_parent_ids(client, parent_ids, max_concurrent_reads=...)``
  returning the same ``(resources, missing, errored)`` shape.

Contract source: docs/case-management-support.md §6.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig


# ─── Test fixtures ───────────────────────────────────────────────────────────


def _make_config(resource_type: str, r_class) -> MagicMock:
    """Config shaped for driving ResourcesHandler._import_get_resources_cb."""
    from datadog_sync.utils.log import Log

    config = MagicMock()
    config.logger = Log(verbose=False)
    config.resources_arg = [resource_type]
    config.filters = None
    config.filter_operator = None
    config.emit_json = False
    config.command = "import"
    config.show_progress_bar = False
    config.max_concurrent_reads = 5
    config.resources = {resource_type: r_class}
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    # Real (non-Mock) scalars the import flow branches on.
    config.fatal_error = False
    config.transient_failure_threshold_pct = 5
    return config


def _make_handler(config):
    from datadog_sync.utils.resources_handler import ResourcesHandler
    from datadog_sync.utils.workers import Counter

    handler = ResourcesHandler(config)
    counter = Counter()
    handler.worker = MagicMock()
    handler.worker.counter = counter
    handler._emit = MagicMock()
    return handler, counter


def _make_model(resource_type: str, id_file_namespace: str) -> MagicMock:
    """A mocked resource class carrying the dispatch metadata."""
    r_class = MagicMock()
    r_class.resource_type = resource_type
    r_class.resource_config = MagicMock()
    r_class.resource_config.list_omitted_attr_prefixes = []
    r_class.resource_config.id_file_namespace = id_file_namespace
    r_class.filter = MagicMock(return_value=True)
    r_class.get_resources_by_ids = AsyncMock()
    r_class.get_resources_by_parent_ids = AsyncMock()
    r_class._get_resources = AsyncMock()
    r_class._import_resource = AsyncMock()
    r_class._send_action_metrics = AsyncMock()
    return r_class


class _StubResource(BaseResource):
    """Minimal concrete BaseResource for interface-shape tests."""

    resource_type = "stub_resource_for_parent_scope_tests"
    resource_config = ResourceConfig(base_path="/fake/stub", skip_resource_mapping=True)

    async def get_resources(self, client):
        return []

    async def import_resource(self, _id=None, resource=None):
        return (_id or "stub-id"), (resource or {})

    async def pre_resource_action_hook(self, _id, resource):
        pass

    async def pre_apply_hook(self):
        pass

    async def create_resource(self, _id, resource):
        return _id, resource

    async def update_resource(self, _id, resource):
        return _id, resource

    async def delete_resource(self, _id):
        pass


# ─── ResourceConfig.id_file_namespace ────────────────────────────────────────


class TestIdFileNamespaceField:
    def test_default_namespace_is_resource(self):
        assert ResourceConfig(base_path="/x").id_file_namespace == "resource"

    def test_explicit_resource_namespace_accepted(self):
        assert ResourceConfig(base_path="/x", id_file_namespace="resource").id_file_namespace == "resource"

    def test_explicit_parent_namespace_accepted(self):
        assert ResourceConfig(base_path="/x", id_file_namespace="parent").id_file_namespace == "parent"

    def test_invalid_namespace_rejected(self):
        with pytest.raises(ValueError, match="id_file_namespace"):
            ResourceConfig(base_path="/x", id_file_namespace="bogus")


# ─── BaseResource.get_resources_by_parent_ids interface ──────────────────────


class TestGetResourcesByParentIds:
    def test_default_implementation_raises_not_implemented(self):
        stub = _StubResource(MagicMock())
        with pytest.raises(NotImplementedError, match="get_resources_by_parent_ids"):
            asyncio.run(stub.get_resources_by_parent_ids(MagicMock(), ["parent-a"]))

    def test_signature_mirrors_get_resources_by_ids(self):
        """The parent-scope interface must be callable with the same
        (client, ids, max_concurrent_reads) surface and return the
        (resources, missing, errored) shape used by get_resources_by_ids."""
        import inspect

        sig = inspect.signature(BaseResource.get_resources_by_parent_ids)
        params = list(sig.parameters)
        assert params[:3] == ["self", "client", "parent_ids"], params
        assert "max_concurrent_reads" in params


# ─── Dispatch routing in _import_get_resources_cb ────────────────────────────


class TestDispatchRouting:
    @pytest.fixture(autouse=True)
    def _allowlist(self, monkeypatch):
        """Register the test types on the import allowlist for the duration
        of each test, mirroring how real types join it with their model PR."""
        import datadog_sync.utils.configuration as configuration_module

        test_types = {"test_resource_ns_type", "test_parent_ns_type"}
        original = configuration_module._ID_FILE_IMPORT_SUPPORTED_TYPES
        monkeypatch.setattr(
            configuration_module,
            "_ID_FILE_IMPORT_SUPPORTED_TYPES",
            original | test_types,
        )
        yield

    def _drive_callback(self, r_class, resource_type, id_payload):
        config = _make_config(resource_type, r_class)
        config.id_payload = {resource_type: id_payload}
        handler, counter = _make_handler(config)
        tmp_storage = defaultdict(list)
        asyncio.run(handler._import_get_resources_cb(resource_type, tmp_storage))
        return handler, counter, tmp_storage

    def test_resource_namespace_routes_to_get_resources_by_ids(self):
        r_class = _make_model("test_resource_ns_type", "resource")
        r_class.get_resources_by_ids.return_value = ([{"id": "r-1"}], [], [])

        handler, counter, storage = self._drive_callback(r_class, "test_resource_ns_type", ["r-1"])

        r_class.get_resources_by_ids.assert_awaited_once()
        r_class.get_resources_by_parent_ids.assert_not_awaited()
        assert storage["test_resource_ns_type"] == [{"id": "r-1"}]

    def test_parent_namespace_routes_to_get_resources_by_parent_ids(self):
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = ([{"id": "child-1"}], [], [])

        handler, counter, storage = self._drive_callback(r_class, "test_parent_ns_type", ["proj-a", "proj-b"])

        r_class.get_resources_by_parent_ids.assert_awaited_once()
        r_class.get_resources_by_ids.assert_not_awaited()
        args, kwargs = r_class.get_resources_by_parent_ids.call_args
        assert list(args[1]) == ["proj-a", "proj-b"], "parent ids must be passed verbatim"
        assert kwargs.get("max_concurrent_reads") == 5
        assert storage["test_parent_ns_type"] == [{"id": "child-1"}]

    def test_parent_ids_are_source_ids_passed_verbatim(self):
        """The contract: parent ids supplied are SOURCE ids; the model maps
        them through state for destination-path operations. The dispatcher
        must not transform them."""
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = ([], [], [])

        self._drive_callback(r_class, "test_parent_ns_type", ["src-proj-1"])

        args, _ = r_class.get_resources_by_parent_ids.call_args
        assert list(args[1]) == ["src-proj-1"]

    def test_zero_child_parent_scope_is_success(self):
        """A parent whose enumeration returns no children is a complete scope,
        not an error or a skip."""
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = ([], [], [])

        handler, counter, storage = self._drive_callback(r_class, "test_parent_ns_type", ["proj-a"])

        assert counter.failure == 0
        assert counter.successes >= 1
        assert storage["test_parent_ns_type"] == []

    def test_failed_parent_scope_isolated_and_counted(self):
        """A parent whose enumeration errored is a failed scope: the failure
        counts (feeding type-wide authority), other parents are unaffected —
        the model itself isolates them in the returned shape."""
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = (
            [{"id": "child-of-proj-a"}],
            [],
            [("proj-b", "permanent", "HTTP 403")],
        )

        handler, counter, storage = self._drive_callback(r_class, "test_parent_ns_type", ["proj-a", "proj-b"])

        # The child of the healthy parent was still discovered...
        assert storage["test_parent_ns_type"] == [{"id": "child-of-proj-a"}]
        # ...the failed parent scope counted as a failure (type-wide authority
        # channel: any failure keeps the type non-authoritative).
        assert counter.failure == 1

    def test_missing_parent_scope_counted_as_skip(self):
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = ([], ["proj-gone"], [])

        handler, counter, storage = self._drive_callback(r_class, "test_parent_ns_type", ["proj-gone"])

        assert storage["test_parent_ns_type"] == []
        assert counter.failure == 0
        # missing -> skipped accounting (mirrors get_resources_by_ids semantics)
        skipped_total = sum(counter.skipped.values()) if isinstance(counter.skipped, dict) else counter.skipped
        assert skipped_total >= 1

    def test_parent_namespace_type_not_on_allowlist_falls_back_to_list(self):
        """Dispatch only applies to allowlisted types: a parent-namespace
        type without an allowlist entry uses the legacy whole-type list
        path (its model PR adds the entry — the explicit-review pattern)."""
        import datadog_sync.utils.configuration as configuration_module

        # Deliberately NOT on the allowlist for this test.
        r_class = _make_model("test_parent_unlisted_type", "parent")
        assert "test_parent_unlisted_type" not in configuration_module._ID_FILE_IMPORT_SUPPORTED_TYPES

        config = _make_config("test_parent_unlisted_type", r_class)
        config.id_payload = {"test_parent_unlisted_type": ["proj-a"]}
        handler, counter = _make_handler(config)
        tmp_storage = defaultdict(list)
        asyncio.run(handler._import_get_resources_cb("test_parent_unlisted_type", tmp_storage))

        r_class.get_resources_by_parent_ids.assert_not_awaited()
        r_class.get_resources_by_ids.assert_not_awaited()
        r_class._get_resources.assert_awaited_once()

    def test_get_resources_by_ids_whole_type_error_handled(self):
        """A parent-namespace model whose get_resources_by_parent_ids raises
        before yielding results hits the existing whole-type failure path."""
        r_class = _make_model("test_parent_ns_type", "parent")
        r_class.get_resources_by_parent_ids.side_effect = RuntimeError("connection pool exhausted")

        handler, counter, storage = self._drive_callback(r_class, "test_parent_ns_type", ["proj-a"])

        assert counter.failure == 1
        assert storage == {}


# ─── End-to-end through import_resources_without_saving ─────────────────────


class _FakeWorker:
    """Minimal functional stand-in for Workers: records the callback for each
    init_workers phase and drains queued items synchronously."""

    def __init__(self):
        from datadog_sync.utils.workers import Counter

        self.counter = Counter()
        self.work_queue = _SyncQueue()
        self._cb = None
        self._extra = ()

    async def init_workers(self, cb, cancel_cb=None, worker_count=None, *args, **kwargs):
        # Mirrors Workers.init_workers(cb, cancel_cb, worker_count, *args):
        # positional args after worker_count are passed to the callback AFTER
        # the queue item (cb(item, *args)).
        self._cb = cb
        self._extra = args

    async def schedule_workers(self, *args, **kwargs):
        while not self.work_queue.empty():
            item = self.work_queue.get()
            await self._cb(item, *self._extra)


class _SyncQueue:
    def __init__(self):
        self._items = []

    def put_nowait(self, item):
        self._items.append(item)

    def get(self):
        return self._items.pop(0)

    def empty(self):
        return not self._items


class TestImportResourcesWithoutSavingDispatch:
    @pytest.fixture(autouse=True)
    def _allowlist(self, monkeypatch):
        import datadog_sync.utils.configuration as configuration_module

        original = configuration_module._ID_FILE_IMPORT_SUPPORTED_TYPES
        monkeypatch.setattr(
            configuration_module,
            "_ID_FILE_IMPORT_SUPPORTED_TYPES",
            original | {"test_parent_e2e_type"},
        )
        yield

    def test_parent_namespace_dispatched_through_full_import(self):
        r_class = _make_model("test_parent_e2e_type", "parent")
        r_class.get_resources_by_parent_ids.return_value = ([{"id": "child-1", "name": "test"}], [], [])

        config = _make_config("test_parent_e2e_type", r_class)
        config.id_payload = {"test_parent_e2e_type": ["src-proj-1"]}
        handler = None
        from datadog_sync.utils.resources_handler import ResourcesHandler

        handler = ResourcesHandler(config)
        handler.worker = _FakeWorker()

        asyncio.run(handler.import_resources_without_saving())

        # The parent-namespace dispatch happened on the discovery pass...
        r_class.get_resources_by_parent_ids.assert_awaited_once()
        r_class.get_resources_by_ids.assert_not_awaited()
        # ...and the discovered resource flowed into the per-resource import.
        r_class._import_resource.assert_awaited()

    def test_resource_namespace_dispatched_through_full_import(self):
        r_class = _make_model("test_parent_e2e_type", "resource")
        r_class.get_resources_by_ids.return_value = ([{"id": "res-1"}], [], [])

        config = _make_config("test_parent_e2e_type", r_class)
        config.id_payload = {"test_parent_e2e_type": ["res-1"]}
        from datadog_sync.utils.resources_handler import ResourcesHandler

        handler = ResourcesHandler(config)
        handler.worker = _FakeWorker()

        asyncio.run(handler.import_resources_without_saving())

        r_class.get_resources_by_ids.assert_awaited_once()
        r_class.get_resources_by_parent_ids.assert_not_awaited()
