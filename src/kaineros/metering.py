"""Token metering — a proxy for how much compute each brain would need to run locally (spec §33).

One meter per brain (deep = extractor + judge, fast = phraser). The panels show total and
last-hour counts so you can watch the deep brain's cost while it builds a wiki, and judge whether
a local model could carry that load.
"""
from __future__ import annotations

import time

WINDOW = 3600.0  # "last hour"


class TokenMeter:
    def __init__(self) -> None:
        self.total = 0
        self._events: list[tuple[float, int]] = []  # (at, tokens), pruned to the window

    def add(self, tokens: int, at: float | None = None) -> None:
        if not tokens or tokens <= 0:
            return
        self.total += int(tokens)
        self._events.append((time.time() if at is None else at, int(tokens)))

    def last_hour(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cutoff = now - WINDOW
        self._events = [(a, n) for a, n in self._events if a >= cutoff]
        return sum(n for _, n in self._events)
