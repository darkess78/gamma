from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class LazySingleton(Generic[T]):
    """Small module-level cache for lazy service initialization."""

    def __init__(self) -> None:
        self._value: T | None = None

    def get(self, factory: Callable[[], T]) -> T:
        if self._value is None:
            self._value = factory()
        return self._value

    def set(self, value: T | None) -> None:
        self._value = value
