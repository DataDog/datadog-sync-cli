# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Protocol for the write-only source-state surface shared by State and ImportState.

The import command never reads existing state — it discards prior data, fetches
fresh resources from the API, and writes the new state. Both State (which loads
on construction) and ImportState (which skips the load) implement this protocol;
code on the import path can be typed against the protocol so it does not depend
on read accessors that ImportState does not expose.
"""

from typing import Any, Dict, List, Protocol, runtime_checkable

from datadog_sync.constants import Origin


@runtime_checkable
class SourceStateWriter(Protocol):
    """Write-only state surface used by the import command path.

    State satisfies this by exposing both reads (via .source / .destination
    properties) and these write methods. ImportState satisfies this by exposing
    only the write methods (no .source / .destination properties at all).
    """

    def set_source(self, resource_type: str, _id: str, resource: Dict[str, Any]) -> None:
        """Append or overwrite one resource in the in-memory source state."""
        ...

    def clear_source_type(self, resource_type: str) -> None:
        """Clear the in-memory source dict for one resource type."""
        ...

    def mark_source_authoritative(self, resource_types: List[str]) -> None:
        """Mark resource types as having a complete source load (for stale-file pruning)."""
        ...

    def clear_source_authoritative(self, resource_types: List[str]) -> None:
        """Clear authoritative markers, e.g. before a re-import."""
        ...

    def dump_state(self, origin: Origin = Origin.SOURCE) -> None:
        """Flush in-memory state to storage. Import callers should leave the default."""
        ...
