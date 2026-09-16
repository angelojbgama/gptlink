"""Reconnect backoff primitives."""

from __future__ import annotations

import random


class Backoff:
    def __init__(self, *, base: float = 1.0, cap: float = 30.0, jitter: float = 0.2) -> None:
        if base <= 0 or cap < base or not 0 <= jitter <= 1:
            raise ValueError("invalid backoff parameters")
        self.base = base
        self.cap = cap
        self.jitter = jitter

    def delay(self, attempt: int, *, random_value: float | None = None) -> float:
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        value = min(self.cap, self.base * (2**attempt))
        if self.jitter == 0:
            return value
        sample = random.random() if random_value is None else random_value
        return min(self.cap, value * (1 - self.jitter + 2 * self.jitter * sample))

    def reset(self) -> None:
        """Compatibility hook for callers that track attempts externally."""
