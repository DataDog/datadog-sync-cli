# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import os
import signal
import subprocess
import sys

import pytest

from datadog_sync.cli_runtime import reset_sigpipe

# Repeats `sync --help` so total output (~17KB per call) comfortably exceeds any
# OS pipe buffer. That forces a write after the reader has closed, which is what
# triggers a real SIGPIPE. A single call can fit entirely in the pipe buffer, in
# which case the producer exits cleanly and the test can't tell a fixed process
# from an unfixed one.
_PRODUCER = """
from datadog_sync.cli import cli
for _ in range(100):
    try:
        cli(['sync', '--help'])
    except SystemExit:
        pass
"""


@pytest.mark.skipif(os.name == "nt", reason="SIGPIPE is a Unix contract")
def test_reset_sigpipe_restores_default_handler():
    from unittest.mock import patch

    with patch("signal.signal") as set_signal:
        reset_sigpipe()
    set_signal.assert_called_once_with(signal.SIGPIPE, signal.SIG_DFL)


@pytest.mark.skipif(os.name == "nt", reason="SIGPIPE is a Unix contract")
def test_help_pipe_closes_without_broken_pipe_traceback():
    producer = subprocess.Popen(
        [sys.executable, "-c", _PRODUCER],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert producer.stdout is not None
    producer.stdout.readline()
    producer.stdout.close()
    producer.wait(timeout=30)
    stderr = producer.stderr.read() if producer.stderr is not None else ""
    # The discriminating signal: with reset_sigpipe() wired up, the process is
    # terminated by the SIGPIPE signal itself. Without it, Python's default
    # SIGPIPE handler turns the signal into a BrokenPipeError, which the
    # top-level `except Exception` handler in DatadogSyncGroup.main swallows
    # into a plain `SystemExit(1)` with no traceback printed - so stderr being
    # clean does not by itself prove the fix is in place.
    assert producer.returncode == -signal.SIGPIPE
    assert "BrokenPipeError" not in stderr
    assert "Traceback" not in stderr
