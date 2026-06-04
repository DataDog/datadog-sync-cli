# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for CustomClient._client_timeout() and the sock_read timeout shape.

Verifies that:
1. _client_timeout() returns an aiohttp.ClientTimeout with total=None and
   sock_read equal to self.timeout.
2. Every HTTP verb (get, post, put, patch, delete) receives a ClientTimeout
   rather than a bare integer.
3. asyncio.TimeoutError raised during a body read propagates uncaught through
   request_with_retry (so it reaches the caller's except handler).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from datadog_sync.utils.custom_client import CustomClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client(timeout: int = 120) -> CustomClient:
    """Return a CustomClient with a minimal stub — no real network I/O."""
    return CustomClient(
        host="https://api.datadoghq.com",
        auth={"apiKeyAuth": "fake-api", "appKeyAuth": "fake-app"},
        retry_timeout=300,
        timeout=timeout,
        send_metrics=False,
    )


# ---------------------------------------------------------------------------
# Unit: _client_timeout shape
# ---------------------------------------------------------------------------


class TestClientTimeoutShape:
    """_client_timeout() must return ClientTimeout(total=None, sock_read=<timeout>)."""

    def test_returns_client_timeout_instance(self):
        client = _make_client(timeout=60)
        ct = client._client_timeout()
        assert isinstance(ct, aiohttp.ClientTimeout)

    def test_total_is_none(self):
        """total=None ensures long-but-progressing body reads are not hard-capped."""
        client = _make_client(timeout=120)
        ct = client._client_timeout()
        assert ct.total is None

    def test_sock_read_matches_self_timeout(self):
        """sock_read must equal self.timeout — the per-chunk gap deadline."""
        for t in (30, 60, 120, 300):
            client = _make_client(timeout=t)
            ct = client._client_timeout()
            assert ct.sock_read == t, f"Expected sock_read={t}, got {ct.sock_read}"

    def test_different_timeout_values_propagate(self):
        """Changing the client's timeout changes the returned sock_read."""
        c30 = _make_client(timeout=30)
        c300 = _make_client(timeout=300)
        assert c30._client_timeout().sock_read == 30
        assert c300._client_timeout().sock_read == 300


# ---------------------------------------------------------------------------
# Unit: HTTP verb wiring — each method must pass a ClientTimeout
# ---------------------------------------------------------------------------


def _mock_session_response(status: int = 200, json_body: dict = None) -> MagicMock:
    """Build a mock context-manager response that returns successfully."""
    resp = AsyncMock()
    resp.status = status
    resp.raise_for_status = MagicMock(return_value=None)
    resp.json = AsyncMock(return_value=json_body or {"data": []})
    resp.text = AsyncMock(return_value="")
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _assert_timeout_is_client_timeout(call_kwargs: dict, expected_sock_read: int):
    """Helper: assert the timeout kwarg is a ClientTimeout with correct shape."""
    t = call_kwargs.get("timeout")
    assert isinstance(t, aiohttp.ClientTimeout), f"Expected ClientTimeout, got {type(t)}"
    assert t.total is None, f"Expected total=None, got {t.total}"
    assert t.sock_read == expected_sock_read, f"Expected sock_read={expected_sock_read}, got {t.sock_read}"


class TestVerbsUseClientTimeout:
    """Every HTTP verb must forward a properly shaped ClientTimeout to aiohttp."""

    def _setup_client_with_mock_session(self, timeout: int = 120):
        client = _make_client(timeout=timeout)
        client.session = MagicMock()
        client.session.get = MagicMock(return_value=_mock_session_response())
        client.session.post = MagicMock(return_value=_mock_session_response())
        client.session.put = MagicMock(return_value=_mock_session_response())
        client.session.patch = MagicMock(return_value=_mock_session_response())
        client.session.delete = MagicMock(return_value=_mock_session_response())
        return client

    def test_get_passes_client_timeout(self):
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.get("/api/v1/test"))
        _, kw = client.session.get.call_args
        _assert_timeout_is_client_timeout(kw, expected_sock_read=120)

    def test_post_passes_client_timeout(self):
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.post("/api/v1/test", body={"key": "val"}))
        _, kw = client.session.post.call_args
        _assert_timeout_is_client_timeout(kw, expected_sock_read=120)

    def test_put_passes_client_timeout(self):
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.put("/api/v1/test", body={"key": "val"}))
        _, kw = client.session.put.call_args
        _assert_timeout_is_client_timeout(kw, expected_sock_read=120)

    def test_patch_passes_client_timeout(self):
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.patch("/api/v1/test", body={"key": "val"}))
        _, kw = client.session.patch.call_args
        _assert_timeout_is_client_timeout(kw, expected_sock_read=120)

    def test_delete_passes_client_timeout(self):
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.delete("/api/v1/test"))
        _, kw = client.session.delete.call_args
        _assert_timeout_is_client_timeout(kw, expected_sock_read=120)

    def test_no_bare_int_timeout_in_any_verb(self):
        """None of the HTTP verbs should pass a bare int as timeout."""
        client = self._setup_client_with_mock_session(timeout=120)
        asyncio.run(client.get("/api/v1/test"))
        asyncio.run(client.post("/api/v1/test", body={}))
        asyncio.run(client.put("/api/v1/test", body={}))
        asyncio.run(client.patch("/api/v1/test", body={}))
        asyncio.run(client.delete("/api/v1/test"))

        for verb_mock in (
            client.session.get,
            client.session.post,
            client.session.put,
            client.session.patch,
            client.session.delete,
        ):
            _, kw = verb_mock.call_args
            timeout = kw.get("timeout")
            assert not isinstance(timeout, int), (
                f"{verb_mock} passed a bare int timeout={timeout!r}; "
                "must use aiohttp.ClientTimeout"
            )


# ---------------------------------------------------------------------------
# Integration: asyncio.TimeoutError propagates through request_with_retry
# ---------------------------------------------------------------------------


class TestTimeoutPropagation:
    """asyncio.TimeoutError from a body stall must escape request_with_retry."""

    def _resp_that_raises_on_text(self, exc: Exception) -> MagicMock:
        """Return a mock response whose .text() raises exc (simulates body stall)."""
        resp = MagicMock()
        resp.status = 200
        resp.raise_for_status = MagicMock(return_value=None)
        resp.text = AsyncMock(side_effect=exc)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def test_asyncio_timeout_error_propagates(self):
        """asyncio.TimeoutError from resp.text() must propagate out of get()."""
        client = _make_client(timeout=120)
        client.session = MagicMock()
        client.session.get = MagicMock(
            return_value=self._resp_that_raises_on_text(asyncio.TimeoutError("sock_read exceeded"))
        )

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            asyncio.run(client.get("/api/v1/dashboards"))

    def test_builtin_timeout_error_propagates(self):
        """Built-in TimeoutError must also propagate (covers Python 3.11 alias)."""
        client = _make_client(timeout=120)
        client.session = MagicMock()
        client.session.get = MagicMock(
            return_value=self._resp_that_raises_on_text(TimeoutError("timeout"))
        )

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            asyncio.run(client.get("/api/v1/dashboards"))
