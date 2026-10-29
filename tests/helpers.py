"""Shared helpers for WakeQuest tests."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from wakequest.clock import FakeClock
from wakequest.settings import Settings
from wakequest.state import Effects, Machine


def hello(**over: Any) -> dict[str, Any]:
    msg = {
        "type": "hello", "protocol": 1, "deviceId": "wq-a1b2c3", "firmware": "0.1.0",
        "tapActiveLevel": "LOW", "sensorStable": True, "indicator": False, "sessionId": None,
        "tapSeq": 0, "uptimeMs": 5000,
    }
    msg.update(over)
    return msg


def local_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def new_machine(clock: FakeClock, **kw: Any) -> Machine:
    return Machine(localize=clock.localize, **kw)


def make_ready(m: Machine, clock: FakeClock, browser: str = "b1") -> None:
    now = clock.now()
    m.connect_requested(now)
    m.link_hello(now, hello())
    m.browser_connected(now, browser)
    m.browser_message(now, browser, {"type": "audio_test_ok"})


def add_alarm(m: Machine, clock: FakeClock, minutes: int = 5, name: str = "Wake") -> str:
    t = clock.now() + timedelta(minutes=minutes)
    eff = m.create_alarm(clock.now(), {"name": name, "localTime": local_str(t)})
    return eff.result.id


def ring_study(m: Machine, clock: FakeClock, *, ack: bool = True, sound: bool = True) -> str:
    """Create + arm an alarm, advance to its time, tick; optionally ack and confirm sound."""
    make_ready(m, clock)
    aid = add_alarm(m, clock, minutes=1)
    m.arm_alarm(clock.now(), aid)
    scheduled = datetime.fromisoformat(m.alarms[aid].scheduledAt)
    clock.advance_sync((scheduled - clock.now()).total_seconds())
    eff = m.tick(clock.now())
    assert m.ringing is not None, eff
    sid = m.ringing.sessionId
    if ack:
        m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid,
                                       "indicator": True, "ignored": False})
    if sound:
        clock.advance_sync(0.3)
        m.browser_message(clock.now(), "b1", {"type": "sound_started", "sessionId": sid})
    return sid


def tap(m: Machine, clock: FakeClock, seq: int, session_id: Any, uptime: int | None = None) -> Effects:
    return m.device_message(clock.now(), {"type": "tap", "seq": seq, "sessionId": session_id,
                                          "uptimeMs": uptime if uptime is not None else 10_000 + seq})


def names(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events]


def fast_settings(data_dir: Any, **over: Any) -> Settings:
    s = Settings(
        data_dir=str(data_dir), device_mode="fake", test_mode=True, test_alarm_delay_s=0.2,
        ping_interval_s=0.2, ping_timeout_s=0.4, watchdog_s=0.6, ack_timeout_s=0.4,
        hello_timeout_s=0.5, open_timeout_s=0.5, resolve_timeout_s=1.0,
        backoff_initial_s=0.05, backoff_cap_active_s=0.2, backoff_cap_idle_s=0.5,
        tick_interval_s=0.02, fake_status_interval_s=0.2,
    )
    for k, v in over.items():
        setattr(s, k, v)
    return s


async def wait_until(pred: Callable[[], Any], timeout: float = 3.0, interval: float = 0.01) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        v = pred()
        if v:
            return v
        if time.monotonic() > deadline:
            raise AssertionError("condition not met within %.1fs" % timeout)
        await asyncio.sleep(interval)


def wait_sync(pred: Callable[[], Any], timeout: float = 3.0, interval: float = 0.02) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        v = pred()
        if v:
            return v
        if time.monotonic() > deadline:
            raise AssertionError("condition not met within %.1fs" % timeout)
        time.sleep(interval)
