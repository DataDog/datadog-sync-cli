# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the monitor error logging + query summarization.

Motivation: when the destination monitor API rejects a create/update, the
previous behavior logged only the response body (e.g. ``400 Bad Request -
{"errors":["Invalid query: Check for invalid tags or facets in your query."]}``),
which is impossible to triage without cross-org access to the source monitor
JSON. These tests verify that the outbound monitor's id/type/query are logged
on 4xx/5xx, that @application.id is preserved through truncation, and that
successful (2xx) monitors don't emit spurious warnings.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.monitors import _summarize_query_for_log, _log_monitor_http_error
from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _make_err(status_code):
    err = MagicMock()
    err.status_code = status_code
    return err


def test_summarize_short_query_returned_unchanged():
    q = 'rum("@application.id:abc-123 env:prod").last("5m") > 5'
    assert _summarize_query_for_log(q) == q


def test_summarize_none_returned_none():
    assert _summarize_query_for_log(None) is None


def test_summarize_long_query_without_appid_is_head_truncated():
    q = "x" * 5000
    out = _summarize_query_for_log(q)
    assert out.endswith("...")
    assert len(out) == 2000
    assert out[:5] == "xxxxx"


def test_summarize_long_query_with_appid_preserves_token():
    """@application.id must survive the truncation window."""
    padding_left = "A" * 4000
    padding_right = "B" * 4000
    q = padding_left + " @application.id:c123c907-c2bd-4421-92dd-e0720bd7f14b " + padding_right
    out = _summarize_query_for_log(q)
    assert "@application.id:c123c907-c2bd-4421-92dd-e0720bd7f14b" in out
    # The output is roughly bounded by the max plus ellipses; the important
    # invariant is that we didn't drop the token.
    assert out.count("...") >= 1


def test_summarize_long_query_appid_at_end_preserved():
    q = ("x" * 3000) + " @application.id:abc-999"
    out = _summarize_query_for_log(q)
    assert "@application.id:abc-999" in out


def test_summarize_long_query_appid_at_start_preserved():
    q = "@application.id:xyz-1 " + ("y" * 3000)
    out = _summarize_query_for_log(q)
    assert "@application.id:xyz-1" in out


def test_log_monitor_http_error_falls_back_to_queries_field(caplog):
    """Formula / multi-query monitors populate `queries: [...]` instead of a
    single `query` string. The error-logging path must fall back so the
    outbound payload is still visible for diagnosis."""
    caplog.set_level("WARNING", logger="datadog_sync_cli")
    resource = {
        "type": "query alert",
        "queries": ['rum("@application.id:abc-def env:prod").last("5m") > 5'],
    }
    _log_monitor_http_error("m-multi", "create", resource, _make_err(400))
    assert "monitor create failed" in caplog.text
    # The @application.id token from within the queries list survives.
    assert "@application.id:abc-def" in caplog.text
    # Not the literal string "None" (would signal the fallback didn't fire).
    assert "query=None" not in caplog.text


def test_summarize_pathological_appid_token_still_bounded():
    """If the @application.id: value itself is unreasonably long (attacker,
    malformed, or a future field-shape change), the summarizer must still
    produce output bounded by the overall cap."""
    huge_token = "@application.id:" + ("Z" * 5000)
    q = "prefix " + huge_token + " suffix"
    out = _summarize_query_for_log(q)
    # The overall output must be bounded; the exact upper bound is the
    # length cap plus a small constant for ellipses.
    assert len(out) <= 2100  # 2000 cap + a few ellipses worth of slack
    # The token prefix is still visible so operators know what happened.
    assert "@application.id:" in out


def test_log_monitor_http_error_skipped_on_2xx(caplog):
    caplog.set_level("WARNING", logger="datadog_sync_cli")
    _log_monitor_http_error("42", "create", {"query": "q", "type": "metric alert"}, _make_err(200))
    # Empty caplog.records is the stronger assertion — no warning emitted at all.
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_log_monitor_http_error_emitted_on_4xx(caplog):
    """4xx must emit a WARNING with the outbound identifiers."""
    caplog.set_level("WARNING", logger="datadog_sync_cli")
    resource = {
        "query": 'rum("@application.id:c123c907-... env:prod").last("5m") > 5',
        "type": "rum alert",
    }
    _log_monitor_http_error("142464149", "create", resource, _make_err(400))
    assert "monitor create failed" in caplog.text
    assert "status=400" in caplog.text
    assert "id=142464149" in caplog.text
    assert "'rum alert'" in caplog.text
    assert "@application.id:c123c907-..." in caplog.text


def test_log_monitor_http_error_emitted_on_5xx(caplog):
    """5xx (e.g. 512) must also emit; type + query length are diagnostic for
    the overload class of failure."""
    caplog.set_level("WARNING", logger="datadog_sync_cli")
    resource = {"query": "avg(last_1h):anomalies(...)", "type": "query alert"}
    _log_monitor_http_error("85192266", "update", resource, _make_err(512))
    assert "monitor update failed" in caplog.text
    assert "status=512" in caplog.text
    assert "id=85192266" in caplog.text
    assert "'query alert'" in caplog.text


def test_summarize_handles_non_string_query_repr():
    """Formula / multi-query monitors sometimes populate `queries: [...]`
    instead of a single `query` string. Ensure the summarizer stringifies
    without exploding.
    """
    q = ["query1", "query2 with @application.id:abc-1"]
    out = _summarize_query_for_log(q)
    assert out is not None
    # repr(list) preserves the app.id token as a substring, so the truncation
    # path can still preserve it (short input here — no truncation triggered).
    assert "@application.id:abc-1" in out


def test_summarize_handles_non_string_query_beyond_cap():
    """Non-string query that exceeds the cap still gets truncated safely."""
    q = ["x" * 3000]
    out = _summarize_query_for_log(q)
    assert out is not None
    assert len(out) <= 2000


def test_end_to_end_create_error_logs_query(caplog):
    """Drive create_resource -> destination raise -> _log_monitor_http_error,
    proving the WARN fires from the real error path, not just from a direct
    call to the helper."""
    from datadog_sync.model.monitors import Monitors

    caplog.set_level("WARNING", logger="datadog_sync_cli")

    # Assemble a Monitors instance with a mocked destination client.
    config = MagicMock()
    resource = {"query": "rum(\"@application.id:c123c907-abc\").last(\"5m\") > 5", "type": "rum alert"}

    class FakeErrorResp:
        status = 400
        message = "Bad Request"
        headers = {}

    err = CustomClientHTTPError(FakeErrorResp(), message='{"errors":["Invalid query"]}')

    dest_client = MagicMock()
    dest_client.post = AsyncMock(side_effect=err)
    config.destination_client = dest_client

    m = Monitors(config)

    with pytest.raises(CustomClientHTTPError):
        asyncio.run(m.create_resource("mon-1", resource))

    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("monitor create failed" in r.getMessage() for r in warns)
    assert any("@application.id:c123c907-abc" in r.getMessage() for r in warns)
