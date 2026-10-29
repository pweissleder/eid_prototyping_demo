"""Scheduler: drives ``Machine.tick`` from the injected clock.

The armed study alarm, the presenter test alarm and the 2 s activation-ack
deadline are all wall-clock deadlines held by the state machine. Polling every
``interval`` seconds (default 0.2 s) keeps firing ≤ interval late, well inside
QR-01's 1 s service budget, and copes with wall-clock jumps (e.g. NTP).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable

from .clock import Clock

log = logging.getLogger("wakequest.scheduler")


class Scheduler:
    def __init__(self, tick: Callable[[], Awaitable[None]], clock: Clock, interval: float = 0.2) -> None:
        self._tick = tick
        self._clock = clock
        self.interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="wakequest-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad tick stop the alarm clock
                log.exception("scheduler tick failed")
            await self._clock.sleep(self.interval)
