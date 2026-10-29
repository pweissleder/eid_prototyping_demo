"""Injectable clock.

All service timestamps come from ``Clock.now()``: an aware datetime in the
host's local timezone. Durations that must not jump with wall-clock changes use
``monotonic()``. ``FakeClock`` lets tests move time deterministically.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...

    def localize(self, naive: datetime) -> datetime: ...


class SystemClock:
    """Real clock in the host's local timezone."""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))

    def localize(self, naive: datetime) -> datetime:
        # astimezone() on a naive datetime interprets it as system local time
        # (including that date's UTC offset).
        return naive.astimezone()


DEFAULT_FAKE_START = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone(timedelta(hours=1)))


class FakeClock:
    """Deterministic clock for tests.

    ``sleep()`` only returns when a test calls ``advance()`` past its deadline.
    ``set_now``/``advance_sync`` move time without waking sleepers (for pure
    state-machine tests that never sleep).
    """

    def __init__(self, start: datetime | None = None) -> None:
        start = start or DEFAULT_FAKE_START
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start
        self._mono = 1000.0
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def localize(self, naive: datetime) -> datetime:
        return naive.replace(tzinfo=self._now.tzinfo)

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._mono + seconds, next(self._counter), fut))
        await fut

    def advance_sync(self, seconds: float) -> None:
        self._mono += seconds
        self._now += timedelta(seconds=seconds)

    @property
    def pending_sleepers(self) -> int:
        return sum(1 for *_, f in self._sleepers if not f.done())

    async def _settle(self) -> None:
        for _ in range(10):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        """Move time forward, waking each sleeper at its own deadline in order."""
        target = self._mono + seconds
        await self._settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, fut = heapq.heappop(self._sleepers)
            if deadline > self._mono:
                self.advance_sync(deadline - self._mono)
            if not fut.done():
                fut.set_result(None)
            await self._settle()
        if target > self._mono:
            self.advance_sync(target - self._mono)
        await self._settle()


def iso(dt: datetime | None) -> str | None:
    """ISO 8601 with millisecond precision and UTC offset."""
    return None if dt is None else dt.isoformat(timespec="milliseconds")


def ms_between(start: datetime, end: datetime) -> int:
    return int(round((end - start).total_seconds() * 1000))
