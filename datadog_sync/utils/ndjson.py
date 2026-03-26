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
    """
    try:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass
