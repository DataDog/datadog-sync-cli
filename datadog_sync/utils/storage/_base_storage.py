# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict
from collections import defaultdict


@dataclass
class StorageData:
    source: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))
    destination: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))


class BaseStorage(ABC):
    """Base class for storage"""

    @abstractmethod
    def get(self, origin) -> StorageData:
        """Get resouces state from storage"""
        pass

    @abstractmethod
    def put(self, origin, data: StorageData) -> None:
        """Write resources into storage"""
        pass
