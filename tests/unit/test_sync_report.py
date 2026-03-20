# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import json
from io import StringIO
from unittest.mock import patch

from datadog_sync.utils.sync_report import ResourceOutcome, _REASON_MAX_LEN


class TestResourceOutcome:
    def test_create_outcome(self):
        outcome = ResourceOutcome(
            resource_type="dashboards",
            id="abc-123",
            action_type="sync",
            status="success",
            action_sub_type="create",
            reason="",
        )
        assert outcome.resource_type == "dashboards"
        assert outcome.id == "abc-123"
        assert outcome.action_type == "sync"
        assert outcome.status == "success"
        assert outcome.action_sub_type == "create"
        assert outcome.reason == ""

    def test_create_outcome_with_reason(self):
        outcome = ResourceOutcome(
            resource_type="monitors",
            id="12345",
            action_type="import",
            status="skipped",
            action_sub_type="",
            reason="Synthetics monitors are created by synthetics tests.",
        )
        assert outcome.status == "skipped"
        assert outcome.reason == "Synthetics monitors are created by synthetics tests."

    def test_outcome_to_dict(self):
        outcome = ResourceOutcome(
            resource_type="dashboards",
            id="abc-123",
            action_type="sync",
            status="failure",
            action_sub_type="",
            reason="500 Internal Server Error",
        )
        d = outcome.to_dict()
        assert d == {
            "resource_type": "dashboards",
            "id": "abc-123",
            "action_type": "sync",
            "status": "failure",
            "action_sub_type": "",
            "reason": "500 Internal Server Error",
        }


class TestEmit:
    def test_emit_writes_json_line_to_stdout(self):
        outcome = ResourceOutcome("dashboards", "abc-123", "sync", "success", "create", "")
        buf = StringIO()
        with patch("sys.stdout", buf):
            outcome.emit()

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["resource_type"] == "dashboards"
        assert parsed["id"] == "abc-123"
        assert parsed["action_type"] == "sync"
        assert parsed["status"] == "success"
        assert parsed["action_sub_type"] == "create"
        assert parsed["reason"] == ""

    def test_emit_one_line_per_call(self):
        """Each emit() produces exactly one line."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourceOutcome("dashboards", "a", "sync", "success", "create", "").emit()
            ResourceOutcome("monitors", "1", "sync", "failure", "", "err").emit()

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 2

    def test_emit_is_valid_jsonl(self):
        """Multiple emits produce valid JSONL (each line is independent JSON)."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourceOutcome("dashboards", "a", "sync", "success", "create", "").emit()
            ResourceOutcome("monitors", "1", "import", "skipped", "", "synth alert").emit()
            ResourceOutcome("monitors", "2", "sync", "failure", "", "500").emit()

        lines = buf.getvalue().strip().split("\n")
        for line in lines:
            parsed = json.loads(line)
            assert "resource_type" in parsed
            assert "status" in parsed

    def test_emit_includes_reason(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourceOutcome("monitors", "123", "sync", "failure", "", "403 Forbidden").emit()

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["reason"] == "403 Forbidden"

    def test_emit_all_statuses(self):
        """All 4 status values emit valid JSON."""
        statuses = ["success", "skipped", "failure", "filtered"]
        buf = StringIO()
        with patch("sys.stdout", buf):
            for status in statuses:
                ResourceOutcome("dashboards", "x", "sync", status, "", "").emit()

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 4
        emitted_statuses = {json.loads(line)["status"] for line in lines}
        assert emitted_statuses == set(statuses)

    def test_emit_all_action_types(self):
        """All 3 action_type values emit valid JSON."""
        action_types = ["import", "sync", "delete"]
        buf = StringIO()
        with patch("sys.stdout", buf):
            for action_type in action_types:
                ResourceOutcome("dashboards", "x", action_type, "success", "", "").emit()

        lines = buf.getvalue().strip().split("\n")
        emitted_action_types = {json.loads(line)["action_type"] for line in lines}
        assert emitted_action_types == set(action_types)

    def test_emit_action_sub_types(self):
        """action_sub_type values emit correctly."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            ResourceOutcome("dashboards", "a", "sync", "success", "create", "").emit()
            ResourceOutcome("dashboards", "b", "sync", "success", "update", "").emit()
            ResourceOutcome("dashboards", "c", "import", "success", "", "").emit()

        lines = buf.getvalue().strip().split("\n")
        assert json.loads(lines[0])["action_sub_type"] == "create"
        assert json.loads(lines[1])["action_sub_type"] == "update"
        assert json.loads(lines[2])["action_sub_type"] == ""


class TestReasonTruncation:
    def test_short_reason_unchanged(self):
        outcome = ResourceOutcome("dashboards", "a", "sync", "failure", "", "short error")
        assert outcome.reason == "short error"

    def test_long_reason_truncated(self):
        long_reason = "x" * 2000
        outcome = ResourceOutcome("dashboards", "a", "sync", "failure", "", long_reason)
        assert len(outcome.reason) == _REASON_MAX_LEN + len("...(truncated)")
        assert outcome.reason.endswith("...(truncated)")
        assert outcome.reason.startswith("x" * _REASON_MAX_LEN)

    def test_exact_limit_not_truncated(self):
        exact_reason = "y" * _REASON_MAX_LEN
        outcome = ResourceOutcome("dashboards", "a", "sync", "failure", "", exact_reason)
        assert outcome.reason == exact_reason

