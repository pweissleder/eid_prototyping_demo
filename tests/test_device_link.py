"""Integration: Service + DeviceLink against FakeDevice over real sockets."""

from __future__ import annotations

import asyncio
import json
import socket
import time

import pytest
from websockets.asyncio.server import serve

from wakequest import device_link
from wakequest.device_link import DeviceLink, LinkError, validate_hello
from wakequest.service import Service

from .helpers import fast_settings, names, wait_until


@pytest.fixture
async def svc(tmp_path):
    s = Service(fast_settings(tmp_path))
    await s.start()
    yield s
    await s.stop()


def dev_status(s: Service) -> str:
    return s.machine.device.status


async def connect(s: Service) -> None:
    await s.connect_device()
    await wait_until(lambda: dev_status(s) == "connected")


async def make_ready(s: Service) -> str:
    await connect(s)
    bid, _ = await s.add_browser()
    await s.browser_message(bid, {"type": "audio_test_ok"})
    return bid


async def ring_test_alarm(s: Service, bid: str) -> str:
    await s.run(s.machine.schedule_test_alarm)
    await wait_until(lambda: s.machine.ringing is not None)
    sid = s.machine.ringing.sessionId
    await s.browser_message(bid, {"type": "sound_started", "sessionId": sid})
    await wait_until(lambda: s.machine.ringing.activationStatus == "acked")
    return sid


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_connect_reaches_connected_after_hello_and_syncs(svc):
    await svc.connect_device()
    assert dev_status(svc) in ("connecting", "connected")
    await wait_until(lambda: dev_status(svc) == "connected")
    assert svc.machine.device.deviceId == svc.fake.device_id
    await wait_until(lambda: svc.fake.received[:1] == [{"type": "sync", "sessionId": None}])
    evs = names(svc.machine.events)
    assert evs.index("device_connect_requested") < evs.index("device_connected") < evs.index("sync_sent")
    # status heartbeats keep it connected well past the watchdog
    await asyncio.sleep(1.0)
    assert dev_status(svc) == "connected"


async def test_connect_failure_is_understandable_and_retryable(tmp_path):
    port = free_port()
    s = Service(fast_settings(tmp_path, device_mode="real", device_host="127.0.0.1", device_port=port))
    await s.start()
    try:
        await s.connect_device()
        await wait_until(lambda: s.machine.device.status == "disconnected")
        err = s.machine.device.lastError
        assert "refused" in err and "127.0.0.1" in err
        assert "device_connect_failed" in names(s.machine.events)
        await asyncio.sleep(0.3)
        assert not s.link.running  # does not keep hammering until the user retries
        # bring a device up on that port, retry succeeds
        from wakequest.fake_device import FakeDevice
        dev = FakeDevice(port=port, status_interval=0.2)
        await dev.start()
        try:
            await s.connect_device()
            await wait_until(lambda: s.machine.device.status == "connected")
            assert s.machine.device.lastError is None
        finally:
            await s.link.stop()
            await dev.stop()
    finally:
        await s.stop()


async def test_unresolvable_host_message(tmp_path):
    s = Service(fast_settings(tmp_path, device_mode="real", device_host="no-such-host.invalid"))
    await s.start()
    try:
        await s.connect_device()
        await wait_until(lambda: s.machine.device.status == "disconnected", timeout=5)
        assert "Could not find the device at no-such-host.invalid" in s.machine.device.lastError
    finally:
        await s.stop()


async def test_wrong_protocol_hello_rejected(tmp_path):
    async def handler(ws):
        await ws.send(json.dumps({"type": "hello", "protocol": 2, "deviceId": "wq-x"}))
        await asyncio.sleep(2)

    server = await serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    s = Service(fast_settings(tmp_path, device_mode="real", device_host="127.0.0.1", device_port=port))
    await s.start()
    try:
        await s.connect_device()
        await wait_until(lambda: s.machine.device.status == "disconnected")
        assert "protocol 2" in s.machine.device.lastError
        assert s.machine.device.deviceId is None
    finally:
        await s.stop()
        server.close()


async def test_silent_server_hello_timeout(tmp_path):
    async def handler(ws):
        await asyncio.sleep(3)

    server = await serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    s = Service(fast_settings(tmp_path, device_mode="real", device_host="127.0.0.1", device_port=port))
    await s.start()
    try:
        await s.connect_device()
        await wait_until(lambda: s.machine.device.status == "disconnected")
        assert "no hello" in s.machine.device.lastError
    finally:
        await s.stop()
        server.close()


def test_validate_hello():
    assert validate_hello({"type": "hello", "protocol": 1, "deviceId": "wq-1"})
    for bad in ({"type": "status"}, {"type": "hello", "protocol": 1}, "x"):
        with pytest.raises(LinkError):
            validate_hello(bad)


async def test_resolves_ipv4(tmp_path):
    link = DeviceLink(None, fast_settings(tmp_path))  # type: ignore[arg-type]
    link.set_target("localhost", 81)
    assert await link.resolve_ipv4() == "127.0.0.1"
    link.set_target("10.1.2.3", 81)
    assert await link.resolve_ipv4() == "10.1.2.3"


async def test_local_name_falls_back_to_system_resolver(tmp_path, monkeypatch):
    calls = []

    async def fake_system_resolve(host, timeout_s):
        calls.append(host)
        return "172.20.10.2"

    monkeypatch.setattr(device_link, "system_resolve_local", fake_system_resolve)
    link = DeviceLink(None, fast_settings(tmp_path))  # type: ignore[arg-type]
    link.set_target("wakequest-test-unresolvable.local", 81)
    assert await link.resolve_ipv4() == "172.20.10.2"
    assert calls == ["wakequest-test-unresolvable.local"]


async def test_failed_fallback_keeps_understandable_error(tmp_path, monkeypatch):
    async def no_answer(host, timeout_s):
        return None

    monkeypatch.setattr(device_link, "system_resolve_local", no_answer)
    link = DeviceLink(None, fast_settings(tmp_path))  # type: ignore[arg-type]
    link.set_target("wakequest-test-unresolvable.local", 81)
    with pytest.raises(LinkError, match="WAKEQUEST_DEVICE_HOST"):
        await link.resolve_ipv4()


async def test_system_resolver_ignores_non_local_names():
    assert await device_link.system_resolve_local("example.invalid", 1.0) is None


async def test_drop_detected_and_auto_reconnect_with_sync(svc):
    await connect(svc)
    n = svc.fake.connections
    await svc.fake.drop_connection()
    await wait_until(lambda: dev_status(svc) == "reconnecting", timeout=2)
    assert "connection_lost" in names(svc.machine.events)
    await wait_until(lambda: dev_status(svc) == "connected", timeout=3)
    assert svc.fake.connections == n + 1
    assert "connection_restored" in names(svc.machine.events)
    assert [m for m in svc.fake.received if m["type"] == "sync"] == [{"type": "sync", "sessionId": None}] * 2


async def test_full_flow_tap_completes_once(svc):
    bid = await make_ready(svc)
    sid = await ring_test_alarm(svc, bid)
    assert svc.fake.session_id == sid and svc.fake.indicator is True
    await svc.fake.tap()
    await wait_until(lambda: svc.machine.run_state() == "completed")
    await wait_until(lambda: svc.fake.indicator is False)
    assert {"type": "deactivate", "sessionId": sid, "reason": "completed"} in svc.fake.received
    await svc.fake.tap(session_id=sid)  # bounce/second tap after completion
    await svc.fake.tap(session_id=sid, seq=svc.fake.tap_seq)  # replayed seq
    await wait_until(lambda: names(svc.machine.events).count("tap_received") == 3)
    taps = [e for e in svc.machine.events if e["event"] == "tap_received"]
    assert [t["reason"] for t in taps] == [None, "duplicate", "duplicate"]
    assert names(svc.machine.events).count("completed") == 1
    # events persisted to disk as they happen
    lines = svc.storage.events_path.read_text().splitlines()
    assert sum(1 for line in lines if '"event":"completed"' in line) == 1


async def test_missing_ack_flagged(svc):
    bid = await make_ready(svc)
    svc.fake.set_acks(False)
    await svc.run(svc.machine.schedule_test_alarm)
    await wait_until(lambda: svc.machine.ringing is not None)
    await wait_until(lambda: svc.machine.ringing.activationStatus == "missing", timeout=2)
    assert "activation_missing" in names(svc.machine.events)
    # tap cannot complete an unacknowledged activation
    await svc.fake.tap()
    await wait_until(lambda: "tap_received" in names(svc.machine.events))
    tapped = [e for e in svc.machine.events if e["event"] == "tap_received"]
    assert [(t["accepted"], t["reason"]) for t in tapped] == [(False, "not_activated")]
    assert svc.machine.run_state() == "ringing"


async def test_power_loss_during_ring_detected_reconnect_resyncs_no_double_reward(svc):
    bid = await make_ready(svc)
    sid = await ring_test_alarm(svc, bid)
    t0 = time.monotonic()
    await svc.fake.power_off()
    await wait_until(lambda: dev_status(svc) == "reconnecting", timeout=5)
    assert time.monotonic() - t0 < 5
    s = svc.machine.ringing
    assert s is not None and s.connectionLostDuringRun  # still ringing, never "completed"
    await asyncio.sleep(0.3)
    await svc.fake.power_on(reboot=True)  # RAM cleared: sessionId null, tapSeq 0
    await wait_until(lambda: dev_status(svc) == "connected", timeout=3)
    await wait_until(lambda: svc.fake.session_id == sid and svc.fake.indicator)
    assert {"type": "sync", "sessionId": sid} in svc.fake.received
    await svc.fake.tap()  # seq 1 again after reboot
    await wait_until(lambda: svc.machine.run_state() == "completed")
    # another drop after completion: sync(null), no second reward
    await svc.fake.drop_connection()
    await wait_until(lambda: dev_status(svc) == "connected" and
                     names(svc.machine.events).count("connection_restored") == 2, timeout=3)
    await wait_until(lambda: svc.fake.received[-1] == {"type": "sync", "sessionId": None})
    assert names(svc.machine.events).count("completed") == 1


async def test_loss_detected_within_5s_with_protocol_timings(tmp_path):
    """Real protocol timings (ping 1 s / timeout 2 s, watchdog 3 s): QR-04 ≤ 5 s."""
    s = Service(fast_settings(tmp_path, ping_interval_s=1.0, ping_timeout_s=2.0, watchdog_s=3.0,
                              fake_status_interval_s=1.0))
    await s.start()
    try:
        await connect(s)
        await asyncio.sleep(1.2)
        t0 = time.monotonic()
        await s.fake.power_off()
        await wait_until(lambda: dev_status(s) == "reconnecting", timeout=6, interval=0.02)
        took = time.monotonic() - t0
        assert took <= 5.0, took
        lost = [e for e in s.machine.events if e["event"] == "connection_lost"][0]
        assert lost["reason"]
    finally:
        await s.stop()


async def test_browser_snapshots_pushed(svc):
    bid, q = await svc.add_browser()
    first = q.get_nowait()
    assert first["type"] == "snapshot" and first["audio"]["browsersConnected"] == 1
    await connect(svc)
    snaps = []
    while not q.empty():
        snaps.append(q.get_nowait())
    assert snaps[-1]["device"]["status"] == "connected"
    assert [x["rev"] for x in snaps] == sorted(x["rev"] for x in snaps)
    await svc.remove_browser(bid)


def test_backoff_delays_match_protocol(tmp_path):
    from wakequest.settings import Settings

    link = DeviceLink(None, Settings(data_dir=str(tmp_path)))  # type: ignore[arg-type]
    assert [link.backoff_delay(a, False) for a in range(6)] == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]
    assert [link.backoff_delay(a, True) for a in range(5)] == [0.5, 1.0, 2.0, 2.0, 2.0]


async def test_keeps_retrying_while_device_off(svc):
    await connect(svc)
    await svc.fake.power_off()
    await wait_until(lambda: "attempt" in (svc.machine.device.lastError or ""), timeout=4)
    assert dev_status(svc) == "reconnecting"
    await svc.fake.power_on(reboot=False)
    await wait_until(lambda: dev_status(svc) == "connected", timeout=3)


async def test_shutdown_while_ringing_aborts_and_turns_light_off(tmp_path):
    s = Service(fast_settings(tmp_path))
    await s.start()
    bid = await make_ready(s)
    sid = await ring_test_alarm(s, bid)
    fake = s.fake
    s.fake = None  # keep the fake device running so we can inspect what it received
    await s.stop()
    try:
        await wait_until(lambda: {"type": "deactivate", "sessionId": sid, "reason": "aborted"} in fake.received)
        aborted = [e for e in s.machine.events if e["event"] == "aborted"][0]
        assert aborted["reason"] == "service_shutdown"
    finally:
        await fake.stop()
