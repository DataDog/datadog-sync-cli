# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from dataclasses import asdict, dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class CommandCapabilities:
    api_reads: bool
    api_writes: bool
    state_reads: bool
    state_writes: bool
    may_prompt: bool
    supports_plan: bool
    plan_command: Optional[str] = None

    def to_dict(self):
        return asdict(self)


COMMAND_CAPABILITIES: Dict[str, CommandCapabilities] = {
    "import": CommandCapabilities(True, False, True, True, False, False),
    "sync": CommandCapabilities(True, True, True, True, True, True, "diffs"),
    "diffs": CommandCapabilities(True, False, True, False, False, True, "diffs"),
    "migrate": CommandCapabilities(True, True, True, True, True, True, "diffs"),
    "reset": CommandCapabilities(True, True, True, True, True, False),
    "prune": CommandCapabilities(True, False, True, True, True, True, "prune --dry-run"),
    "schema": CommandCapabilities(False, False, False, False, False, False),
    "completions": CommandCapabilities(False, False, False, False, False, False),
}
