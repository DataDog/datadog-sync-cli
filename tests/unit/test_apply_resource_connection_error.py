# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Verify _apply_resource_cb logs ResourceConnectionError details at ERROR level.

The error is also logged inside ``connect_resources`` at INFO. Consumers that
filter on ERROR-level lines (e.g. dd-source/managed-sync's CLI error harvester)
need an explicit ERROR record so that missing dependencies surface as
investigable failures rather than silent skips.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.utils.resource_utils import ResourceConnectionError
from datadog_sync.utils.resources_handler import ResourcesHandler


def _drive_apply_cb(mock_config, resource_type, _id, exc):
    """Construct a ResourcesHandler and drive _apply_resource_cb so the
    resource class's connect_resources raises ``exc``. Returns the handler."""

    r_class = MagicMock()
    r_class.resource_config = MagicMock(concurrent=True)
    r_class.connect_resources = MagicMock(side_effect=exc)
    r_class._pre_resource_action_hook = AsyncMock()
    r_class._send_action_metrics = AsyncMock()

    mock_config.resources = {resource_type: r_class}
    mock_config.state.source[resource_type][_id] = {"id": _id}

    handler = ResourcesHandler(mock_config)
    handler.worker = MagicMock()
    handler.worker.counter = MagicMock()
    handler.sorter = MagicMock()
    handler._emit = MagicMock()

    asyncio.run(handler._apply_resource_cb([resource_type, _id]))
    return handler


def test_resource_connection_error_logged_at_error_level(mock_config):
    """ResourceConnectionError must trigger logger.error with the failed-connections detail."""
    failed = {"service_level_objectives": ["slo-abc-def"]}
    exc = ResourceConnectionError(failed)

    _drive_apply_cb(mock_config, "dashboards", "z7a-75h-p2q", exc)

    mock_config.logger.error.assert_called_once()
    args, kwargs = mock_config.logger.error.call_args
    assert args[0].startswith("missing connections: ")
    assert "service_level_objectives" in args[0]
    assert "slo-abc-def" in args[0]
    assert kwargs == {"resource_type": "dashboards", "_id": "z7a-75h-p2q"}


def test_resource_connection_error_still_increments_skipped(mock_config):
    """The new logger.error call must not change the skip/metrics accounting."""
    exc = ResourceConnectionError({"monitors": ["mon-1"]})

    handler = _drive_apply_cb(mock_config, "dashboards", "dash-1", exc)

    handler.worker.counter.increment_skipped.assert_called_once()
    handler.worker.counter.increment_failure.assert_not_called()
    handler._emit.assert_called_once()
    emit_args, emit_kwargs = handler._emit.call_args
    assert emit_args[:4] == ("dashboards", "dash-1", "sync", "skipped")
    assert "reason" in emit_kwargs
