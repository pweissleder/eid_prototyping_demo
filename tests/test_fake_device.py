"""The fake device must follow docs/protocol.md exactly (raw websockets client)."""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from wakequest.fake_device import FakeDevice


@pytest.fixture
async def dev():
    d = FakeDevice(status_interval=0.1, indicator_timeout_s=0.3)
    await d.start()
    yield d
    await d.stop()


async def recv(ws, type_: str | None = None, timeout: float = 1.0) -> dict:
    """Next message (of a type, skipping periodic status) within an overall deadline."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        remaining = end - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        msg = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        if type_ is None and msg["type"] != "status":
            return msg
        if msg["type"] == type_:
            return msg


async def status_where(ws, pred, timeout: float = 1.0) -> dict:
    """First status (skipping stale buffered ones) that satisfies pred."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        st = await recv(ws, "status", timeout=max(0.01, end - loop.time()))
        if pred(st):
            return st


async def cmd(ws, msg) -> dict:
    await ws.send(json.dumps(msg) if not isinstance(msg, (str, bytes)) else msg)
    return await recv(ws)


async def test_hello_first_then_status(dev):
    async with connect(dev.uri, proxy=None) as ws:
        h = json.loads(await ws.recv())
        assert h["type"] == "hello" and h["protocol"] == 1 and h["deviceId"].startswith("wq-")
        assert h.keys() >= {"firmware", "tapActiveLevel", "sensorStable", "indicator", "sessionId",
                            "tapSeq", "uptimeMs"}
        assert h["tapActiveLevel"] == "LOW" and h["sessionId"] is None
        s = await recv(ws, "status")
        assert s.keys() >= {"sensorStable", "indicator", "indicatorTimedOut", "sessionId", "tapSeq",
                            "uptimeMs", "rssi"}
        assert ws.subprotocol is None


async def test_example_exchange(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        assert await cmd(ws, {"type": "sync", "sessionId": None}) == {
            "type": "ack", "of": "sync", "sessionId": None, "indicator": False, "ignored": False}
        a = await cmd(ws, {"type": "activate", "sessionId": "s-1"})
        assert a == {"type": "ack", "of": "activate", "sessionId": "s-1", "indicator": True, "ignored": False}
        t = await dev.tap()
        got = await recv(ws, "tap")
        assert got == t and got["seq"] == 1 and got["sessionId"] == "s-1"
        d = await cmd(ws, {"type": "deactivate", "sessionId": "s-1", "reason": "completed"})
        assert d == {"type": "ack", "of": "deactivate", "sessionId": None, "indicator": False, "ignored": False}


async def test_deactivate_other_session_is_ignored(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        await cmd(ws, {"type": "activate", "sessionId": "s-1"})
        d = await cmd(ws, {"type": "deactivate", "sessionId": "s-2", "reason": "aborted"})
        assert d["ignored"] is True and d["sessionId"] == "s-1" and d["indicator"] is True
        # sync null clears regardless of the held id
        s = await cmd(ws, {"type": "sync", "sessionId": None})
        assert s["sessionId"] is None and s["indicator"] is False and s["ignored"] is False


async def test_deactivate_when_holding_none_acks_not_ignored(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        d = await cmd(ws, {"type": "deactivate", "sessionId": "s-9", "reason": "reset"})
        assert d["ignored"] is False and d["indicator"] is False


async def test_same_session_activate_changes_nothing_and_no_relight_after_timeout(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        await cmd(ws, {"type": "activate", "sessionId": "s-1"})
        started = dev._activated_at
        await asyncio.sleep(0.05)
        a = await cmd(ws, {"type": "activate", "sessionId": "s-1"})
        assert a["indicator"] is True and dev._activated_at == started  # no timer restart
        # wait for the (shortened) 15-min safety timeout
        st = await status_where(ws, lambda m: m["indicatorTimedOut"])
        assert st["indicator"] is False and st["sessionId"] == "s-1"
        for t in ("activate", "sync"):
            a = await cmd(ws, {"type": t, "sessionId": "s-1"})
            assert a["of"] == t and a["indicator"] is False and a["sessionId"] == "s-1"
        assert dev.indicator is False and dev.indicator_timed_out is True  # unchanged
        # a different session is a state change: lights and clears indicatorTimedOut
        a = await cmd(ws, {"type": "activate", "sessionId": "s-2"})
        assert a["indicator"] is True
        st = await status_where(ws, lambda m: m["sessionId"] == "s-2")
        assert st["indicatorTimedOut"] is False and st["indicator"] is True


@pytest.mark.parametrize("raw, code", [
    ("not json", "bad_json"),
    ("[1,2]", "bad_json"),
    (b"\x01\x02", "bad_json"),
    ({"sessionId": "s"}, "bad_field"),
    ({"type": 5, "sessionId": "s"}, "bad_field"),
    ({"type": "reboot"}, "unknown_type"),
    ({"type": "activate"}, "bad_field"),
    ({"type": "activate", "sessionId": ""}, "bad_field"),
    ({"type": "activate", "sessionId": "x" * 65}, "bad_field"),
    ({"type": "activate", "sessionId": None}, "bad_field"),
    ({"type": "activate", "sessionId": 7}, "bad_field"),
    ({"type": "deactivate", "sessionId": None, "reason": "completed"}, "bad_field"),
    ({"type": "deactivate", "sessionId": "s"}, "bad_field"),
    ({"type": "deactivate", "sessionId": "s", "reason": "bored"}, "bad_field"),
    ({"type": "sync"}, "bad_field"),
    ({"type": "sync", "sessionId": ""}, "bad_field"),
])
async def test_validation_errors_never_followed_by_ack(dev, raw, code):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        payload = raw if isinstance(raw, (str, bytes)) else json.dumps(raw)
        await ws.send(payload)
        err = await recv(ws)
        assert err["type"] == "error" and err["code"] == code and "detail" in err
        # the next non-status message must be the ack of a *later* command, not of the bad one
        nxt = await cmd(ws, {"type": "sync", "sessionId": None})
        assert nxt["type"] == "ack" and nxt["of"] == "sync"
        assert dev.session_id is None


async def test_session_id_of_64_chars_accepted(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        a = await cmd(ws, {"type": "activate", "sessionId": "x" * 64})
        assert a["type"] == "ack" and a["indicator"] is True


async def test_unknown_fields_ignored(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        a = await cmd(ws, {"type": "activate", "sessionId": "s-1", "extra": {"x": 1}})
        assert a["type"] == "ack"


async def test_second_client_replaces_first_and_only_newest_is_obeyed(dev):
    ws1 = await connect(dev.uri, proxy=None)
    await recv(ws1, "hello")
    async with connect(dev.uri, proxy=None) as ws2:
        await recv(ws2, "hello")
        with pytest.raises(ConnectionClosed):
            for _ in range(20):
                await ws1.send(json.dumps({"type": "activate", "sessionId": "old-client"}))
                await asyncio.wait_for(ws1.recv(), 0.5)
        assert dev.session_id != "old-client"
        a = await cmd(ws2, {"type": "activate", "sessionId": "new"})
        assert a["sessionId"] == "new"
    await ws1.close()


async def test_taps_not_queued_while_disconnected_and_state_kept(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        await cmd(ws, {"type": "activate", "sessionId": "s-1"})
    await asyncio.sleep(0.05)
    await dev.tap()  # nobody connected: counted, not sent
    async with connect(dev.uri, proxy=None) as ws:
        h = await recv(ws, "hello")
        assert h["tapSeq"] == 1 and h["sessionId"] == "s-1" and h["indicator"] is True
        await asyncio.sleep(0.15)
        with pytest.raises(asyncio.TimeoutError):
            await recv(ws, "tap", timeout=0.2)


async def test_power_cycle_reboots(dev):
    async with connect(dev.uri, proxy=None, close_timeout=0.2) as ws:
        await recv(ws, "hello")
        await cmd(ws, {"type": "activate", "sessionId": "s-1"})
        await dev.tap()
        await recv(ws, "tap")
        await dev.power_off()
        with pytest.raises(asyncio.TimeoutError):  # no status, no answers
            await recv(ws, timeout=0.3)
    with pytest.raises(OSError):
        await connect(dev.uri, proxy=None, open_timeout=0.5)
    await dev.power_on(reboot=True)
    async with connect(dev.uri, proxy=None) as ws:
        h = await recv(ws, "hello")
        assert h["sessionId"] is None and h["tapSeq"] == 0 and h["indicator"] is False


async def test_sensor_unstable_reported(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        dev.set_sensor_stable(False)
        await status_where(ws, lambda m: m["sensorStable"] is False)


async def test_withheld_acks(dev):
    async with connect(dev.uri, proxy=None) as ws:
        await recv(ws, "hello")
        dev.set_acks(False)
        await ws.send(json.dumps({"type": "activate", "sessionId": "s-1"}))
        with pytest.raises(asyncio.TimeoutError):
            await recv(ws, timeout=0.3)
        assert dev.indicator is True  # command still applied
