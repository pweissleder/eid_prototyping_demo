"""Scheduler tests with FakeClock: fires on time (QR-01 service budget ≤ 1 s)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from wakequest.clock import FakeClock
from wakequest.scheduler import Scheduler

from .helpers import add_alarm, make_ready, names, new_machine


@pytest.fixture
async def rig():
    clock = FakeClock()
    m = new_machine(clock)
    fired: list[tuple[datetime, list[str]]] = []

    async def tick() -> None:
        eff = m.tick(clock.now())
        if eff.events:
            fired.append((clock.now(), names(eff.events)))

    sched = Scheduler(tick, clock, interval=0.2)
    sched.start()
    await clock.advance(0)
    yield clock, m, fired
    await sched.stop()


async def test_study_alarm_fires_within_1s_not_before(rig):
    clock, m, fired = rig
    make_ready(m, clock)
    aid = add_alarm(m, clock, minutes=1)
    m.arm_alarm(clock.now(), aid)
    due = datetime.fromisoformat(m.alarms[aid].scheduledAt)
    await clock.advance(59.9)
    assert fired == []
    await clock.advance(1.0)
    assert len(fired) == 1
    at, evs = fired[0]
    assert evs[0] == "ring_started"
    late = (at - due).total_seconds()
    assert 0 <= late <= 1.0, late


async def test_test_alarm_fires_after_delay(rig):
    clock, m, fired = rig
    make_ready(m, clock)
    m.schedule_test_alarm(clock.now())
    start = clock.now()
    await clock.advance(9.9)
    assert fired == []
    await clock.advance(1.0)
    at, evs = fired[0]
    assert evs[0] == "ring_started" and m.ringing.mode == "demo"
    assert 10.0 <= (at - start).total_seconds() <= 11.0


async def test_activation_missing_flagged_by_scheduler(rig):
    clock, m, fired = rig
    make_ready(m, clock)
    m.schedule_test_alarm(clock.now())
    await clock.advance(10.2)
    assert m.ringing.activationStatus == "sent"
    await clock.advance(2.2)
    assert any("activation_missing" in evs for _, evs in fired)
    assert m.ringing.activationStatus == "missing"


async def test_tick_errors_do_not_stop_scheduler():
    clock = FakeClock()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")

    s = Scheduler(tick, clock, interval=0.5)
    s.start()
    await clock.advance(1.6)
    await s.stop()
    assert calls >= 3


async def test_real_clock_scheduler_runs():
    from wakequest.clock import SystemClock

    n = 0

    async def tick() -> None:
        nonlocal n
        n += 1

    s = Scheduler(tick, SystemClock(), interval=0.01)
    s.start()
    await asyncio.sleep(0.1)
    await s.stop()
    assert n >= 3
