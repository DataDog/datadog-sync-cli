# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for ``format_exc_for_log`` and the sync/import error-log call sites.

Pins the contract that empty-``str()`` exceptions (e.g.
``aiohttp.ServerTimeoutError()``) never produce blank ERROR log bodies.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    format_exc_for_log,
)
from datadog_sync.utils.resources_handler import ResourcesHandler

# --- format_exc_for_log unit tests ---------------------------------------


def test_format_exc_for_log_preserves_non_empty_message():
    """Common case: non-empty exception message passes through verbatim."""
    e = ValueError("bad input")
    assert format_exc_for_log(e) == "bad input"


def test_format_exc_for_log_preserves_http_error_message():
    """CustomClientHTTPError must pass through unchanged."""
    from types import SimpleNamespace

    err = CustomClientHTTPError(
        SimpleNamespace(status=500, message="Internal Server Error"),
        message="upstream returned 500",
    )
    out = format_exc_for_log(err)
    assert "500" in out, f"expected status code in formatted output, got {out!r}"
    assert "upstream returned 500" in out, f"expected body in formatted output, got {out!r}"


def test_format_exc_for_log_empty_string_falls_back_to_type_name():
    """Empty-str() exception must surface its class name."""

    class _MyError(Exception):
        pass

    e = _MyError()
    assert str(e) == "", "precondition: bare exception has empty str()"
    out = format_exc_for_log(e)
    assert "_MyError" in out, f"empty-message exception must surface its type name; got {out!r}"


def test_format_exc_for_log_aiohttp_server_timeout_emits_timeout_marker():
    """ServerTimeoutError must produce a greppable 'timeout:' marker + class name."""
    e = aiohttp.ServerTimeoutError()
    assert str(e) == "", "precondition: bare ServerTimeoutError has empty str()"
    out = format_exc_for_log(e)
    assert "timeout" in out.lower(), f"expected 'timeout' marker; got {out!r}"
    assert "ServerTimeoutError" in out, f"expected class name; got {out!r}"


def test_format_exc_for_log_asyncio_timeout_emits_timeout_marker():
    """asyncio.TimeoutError must produce the same 'timeout:' marker as ServerTimeoutError."""
    e = asyncio.TimeoutError()
    assert str(e) == "", "precondition: bare asyncio.TimeoutError has empty str()"
    out = format_exc_for_log(e)
    assert "timeout" in out.lower(), f"expected 'timeout' marker; got {out!r}"


def test_format_exc_for_log_client_os_error_falls_back_to_type():
    """ClientOSError (empty-str, non-timeout) must surface its class name."""
    e = aiohttp.ClientOSError()
    assert str(e) == "", "precondition: bare ClientOSError has empty str()"
    out = format_exc_for_log(e)
    assert "ClientOSError" in out, f"expected class name; got {out!r}"


# --- ResourcesHandler integration tests ----------------------------------


def _drive_apply_cb(mock_config, resource_type, _id, exc):
    """Drive ``_apply_resource_cb`` so ``connect_resources`` raises ``exc``."""
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


def _drive_import_resource(mock_config, resource_type, _id, exc):
    """Drive ``_import_resource`` so ``r_class._import_resource`` raises ``exc``."""
    r_class = MagicMock()
    r_class.resource_config = MagicMock(
        list_omitted_attr_prefixes=[],
        excluded_attributes=[],
    )
    r_class._import_resource = AsyncMock(side_effect=exc)
    r_class._send_action_metrics = AsyncMock()
    r_class.filter = MagicMock(return_value=True)

    mock_config.resources = {resource_type: r_class}
    mock_config.filters = []

    handler = ResourcesHandler(mock_config)
    handler.worker = MagicMock()
    handler.worker.counter = MagicMock()
    handler._emit = MagicMock()

    asyncio.run(handler._import_resource([resource_type, {"id": _id}]))
    return handler


def test_apply_cb_empty_timeout_surfaces_marker(mock_config):
    """ServerTimeoutError on the apply path must emit a 'timeout:' marker, not a blank body."""
    exc = aiohttp.ServerTimeoutError()
    _drive_apply_cb(mock_config, "notebooks", "n-1", exc)

    mock_config.logger.error.assert_called_once()
    args, kwargs = mock_config.logger.error.call_args
    assert args[0], f"logger.error must not be called with empty message; got args={args!r}"
    assert "timeout" in args[0].lower(), f"timeout exception must produce a 'timeout' marker; got {args[0]!r}"
    assert kwargs == {"resource_type": "notebooks", "_id": "n-1"}


def test_apply_cb_empty_exception_surfaces_type_name(mock_config):
    """ClientOSError on the apply path must surface its class name, not a blank body."""
    exc = aiohttp.ClientOSError()
    _drive_apply_cb(mock_config, "notebooks", "n-2", exc)

    mock_config.logger.error.assert_called_once()
    args, _ = mock_config.logger.error.call_args
    assert args[0], f"logger.error must not be called with empty message; got args={args!r}"
    assert "ClientOSError" in args[0], f"empty-message exception must surface its type name; got {args[0]!r}"


def test_apply_cb_normal_exception_message_preserved(mock_config):
    """Regression guard: non-empty exception messages must pass through unchanged."""
    exc = ValueError("bad notebook payload: cells[0].definition missing")
    _drive_apply_cb(mock_config, "notebooks", "n-3", exc)

    mock_config.logger.error.assert_called_once()
    args, _ = mock_config.logger.error.call_args
    assert (
        args[0] == "bad notebook payload: cells[0].definition missing"
    ), f"non-empty exception message must be preserved verbatim; got {args[0]!r}"


def test_apply_cb_http_error_message_preserved(mock_config):
    """CustomClientHTTPError must carry status + body through to the ERROR line."""
    from types import SimpleNamespace

    exc = CustomClientHTTPError(
        SimpleNamespace(status=503, message="Service Unavailable"),
        message="upstream is down",
    )
    _drive_apply_cb(mock_config, "notebooks", "n-4", exc)

    mock_config.logger.error.assert_called_once()
    args, _ = mock_config.logger.error.call_args
    assert "503" in args[0], f"HTTP status must be preserved in error log; got {args[0]!r}"
    assert "upstream is down" in args[0], f"HTTP body must be preserved; got {args[0]!r}"


def test_import_path_empty_timeout_surfaces_marker_at_error_level(mock_config):
    """Import path must surface the 'timeout:' marker at ERROR level (not DEBUG-only)."""
    exc = aiohttp.ServerTimeoutError()
    _drive_import_resource(mock_config, "notebooks", "n-5", exc)

    error_calls = list(mock_config.logger.error.call_args_list)
    formatted_msgs = [c.args[0] for c in error_calls if c.args]
    timeout_marked = [m for m in formatted_msgs if "timeout" in m.lower()]
    assert (
        timeout_marked
    ), f"import path must surface 'timeout' marker at ERROR level; got error msgs={formatted_msgs!r}"
