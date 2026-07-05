# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the HAMR-392 monitor error logging + query summarization.

Motivation: when the destination monitor API rejects a create/update, the
previous behavior logged only the response body (e.g. ``400 Bad Request -
{"errors":["Invalid query: Check for invalid tags or facets in your query."]}``),
which is impossible to triage without cross-org access to the source monitor
JSON. These tests verify that the outbound monitor's id/type/query are logged
on 4xx/5xx, that @application.id is preserved through truncation, and that
successful (2xx) monitors don't emit spurious warnings.
"""

from unittest.mock import MagicMock

from datadog_sync.model.monitors import _summarize_query_for_log, _log_monitor_http_error


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


def test_log_monitor_http_error_skipped_on_2xx(caplog):
    _log_monitor_http_error("42", "create", {"query": "q", "type": "metric alert"}, _make_err(200))
    assert "monitor create failed" not in caplog.text


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
