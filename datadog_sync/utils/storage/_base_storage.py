from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict
from collections import defaultdict


@dataclass
class StorageItem:
    source: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))
    destination: Dict[str, Any] = field(default_factory=lambda: defaultdict(dict))


class BaseStorage(ABC):
    """Base class for storage"""

    @abstractmethod
    def get(self) -> StorageItem:
        """Get resouces state from storage"""
        pass

    @abstractmethod
    def put(self, data: StorageItem) -> None:
        """Write resources into storage"""
        pass
