# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Shared NDJSON writer for the ``--json`` event stream.

In ``--json`` mode, stdout carries a single NDJSON event stream where each line
is a **discriminated union** keyed by ``"type"``:

``"type": "outcome"``
    Resource-level result emitted once per resource per command invocation.
    Fields: ``command``, ``resource_type``, ``id``, ``action_type``,
    ``status``, ``action_sub_type``, ``reason``.
    See :class:`sync_report.ResourceOutcome`.

``"type": "log"``
    Operational log event (info, warning, error, debug).
    Fields: ``level``, ``message``, and optionally ``resource_type``, ``id``.
    See :class:`log.Log` and :class:`log._NdjsonHandler`.

``"type": "error"``
    Command-level failure emitted before any resource work could run (e.g. a
    usage error or an unhandled runtime exception). Fields: ``command``,
    ``error_code``, ``message``, ``exit_code``, ``status``.
    See :class:`cli_events.CommandError`.

``"type": "summary"``
    Terminal invocation summary emitted exactly once, as the final line of
    every structured invocation that did not already end in an ``error``
    event. Fields: ``command``, ``status``, ``counts``, ``duration_ms``,
    ``exit_code``. See :class:`cli_events.InvocationSummary`.

Every structured invocation ends with exactly one ``summary`` or ``error``
event as its final line.

Every event is a single JSON object terminated by ``\\n``.  Consumers should
filter by ``type`` and ignore unknown type values and unknown fields
for forward-compatibility.
"""

from __future__ import annotations

import json
import sys


def write_ndjson_line(event: dict) -> None:
    """Write a single NDJSON event to stdout and flush.

    All NDJSON output in the CLI must go through this function so that
    encoding, flushing, and error handling are consistent.

    On Unix, ``datadog_sync.cli_runtime.reset_sigpipe`` restores the default
    SIGPIPE handler at the process boundary, so a downstream reader closing
    the pipe early (e.g. ``| head``) terminates the process via the signal
    before this function's write would even run. The ``except
    BrokenPipeError`` below is therefore primarily a fallback for contexts
    where that reset does not apply: Windows, which has no SIGPIPE, or
    embedded/library use where this module's writer runs without the CLI's
    process-entry handling.
    """
    try:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass
