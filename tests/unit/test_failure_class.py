# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for failure_class emission on ResourceOutcome and _sanitize_reason.

Covers every branch of _sanitize_reason, the omitempty serialization
round-trip in ResourceOutcome.to_dict(), and the _emit call-site wiring.
"""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

from datadog_sync.utils.resource_utils import CustomClientHTTPError, ResourceConnectionError
from datadog_sync.utils.resources_handler import ResourcesHandler
from datadog_sync.utils.sync_report import ResourceOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_err(status_code: int) -> CustomClientHTTPError:
    """Build a CustomClientHTTPError with the given status code."""
    resp = MagicMock()
    resp.status = status_code
    return CustomClientHTTPError(resp)


def _make_handler(emit_json: bool = True) -> ResourcesHandler:
    """Build a ResourcesHandler with a minimal config mock."""
    handler = MagicMock(spec=ResourcesHandler)
    handler.config = MagicMock()
    handler.config.emit_json = emit_json
    handler.config.command = "import"
    return handler


# ---------------------------------------------------------------------------
# _sanitize_reason — one test per failure_class value
# ---------------------------------------------------------------------------


class TestSanitizeReasonFailureClass:
    """Each branch of _sanitize_reason must produce the correct (reason, failure_class) tuple."""

    def test_http_403(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(403))
        assert reason == "HTTP 403"
        assert fc == "http_4xx_403"

    def test_http_404(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(404))
        assert reason == "HTTP 404"
        assert fc == "http_4xx_404"

    def test_http_429(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(429))
        assert reason == "HTTP 429"
        assert fc == "http_4xx_429"

    def test_http_4xx_other_400(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(400))
        assert reason == "HTTP 400"
        assert fc == "http_4xx_other"

    def test_http_4xx_other_401(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(401))
        assert reason == "HTTP 401"
        assert fc == "http_4xx_other"

    def test_http_4xx_other_422(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(422))
        assert reason == "HTTP 422"
        assert fc == "http_4xx_other"

    def test_http_4xx_other_499(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(499))
        assert reason == "HTTP 499"
        assert fc == "http_4xx_other"

    def test_http_500(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(500))
        assert reason == "HTTP 500"
        assert fc == "http_5xx"

    def test_http_503(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(503))
        assert reason == "HTTP 503"
        assert fc == "http_5xx"

    def test_http_599(self):
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(599))
        assert reason == "HTTP 599"
        assert fc == "http_5xx"

    def test_timeout_error(self):
        reason, fc = ResourcesHandler._sanitize_reason(TimeoutError("timed out"))
        assert reason == "TimeoutError"
        assert fc == "http_timeout"

    def test_resource_connection_error(self):
        err = ResourceConnectionError({"monitors": ["missing-id"]})
        reason, fc = ResourcesHandler._sanitize_reason(err)
        assert reason == "connection_error"
        assert fc == "http_connection"

    def test_generic_exception_returns_class_name_and_unknown(self):
        err = ValueError("something unexpected")
        reason, fc = ResourcesHandler._sanitize_reason(err)
        assert reason == "ValueError"
        assert fc == "unknown"

    def test_generic_runtime_error(self):
        err = RuntimeError("crash")
        reason, fc = ResourcesHandler._sanitize_reason(err)
        assert reason == "RuntimeError"
        assert fc == "unknown"

    def test_http_defensive_non_4xx_5xx(self):
        # 1xx/2xx/3xx shouldn't reach here, but must not crash.
        reason, fc = ResourcesHandler._sanitize_reason(_make_http_err(301))
        assert reason == "HTTP 301"
        assert fc == "unknown"


# ---------------------------------------------------------------------------
# ResourceOutcome.to_dict() — omitempty semantics
# ---------------------------------------------------------------------------


class TestResourceOutcomeFailureClassOmitempty:
    """failure_class must appear in to_dict() only when non-empty."""

    def test_failure_class_omitted_when_empty(self):
        """No kwarg → field absent from serialised dict (omitempty)."""
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="123",
            action_type="import",
            status="success",
            action_sub_type="",
            reason="",
        )
        d = outcome.to_dict()
        assert "failure_class" not in d

    def test_failure_class_omitted_when_explicit_empty_string(self):
        """Explicit failure_class="" → still omitted."""
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="123",
            action_type="import",
            status="failure",
            action_sub_type="",
            reason="HTTP 500",
            failure_class="",
        )
        d = outcome.to_dict()
        assert "failure_class" not in d

    def test_failure_class_present_when_set(self):
        """Non-empty failure_class → included in dict."""
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="123",
            action_type="import",
            status="failure",
            action_sub_type="",
            reason="HTTP 500",
            failure_class="http_5xx",
        )
        d = outcome.to_dict()
        assert "failure_class" in d
        assert d["failure_class"] == "http_5xx"

    def test_all_7_canonical_values_round_trip(self):
        """All 7 canonical failure_class values survive to_dict()."""
        canonical = [
            "http_4xx_403",
            "http_4xx_404",
            "http_4xx_429",
            "http_4xx_other",
            "http_5xx",
            "http_timeout",
            "http_connection",
        ]
        for fc in canonical:
            outcome = ResourceOutcome(
                command="import",
                resource_type="monitors",
                id="1",
                action_type="import",
                status="failure",
                action_sub_type="",
                reason="err",
                failure_class=fc,
            )
            d = outcome.to_dict()
            assert d.get("failure_class") == fc, f"Round-trip failed for {fc!r}"

    def test_required_fields_still_present_with_failure_class(self):
        """Adding failure_class must not remove any existing required fields."""
        outcome = ResourceOutcome(
            command="sync",
            resource_type="dashboards",
            id="abc-123",
            action_type="sync",
            status="failure",
            action_sub_type="",
            reason="HTTP 403",
            failure_class="http_4xx_403",
        )
        d = outcome.to_dict()
        for field in ("type", "command", "resource_type", "id", "action_type", "status", "action_sub_type", "reason"):
            assert field in d, f"Required field {field!r} missing from to_dict()"
        assert d["type"] == "outcome"


# ---------------------------------------------------------------------------
# JSON round-trip — emit() produces parseable NDJSON with failure_class
# ---------------------------------------------------------------------------


class TestFailureClassJsonRoundTrip:
    def test_emit_includes_failure_class_when_set(self):
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="42",
            action_type="import",
            status="failure",
            action_sub_type="",
            reason="HTTP 500",
            failure_class="http_5xx",
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            outcome.emit()

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["type"] == "outcome"
        assert parsed["failure_class"] == "http_5xx"
        assert parsed["reason"] == "HTTP 500"

    def test_emit_excludes_failure_class_when_empty(self):
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="42",
            action_type="import",
            status="failure",
            action_sub_type="",
            reason="HTTP 500",
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            outcome.emit()

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["type"] == "outcome"
        assert "failure_class" not in parsed

    def test_old_consumer_ignores_new_field(self):
        """Consumer parsing without failure_class field sees no KeyError."""
        outcome = ResourceOutcome(
            command="import",
            resource_type="monitors",
            id="42",
            action_type="import",
            status="failure",
            action_sub_type="",
            reason="HTTP 403",
            failure_class="http_4xx_403",
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            outcome.emit()

        parsed = json.loads(buf.getvalue().strip())
        # Old consumer only reads known fields — no KeyError on missing field
        _ = parsed["type"]
        _ = parsed["resource_type"]
        _ = parsed["status"]
        _ = parsed["reason"]
        # Old consumer does NOT access failure_class → no crash


# ---------------------------------------------------------------------------
# _emit call sites — verify failure_class is passed through
# ---------------------------------------------------------------------------


class TestEmitPassesFailureClass:
    """_emit must forward failure_class to ResourceOutcome and into NDJSON output."""

    def _make_handler_for_emit(self) -> ResourcesHandler:
        handler = MagicMock(spec=ResourcesHandler)
        handler.config = MagicMock()
        handler.config.emit_json = True
        handler.config.command = "import"
        return handler

    def test_emit_with_failure_class(self):
        """_emit with failure_class='http_5xx' produces that value in NDJSON."""
        handler = self._make_handler_for_emit()
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "dashboards",
                "abc",
                "import",
                "failure",
                reason="HTTP 500",
                failure_class="http_5xx",
            )
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["failure_class"] == "http_5xx"
        assert parsed["reason"] == "HTTP 500"

    def test_emit_without_failure_class_omits_field(self):
        """_emit with default failure_class='' must not include it in NDJSON."""
        handler = self._make_handler_for_emit()
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "dashboards",
                "abc",
                "import",
                "success",
            )
        parsed = json.loads(buf.getvalue().strip())
        assert "failure_class" not in parsed

    def test_emit_sanitize_reason_http_403_wires_failure_class(self):
        """Simulates the LIST emit path: _sanitize_reason → _emit with failure_class."""
        handler = self._make_handler_for_emit()
        err = _make_http_err(403)
        _reason, _fc = ResourcesHandler._sanitize_reason(err)

        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "monitors",
                "1",
                "import",
                "failure",
                reason=_reason,
                failure_class=_fc,
            )
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["reason"] == "HTTP 403"
        assert parsed["failure_class"] == "http_4xx_403"

    def test_emit_sanitize_reason_timeout_wires_failure_class(self):
        """Simulates the per-ID GET emit path: TimeoutError → http_timeout."""
        handler = self._make_handler_for_emit()
        err = TimeoutError("connection timed out")
        _reason, _fc = ResourcesHandler._sanitize_reason(err)

        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "monitors",
                "99",
                "import",
                "failure",
                reason=_reason,
                failure_class=_fc,
            )
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["reason"] == "TimeoutError"
        assert parsed["failure_class"] == "http_timeout"

    def test_emit_sanitize_reason_connection_error_wires_failure_class(self):
        """ResourceConnectionError → http_connection."""
        handler = self._make_handler_for_emit()
        err = ResourceConnectionError({"monitors": ["missing"]})
        _reason, _fc = ResourcesHandler._sanitize_reason(err)

        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "monitors",
                "77",
                "import",
                "skipped",
                reason=_reason,
                failure_class=_fc,
            )
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["reason"] == "connection_error"
        assert parsed["failure_class"] == "http_connection"

    def test_emit_noop_when_emit_json_false_no_output(self):
        """Even with failure_class set, no output when emit_json=False."""
        handler = self._make_handler_for_emit()
        handler.config.emit_json = False

        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourcesHandler._emit(
                handler,
                "dashboards",
                "abc",
                "import",
                "failure",
                reason="HTTP 500",
                failure_class="http_5xx",
            )
        assert buf.getvalue() == ""

    def test_emit_dedicated_timeout_path_has_http_timeout(self):
        """The inline TimeoutError branch in _import_get_resources_cb emits http_timeout.

        This path uses failure_class="http_timeout" directly rather than going through
        _sanitize_reason — verify the constant is correct and consistent.
        """
        handler = self._make_handler_for_emit()
        buf = StringIO()
        with patch("sys.stdout", buf):
            # Simulate what the dedicated `except TimeoutError:` branch does inline.
            ResourcesHandler._emit(
                handler,
                "monitors",
                "",
                "import",
                "failure",
                reason="TimeoutError",
                failure_class="http_timeout",
            )
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["reason"] == "TimeoutError"
        assert parsed["failure_class"] == "http_timeout"
