# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for CustomClient trust_env / HTTP proxy support (HAMR-393).

Verifies that:
1. _init_session() forwards trust_env=True to aiohttp.ClientSession on both the
   verify_ssl=True and verify_ssl=False branches.
2. trust_env defaults to False when not explicitly passed to CustomClient.
3. post_unauthenticated() forwards trust_env to its own ClientSession.
4. The --http-client-trust-env / DD_HTTP_CLIENT_TRUST_ENV option parses and
   coerces string CLI/envvar values to a real bool by the time it reaches
   command dispatch (run_cmd), without ever touching the network.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.utils.custom_client import CustomClient


def _make_client(trust_env: bool = False, verify_ssl: bool = True) -> CustomClient:
    """Return a CustomClient with a minimal stub — no real network I/O."""
    return CustomClient(
        host="https://api.datadoghq.com",
        auth={"apiKeyAuth": "fake-api", "appKeyAuth": "fake-app"},
        retry_timeout=300,
        timeout=30,
        send_metrics=False,
        verify_ssl=verify_ssl,
        trust_env=trust_env,
    )


# ---------------------------------------------------------------------------
# Unit: _init_session forwards trust_env
# ---------------------------------------------------------------------------


class TestInitSessionTrustEnv:
    """Unit tests: CustomClient._init_session forwards trust_env to aiohttp."""

    def test_verify_ssl_true_branch_passes_trust_env_true(self):
        client = _make_client(trust_env=True, verify_ssl=True)
        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = MagicMock()
            asyncio.run(client._init_session())
            assert mock_session_cls.call_args.kwargs.get("trust_env") is True

    def test_verify_ssl_false_branch_passes_trust_env_true(self):
        client = _make_client(trust_env=True, verify_ssl=False)
        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = MagicMock()
            asyncio.run(client._init_session())
            assert mock_session_cls.call_args.kwargs.get("trust_env") is True

    def test_default_trust_env_is_false(self):
        """When trust_env isn't passed to CustomClient, False must reach the constructor."""
        client = _make_client()
        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = MagicMock()
            asyncio.run(client._init_session())
            assert mock_session_cls.call_args.kwargs.get("trust_env") is False

    def test_verify_ssl_false_default_trust_env_is_false(self):
        client = _make_client(verify_ssl=False)
        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = MagicMock()
            asyncio.run(client._init_session())
            assert mock_session_cls.call_args.kwargs.get("trust_env") is False


# ---------------------------------------------------------------------------
# Unit: post_unauthenticated forwards trust_env
# ---------------------------------------------------------------------------


def _mock_session_cm() -> MagicMock:
    """Build a mock async-context-manager session, mirroring test_custom_client_timeout.py."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestPostUnauthenticatedTrustEnv:
    """Unit tests: CustomClient.post_unauthenticated forwards trust_env to aiohttp."""

    def test_post_unauthenticated_passes_trust_env(self):
        client = _make_client(trust_env=True)
        client._post_raw = AsyncMock()

        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = _mock_session_cm()
            asyncio.run(client.post_unauthenticated("https://example.com/hook", {"key": "value"}))
            assert mock_session_cls.call_args.kwargs.get("trust_env") is True

    def test_post_unauthenticated_default_trust_env_false(self):
        client = _make_client()
        client._post_raw = AsyncMock()

        with (
            patch("datadog_sync.utils.custom_client.aiohttp.ClientSession") as mock_session_cls,
            patch("datadog_sync.utils.custom_client.aiohttp.TCPConnector"),
        ):
            mock_session_cls.return_value = _mock_session_cm()
            asyncio.run(client.post_unauthenticated("https://example.com/hook", {"key": "value"}))
            assert mock_session_cls.call_args.kwargs.get("trust_env") is False


# ---------------------------------------------------------------------------
# CLI: --http-client-trust-env / DD_HTTP_CLIENT_TRUST_ENV parsing
# ---------------------------------------------------------------------------
#
# These tests patch datadog_sync.commands._import.run_cmd — the exact name
# the `import` command's callback dispatches through — so no config build,
# HTTP session, or network I/O ever happens. That makes these CLI tests
# network-safe by construction (a mocked dispatch target), not by luck of
# missing credentials or VCR cassettes. `cli_runner` is a plain CliRunner
# constructed locally in this fixture; it is deliberately NOT named `runner`
# so it can't shadow the VCR-protected `runner` fixture defined in
# tests/conftest.py.


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


class TestCliHttpClientTrustEnv:
    """CLI-level tests for --http-client-trust-env / DD_HTTP_CLIENT_TRUST_ENV."""

    def test_cli_accepts_http_client_trust_env_flag(self, cli_runner):
        """--http-client-trust-env true parses to a real bool by the time run_cmd is dispatched."""
        with patch("datadog_sync.commands._import.run_cmd") as mock_run_cmd:
            result = cli_runner.invoke(cli, ["import", "--http-client-trust-env", "true", "--validate=false"])

        assert result.exit_code == 0, result.output
        mock_run_cmd.assert_called_once()
        _, kwargs = mock_run_cmd.call_args
        assert kwargs["http_client_trust_env"] is True

    def test_cli_rejects_invalid_http_client_trust_env_value(self, cli_runner):
        """An unparseable bool value is rejected before dispatch, with a clear error message."""
        with patch("datadog_sync.commands._import.run_cmd") as mock_run_cmd:
            result = cli_runner.invoke(cli, ["import", "--http-client-trust-env", "not-a-bool", "--validate=false"])

        # CustomOptionClass.handle_parse_result calls sys.exit(1) on a bad bool value.
        assert result.exit_code == 1
        assert "Invalid value" in result.output
        assert "trust" in result.output.lower() and "env" in result.output.lower()
        mock_run_cmd.assert_not_called()

    @pytest.mark.parametrize(
        "env_value,expected",
        [
            ("true", True),
            ("false", False),
            ("1", True),
            ("0", False),
        ],
    )
    def test_envvar_coerces_to_bool(self, cli_runner, env_value, expected):
        """DD_HTTP_CLIENT_TRUST_ENV env values coerce to a real bool via Click before dispatch."""
        with patch("datadog_sync.commands._import.run_cmd") as mock_run_cmd:
            result = cli_runner.invoke(
                cli,
                ["import", "--validate=false"],
                env={"DD_HTTP_CLIENT_TRUST_ENV": env_value},
            )

        assert result.exit_code == 0, result.output
        mock_run_cmd.assert_called_once()
        _, kwargs = mock_run_cmd.call_args
        assert kwargs["http_client_trust_env"] is expected
