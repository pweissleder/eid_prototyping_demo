"""Coordinator: runs the pure ``Machine`` and executes its effects.

Every input is serialized through one asyncio lock, so the state machine sees
one transition at a time (completion is a single transition; duplicates cannot
race). Effects are executed in order: events are appended to the log, alarms
are persisted, device messages are sent, and the full snapshot is pushed to
every connected browser.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any, Callable

from .clock import Clock, SystemClock
from .device_link import DeviceLink
from .fake_device import FakeDevice
from .models import snapshot_json
from .scheduler import Scheduler
from .settings import Settings
from .state import Effects, Machine
from .storage import Storage

log = logging.getLogger("wakequest.service")


class Service:
    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self.storage = Storage(settings.data_dir)
        self.machine = Machine(
            alarms=self.storage.load_alarms(),
            events=self.storage.load_events(),
            localize=self.clock.localize,
            test_alarm_delay_s=settings.test_alarm_delay_s,
            ack_timeout_s=settings.ack_timeout_s,
            device_host=settings.device_host,
            device_port=settings.device_port,
            test_mode=settings.test_mode,
        )
        self.lock = asyncio.Lock()
        self.link = DeviceLink(self, settings, self.clock)
        self.scheduler = Scheduler(self.tick, self.clock, settings.tick_interval_s)
        self.fake: FakeDevice | None = None
        self.browsers: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._bid = itertools.count(1)
        self.started = False

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        if self.settings.device_mode == "fake":
            self.fake = FakeDevice("127.0.0.1", self.settings.fake_device_port,
                                   status_interval=self.settings.fake_status_interval_s)
            await self.fake.start()
            self.link.set_target("127.0.0.1", self.fake.port)
            self.machine.device_host = "127.0.0.1"
            self.machine.device_port = self.fake.port
        await self.run(self.machine.startup)
        self.scheduler.start()
        self.started = True

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.run(self.machine.shutdown)  # while the link is up: deactivates a ringing device
        await self.link.stop()
        if self.fake is not None:
            await self.fake.stop()
        self.started = False

    # ---- core ----------------------------------------------------------
    async def run(self, fn: Callable[..., Effects], *args: Any) -> Any:
        """Apply one state-machine input under the lock and execute its effects."""
        async with self.lock:
            eff = fn(self.clock.now(), *args)
            await self._execute(eff)
            return eff.result

    async def _execute(self, eff: Effects) -> None:
        for ev in eff.events:
            try:
                self.storage.append_event(ev)
            except OSError:
                log.exception("could not append event %s", ev.get("event"))
        if eff.persist_alarms:
            self.storage.save_alarms(self.machine.alarms_list())
        for msg in eff.device:
            ok = await self.link.send(msg)
            if not ok:
                log.warning("device message not sent (link down): %s", msg)
        if eff.push:
            self._broadcast_snapshot()

    def snapshot(self) -> dict[str, Any]:
        return snapshot_json(self.machine.snapshot(self.clock.now(), bump=False))

    def _broadcast_snapshot(self) -> None:
        if not self.browsers:
            self.machine.rev += 1
            return
        snap = snapshot_json(self.machine.snapshot(self.clock.now()))
        for q in self.browsers.values():
            q.put_nowait(snap)

    async def tick(self) -> None:
        await self.run(self.machine.tick)

    # ---- device connection --------------------------------------------
    async def connect_device(self) -> None:
        await self.run(self.machine.connect_requested)
        await self.link.request_connect()

    async def disconnect_device(self) -> None:
        await self.link.stop()
        await self.run(self.machine.disconnect_requested)

    # LinkHandler protocol
    def run_active(self) -> bool:
        return self.machine.run_active()

    async def on_link_attempt(self, reconnecting: bool) -> None:
        await self.run(self.machine.link_attempt, reconnecting)

    async def on_link_hello(self, hello: dict[str, Any]) -> None:
        await self.run(self.machine.link_hello, hello)

    async def on_link_failed(self, error: str) -> None:
        await self.run(self.machine.link_failed, error)

    async def on_link_retry_failed(self, error: str, attempt: int) -> None:
        await self.run(self.machine.link_retry_failed, error, attempt)

    async def on_link_lost(self, reason: str) -> None:
        await self.run(self.machine.link_lost, reason)

    async def on_device_message(self, msg: dict[str, Any]) -> None:
        await self.run(self.machine.device_message, msg)

    # ---- browsers ------------------------------------------------------
    async def add_browser(self) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        bid = f"b{next(self._bid)}"
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self.lock:
            self.browsers[bid] = q
            eff = self.machine.browser_connected(self.clock.now(), bid)
            await self._execute(eff)  # pushes the first snapshot to the new browser too
        return bid, q

    async def remove_browser(self, bid: str) -> None:
        async with self.lock:
            self.browsers.pop(bid, None)
            eff = self.machine.browser_disconnected(self.clock.now(), bid)
            await self._execute(eff)

    async def browser_message(self, bid: str, msg: Any) -> None:
        await self.run(self.machine.browser_message, bid, msg)
