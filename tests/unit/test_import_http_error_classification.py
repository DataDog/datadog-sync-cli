# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019, Datadog, Inc.

"""
Unit tests for HTTP-error classification in the per-resource import worker.

Pins the contract that a transient HTTP error (5xx / 429) on a single
resource's per-id GET is counted as a failure (metric + counter) but does
NOT poison the process exit code.  Permanent HTTP errors (4xx other than
429) and non-HTTP exceptions continue to log at ERROR so exception_logged
is set and run_cmd exits 1.

This mirrors the classification that get_resources_by_ids (the --id-file
path) already applies: 5xx/429 -> "transient", counted, no exit-code
poisoning.  The per-resource worker path (used by dashboards,
dashboard_lists, and every model that propagates CustomClientHTTPError)
previously called logger.error unconditionally for every exception, so a
single 500 on one resource's GET killed the whole import run via
exception_logged -> exit(1).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _http_error(status: int, message: str = "boom") -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = status
    resp.message = "Error"
    return CustomClientHTTPError(resp, message=message)


def _make_handler_with_failing_import(exc: Exception):
    """Build a ResourcesHandler whose _import_resource (the model method)
    raises ``exc``, with a real Log so exception_logged is observable."""
    from datadog_sync.utils.log import Log
    from datadog_sync.utils.resources_handler import ResourcesHandler
    from datadog_sync.utils.workers import Counter

    config = MagicMock()
    config.logger = Log(verbose=False)
    config.resources_arg = ["dashboards"]
    config.filters = None
    config.filter_operator = None
    config.emit_json = False
    config.command = "import"

    r_class = MagicMock()
    r_class.resource_type = "dashboards"
    r_class.resource_config = MagicMock()
    r_class.resource_config.list_omitted_attr_prefixes = []
    r_class.filter = MagicMock(return_value=True)
    r_class._import_resource = AsyncMock(side_effect=exc)
    r_class._send_action_metrics = AsyncMock()

    config.resources = {"dashboards": r_class}
    config.state = MagicMock()
    config.state.source = {}
    config.state.destination = {}

    handler = ResourcesHandler(config)
    counter = Counter()
    handler.worker = MagicMock()
    handler.worker.counter = counter
    handler._emit = MagicMock()

    return handler, config, r_class, counter


class TestImportWorkerHttpErrorClassification:
    """The per-resource import worker must classify CustomClientHTTPError
    so transient failures don't poison the exit code."""

    def test_500_counted_as_failure_but_does_not_poison_exit_code(self):
        """A 500 on one resource's GET is a transient server error: count
        it as a failure and emit the failure metric, but log at WARNING so
        exception_logged stays False and run_cmd exits 0."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(500))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is False, (
            "a transient 500 must not set exception_logged — it would cause "
            "run_cmd to exit(1) and poison the whole import run"
        )

    def test_429_counted_as_failure_but_does_not_poison_exit_code(self):
        """429 (rate limit) is transient: same treatment as 500."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(429))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is False

    def test_503_counted_as_failure_but_does_not_poison_exit_code(self):
        """503 (service unavailable) is transient: same treatment as 500."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(503))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is False

    def test_403_poisons_exit_code(self):
        """A 403 (forbidden) is a permanent error — it should log at ERROR
        so exception_logged is set and run_cmd exits 1."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(403))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert (
            config.logger.exception_logged is True
        ), "a permanent 4xx error must set exception_logged so run_cmd exits 1"

    def test_404_poisons_exit_code(self):
        """A 404 that propagates (not caught as SkipResource by the model)
        is a permanent error — log at ERROR."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(404))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is True

    def test_non_http_exception_poisons_exit_code(self):
        """A non-HTTP exception (e.g. KeyError) is unknown/permanent — log
        at ERROR so the failure is surfaced."""
        handler, config, r_class, counter = _make_handler_with_failing_import(KeyError("missing"))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is True

    def test_aiohttp_client_connection_error_is_transient(self):
        """aiohttp.ClientConnectionError (DNS, connection refused, TCP
        reset) is a transport-shaped failure that should be treated the
        same as 5xx/429 — transient, no exit-code poisoning.  Mirrors
        get_resources_by_ids' classification of aiohttp.ClientError."""
        import aiohttp

        handler, config, r_class, counter = _make_handler_with_failing_import(
            aiohttp.ClientConnectionError("connection refused")
        )

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert counter.failed_ids_by_type["dashboards"] == ["abc-123"]
        assert config.logger.exception_logged is False, (
            "a transport-level connection error is transient and must not " "poison the exit code"
        )

    def test_retry_limit_exhaustion_is_transient(self):
        """The retry wrapper raises a plain Exception after exhausting its
        budget. It must retain transient treatment in the legacy worker,
        matching get_resources_by_ids."""
        exc = Exception("retry limit exceeded timeout: 100 retry_count: 4 error: synthetic overload")
        handler, config, r_class, counter = _make_handler_with_failing_import(exc)

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert config.logger.exception_logged is False
        assert handler._import_transient_failures_by_type["dashboards"] == 1

    def test_transient_failure_budget_sets_fatal_after_workers_finish(self):
        """A broad transient outage is allowed to finish processing but must
        make the command return non-zero after the worker phase."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(503))
        config.transient_failure_threshold_pct = 5
        config.fatal_error = False
        handler._import_attempts_by_type["dashboards"] = 100
        handler._import_transient_failures_by_type["dashboards"] = 6

        handler._enforce_import_transient_failure_budget()

        assert config.fatal_error is True

    def test_transient_failure_budget_allows_failures_below_threshold(self):
        """An isolated transient failure below the configured percentage
        does not fail the completed import."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(503))
        config.transient_failure_threshold_pct = 5
        config.fatal_error = False
        handler._import_attempts_by_type["dashboards"] = 100
        handler._import_transient_failures_by_type["dashboards"] = 4

        handler._enforce_import_transient_failure_budget()

        assert config.fatal_error is False

    def test_completed_worker_phase_saves_partial_state_then_exits_on_budget_breach(self):
        """The full legacy path processes every listed resource, saves partial
        state, and only then returns non-zero when the budget is exceeded."""
        from datadog_sync.utils.log import Log
        from datadog_sync.utils.resources_handler import ResourcesHandler

        config = MagicMock()
        config.logger = Log(verbose=False)
        config.resources_arg = ["dashboards"]
        config.filters = None
        config.filter_operator = None
        config.emit_json = False
        config.command = "import"
        config.source_client = MagicMock()
        config.show_progress_bar = False
        config.max_workers = 10
        config.max_concurrent_reads = 30
        config.id_payload = None
        config.transient_failure_threshold_pct = 5
        config.fatal_error = False
        config.state = MagicMock()

        resources = [{"id": f"dashboard-{i}"} for i in range(100)]
        attempted = []

        async def import_one(resource):
            attempted.append(resource["id"])
            if len(attempted) <= 6:
                raise _http_error(503)

        r_class = MagicMock()
        r_class.resource_type = "dashboards"
        r_class.resource_config = MagicMock(list_omitted_attr_prefixes=[])
        r_class.filter = MagicMock(return_value=True)
        r_class._get_resources = AsyncMock(return_value=resources)
        r_class._import_resource = AsyncMock(side_effect=import_one)
        r_class._send_action_metrics = AsyncMock()
        config.resources = {"dashboards": r_class}

        handler = ResourcesHandler(config)

        async def run_import():
            await handler.init_async()
            with pytest.raises(SystemExit) as exc_info:
                await handler.import_resources_without_saving()
            assert exc_info.value.code == 1

        asyncio.run(run_import())

        assert len(attempted) == 100
        assert config.fatal_error is True
        config.state.dump_state.assert_called_once()

    def test_resource_connection_error_poisons_exit_code(self):
        """ResourceConnectionError (unresolved resource references) is a
        permanent problem — the referenced resource is gone, not a
        transient transport failure.  Must log at ERROR so it is surfaced."""
        from datadog_sync.utils.resource_utils import ResourceConnectionError

        handler, config, r_class, counter = _make_handler_with_failing_import(
            ResourceConnectionError(failed_connections_dict={"monitors": ["mon-1"]})
        )

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        assert counter.failure == 1
        assert config.logger.exception_logged is True, (
            "ResourceConnectionError is a permanent unresolved-reference "
            "problem, not a transient transport failure — must surface at ERROR"
        )

    def test_500_emits_failure_metric_with_http_5xx_class(self):
        """The NDJSON outcome metric must carry failure_class=http_5xx so
        downstream consumers can distinguish transient from permanent."""
        handler, config, r_class, counter = _make_handler_with_failing_import(_http_error(500))

        asyncio.run(handler._import_resource(("dashboards", {"id": "abc-123"})))

        # _emit(resource_type, _id, action, status, reason=..., failure_class=...)
        emit_calls = handler._emit.call_args_list
        assert len(emit_calls) == 1
        _, kwargs = emit_calls[0]
        assert kwargs.get("failure_class") == "http_5xx"
        assert kwargs.get("reason") == "HTTP 500"
