# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Literal

from datadog_sync.utils.ndjson import write_ndjson_line


_REASON_MAX_LEN = 1024


@dataclass
class ResourceOutcome:
    """A single resource-level outcome emitted as a JSON line to stdout.

    Field names and values are aligned with the CLI's ``datadog.org-sync.action``
    metric tags so that downstream consumers can
    forward them as statsd tags without translation.

    Metric-tag mapping::

        JSON field       CLI metric tag        Values
        ─────────────    ──────────────        ──────
        resource_type    resource_type:X       dashboards, monitors, ...
        id               id:X                  resource identifier (empty for type-level failures)
        action_type      action_type:X         import | sync | delete
        status           status:X              success | skipped | failure | filtered
        action_sub_type  action_sub_type:X     create | update | "" (sync only)
        reason           reason:X              freetext explanation (truncated to 1024 chars)

    Note: ``filtered`` is a JSON-only status. The CLI metric (``datadog.org-sync.action``)
    is not emitted for filtered resources, so this value has no metric-tag counterpart.

    The ``command`` field reflects the CLI command that was invoked (``import``,
    ``sync``, ``diffs``, ``migrate``, ``reset``).  This lets consumers distinguish
    dry-run from live: a ``diffs`` outcome describes *intended* actions, while a
    ``sync`` outcome describes *completed* mutations.

    Stdout/stderr contract: In ``--json`` mode, stdout carries a single NDJSON event
    stream where each line is a discriminated union with a ``type`` field.  Outcome
    events carry ``"type": "outcome"``; log events carry ``"type": "log"``.
    Machine consumers should pipe stdout and filter by ``type``.
    """

    command: Literal["import", "sync", "diffs", "migrate", "reset"]
    resource_type: str
    id: str
    action_type: Literal["import", "sync", "delete"]
    status: Literal["success", "skipped", "failure", "filtered"]
    action_sub_type: Literal["create", "update", ""]  # only populated on sync success
    reason: str  # empty for success, explanation for skip/fail

    def __post_init__(self) -> None:
        if len(self.reason) > _REASON_MAX_LEN:
            self.reason = self.reason[:_REASON_MAX_LEN] + "...(truncated)"

    def to_dict(self) -> Dict[str, str]:
        return {"type": "outcome", **asdict(self)}

    def emit(self) -> None:
        """Write this outcome as a single JSON line to stdout."""
        write_ndjson_line(self.to_dict())
