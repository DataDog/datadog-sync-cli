# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Tests for the Log class, focusing on the NDJSON emit_json mode.

When emit_json=True, all log calls should write JSON lines to stdout
with {"type":"log", "level":"...", "message":"..."} and stderr should
receive no output.
"""

import json
import logging
from io import StringIO
from unittest.mock import patch

from datadog_sync.utils.log import Log


class TestLogHumanMode:
    """Verify human mode (emit_json=False) still works as before."""

    def test_human_mode_uses_stderr(self):
        """In human mode, log output goes to stderr (via logging), not stdout."""
        buf = StringIO()
        logger = Log(verbose=False)
        with patch("sys.stdout", buf):
            logger.info("hello human")
        # stdout should be empty in human mode
        assert buf.getvalue() == ""

    def test_human_mode_exception_tracked(self):
        logger = Log(verbose=False)
        assert logger.exception_logged is False
        logger.error("something broke")
        assert logger.exception_logged is True


class TestLogJsonMode:
    """When emit_json=True, log calls emit NDJSON to stdout."""

    def test_info_emits_json_to_stdout(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("starting sync")
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["type"] == "log"
        assert parsed["level"] == "info"
        assert parsed["message"] == "starting sync"

    def test_debug_emits_json_to_stdout(self):
        logger = Log(verbose=True, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("debug detail")
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["type"] == "log"
        assert parsed["level"] == "debug"
        assert parsed["message"] == "debug detail"

    def test_warning_emits_json_to_stdout(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.warning("careful now")
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["type"] == "log"
        assert parsed["level"] == "warning"
        assert parsed["message"] == "careful now"

    def test_error_emits_json_to_stdout(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.error("it broke")
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["type"] == "log"
        assert parsed["level"] == "error"
        assert parsed["message"] == "it broke"

    def test_error_sets_exception_logged(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.error("it broke")
        assert logger.exception_logged is True

    def test_exception_emits_json_to_stdout(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.exception("unhandled")
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["type"] == "log"
        assert parsed["level"] == "error"
        assert parsed["message"] == "unhandled"

    def test_exception_sets_exception_logged(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.exception("unhandled")
        assert logger.exception_logged is True

    def test_resource_context_included(self):
        """resource_type and id should appear in the JSON when provided."""
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("syncing", resource_type="dashboards", _id="abc-123")
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["resource_type"] == "dashboards"
        assert parsed["id"] == "abc-123"
        assert parsed["message"] == "syncing"

    def test_resource_context_absent_when_not_provided(self):
        """resource_type and id should not appear when not provided."""
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("general message")
        parsed = json.loads(buf.getvalue().strip())
        assert "resource_type" not in parsed
        assert "id" not in parsed

    def test_multiple_calls_produce_valid_ndjson(self):
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("first")
            logger.warning("second")
            logger.error("third")
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert parsed["type"] == "log"

    def test_json_mode_silences_stderr(self):
        """In JSON mode, no output should go to stderr via logging handlers."""
        logger = Log(verbose=False, emit_json=True)
        # Capture stderr by intercepting the logging handler
        stderr_buf = StringIO()
        handler = logging.StreamHandler(stderr_buf)
        logger.logger.addHandler(handler)
        try:
            stdout_buf = StringIO()
            with patch("sys.stdout", stdout_buf):
                logger.info("should not appear on stderr")
            # The logger's handlers should have been configured to not emit
            # Check that no handlers on the logger write to stderr
            assert stderr_buf.getvalue() == ""
        finally:
            logger.logger.removeHandler(handler)

    def test_debug_suppressed_when_not_verbose(self):
        """In non-verbose JSON mode, debug messages should be suppressed."""
        logger = Log(verbose=False, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("should not appear")
        assert buf.getvalue() == ""

    def test_debug_emitted_when_verbose(self):
        """In verbose JSON mode, debug messages should be emitted."""
        logger = Log(verbose=True, emit_json=True)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("should appear")
        assert buf.getvalue().strip() != ""
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["level"] == "debug"


class TestLogDefaultEmitJson:
    """Verify emit_json defaults to False for backward compatibility."""

    def test_default_is_human_mode(self):
        logger = Log(verbose=False)
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("hello")
        assert buf.getvalue() == ""
