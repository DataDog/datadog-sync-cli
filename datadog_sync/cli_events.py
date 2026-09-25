# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from dataclasses import asdict, dataclass
from typing import Dict

from datadog_sync.utils.ndjson import write_ndjson_line


@dataclass(frozen=True)
class CommandError:
    command: str
    error_code: str
    message: str
    exit_code: int
    type: str = "error"
    status: str = "error"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def emit(self) -> None:
        write_ndjson_line(self.to_dict())
