# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

import json
import sys
from dataclasses import dataclass


@dataclass
class ResourceOutcome:
    """A single resource-level outcome emitted as a JSON line to stdout.

    Field names and values are aligned with the CLI's ``datadog.org-sync.action``
    metric tags so that downstream consumers (e.g. the managed-sync Go worker) can
    forward them as statsd tags without translation.

    Metric-tag mapping::

        JSON field       CLI metric tag        Values
        ─────────────    ──────────────        ──────
        resource_type    resource_type:X       dashboards, monitors, ...
        id               id:X                  resource identifier (empty for type-level failures)
        action_type      action_type:X         import | sync | delete
        status           status:X              success | skipped | failure | filtered
        action_sub_type  action_sub_type:X     create | update | "" (sync only)
        reason           reason:X              freetext explanation

    Note: ``filtered`` is a JSON-only status. The CLI metric (``datadog.org-sync.action``)
    is not emitted for filtered resources, so this value has no metric-tag counterpart.

    Stdout/stderr contract: JSON outcomes go to stdout; all logging and progress output
    goes to stderr. Machine consumers should pipe stdout only.
    """

    resource_type: str
    id: str
    action_type: str  # "import" | "sync" | "delete"
    status: str  # "success" | "skipped" | "failure" | "filtered"
    action_sub_type: str  # "create" | "update" | "" (only populated on sync success)
    reason: str  # empty for success, explanation for skip/fail

    def to_dict(self) -> dict:
        return {
            "resource_type": self.resource_type,
            "id": self.id,
            "action_type": self.action_type,
            "status": self.status,
            "action_sub_type": self.action_sub_type,
            "reason": self.reason,
        }

    def emit(self) -> None:
        """Write this outcome as a single JSON line to stdout."""
        print(json.dumps(self.to_dict()), file=sys.stdout)
