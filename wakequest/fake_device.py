"""Protocol-compliant fake WakeQuest device (docs/protocol.md v1).

An asyncio ``websockets`` server that behaves like the ESP32 firmware, plus
fault-injection controls for tests and for ``WAKEQUEST_DEVICE=fake``:
emit taps, drop the connection, power off/on (with reboot), withhold acks and
toggle ``sensorStable``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("wakequest.fake_device")

CURRENT = object()  # sentinel: "use the session the device currently holds"
DEACTIVATE_REASONS = {"completed", "aborted", "reset"}
SESSION_ID_MAX = 64


def valid_session_id(v: Any) -> bool:
    return isinstance(v, str) and 1 <= len(v) <= SESSION_ID_MAX


class FakeDevice:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        device_id: str = "wq-fa4e01",
        firmware: str = "0.1.0-fake",
        status_interval: float = 1.0,
        indicator_timeout_s: float = 15 * 60,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.firmware = firmware
        self.status_interval = status_interval
        self.indicator_timeout_s = indicator_timeout_s
        # device RAM
        self.session_id: str | None = None
        self.indicator = False
        self.indicator_timed_out = False
        self.tap_seq = 0
        self.sensor_stable = True
        self._boot = time.monotonic()
        self._activated_at: float | None = None
        # fault injection
        self.send_acks = True
        self.powered = True
        # observation
        self.received: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.connections = 0
        self._server: Server | None = None
        self._conn: ServerConnection | None = None
        self._status_task: asyncio.Task[None] | None = None

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        self._server = await serve(self._handler, self.host, self.port, ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]
        self.powered = True
        log.info("fake device listening on ws://%s:%d/", self.host, self.port)

    async def stop(self) -> None:
        self._stop_status()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.transport.abort()
            self._conn = None
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
            self._server = None

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}/"

    @property
    def connected(self) -> bool:
        return self._conn is not None and self.powered

    def uptime_ms(self) -> int:
        return int((time.monotonic() - self._boot) * 1000)

    # ---- fault injection ----------------------------------------------
    async def drop_connection(self) -> None:
        """Abruptly drop the TCP connection (Wi-Fi blip). Device state is kept."""
        conn = self._conn
        self._conn = None
        self._stop_status()
        if conn is not None:
            conn.transport.abort()

    async def power_off(self) -> None:
        """Simulate power loss: stop answering pings and messages, refuse new connections."""
        self.powered = False
        self._stop_status()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.transport.pause_reading()
        if self._server is not None:
            self._server.close(close_connections=False)
            self._server = None

    async def power_on(self, reboot: bool = True) -> None:
        """Restore power. A real power loss reboots the board and clears its RAM."""
        if self.powered:
            return
        old = self._conn
        self._conn = None
        if old is not None:
            with contextlib.suppress(Exception):
                old.transport.abort()
        if reboot:
            self.session_id = None
            self.indicator = False
            self.indicator_timed_out = False
            self.tap_seq = 0
            self._activated_at = None
            self._boot = time.monotonic()
        await self.start()

    def set_sensor_stable(self, stable: bool) -> None:
        self.sensor_stable = stable
        self._spawn_send(self._status_msg())

    def set_acks(self, enabled: bool) -> None:
        self.send_acks = enabled

    async def tap(self, session_id: Any = CURRENT, seq: int | None = None) -> dict[str, Any]:
        """Report a tap edge. Not queued when no client is connected (protocol)."""
        if seq is None:
            self.tap_seq += 1
            seq = self.tap_seq
        msg = {
            "type": "tap",
            "seq": seq,
            "sessionId": self.session_id if session_id is CURRENT else session_id,
            "uptimeMs": self.uptime_ms(),
        }
        if self.connected:
            await self._send(self._conn, msg)
        return msg

    async def send_raw(self, msg: dict[str, Any]) -> None:
        if self.connected:
            await self._send(self._conn, msg)

    def state(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "powered": self.powered,
            "uri": self.uri,
            "deviceId": self.device_id,
            "sessionId": self.session_id,
            "indicator": self.indicator,
            "indicatorTimedOut": self.indicator_timed_out,
            "tapSeq": self.tap_seq,
            "sensorStable": self.sensor_stable,
            "sendAcks": self.send_acks,
            "connections": self.connections,
            "received": list(self.received[-50:]),
        }

    # ---- protocol ------------------------------------------------------
    def _hello(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "protocol": 1,
            "deviceId": self.device_id,
            "firmware": self.firmware,
            "tapActiveLevel": "LOW",
            "sensorStable": self.sensor_stable,
            "indicator": self.indicator,
            "sessionId": self.session_id,
            "tapSeq": self.tap_seq,
            "uptimeMs": self.uptime_ms(),
        }

    def _status_msg(self) -> dict[str, Any]:
        self._check_safety_timeout()
        return {
            "type": "status",
            "sensorStable": self.sensor_stable,
            "indicator": self.indicator,
            "indicatorTimedOut": self.indicator_timed_out,
            "sessionId": self.session_id,
            "tapSeq": self.tap_seq,
            "uptimeMs": self.uptime_ms(),
            "rssi": -58,
        }

    def _check_safety_timeout(self) -> None:
        if (self.indicator and self._activated_at is not None
                and time.monotonic() - self._activated_at >= self.indicator_timeout_s):
            self.indicator = False
            self.indicator_timed_out = True

    async def _handler(self, conn: ServerConnection) -> None:
        if not self.powered:
            return
        old = self._conn
        if old is not None:  # protocol: a second client replaces the older one
            self._stop_status()
            with contextlib.suppress(Exception):
                await old.close(1000, "replaced by a newer client")
        self._conn = conn
        self.connections += 1
        await self._send(conn, self._hello())
        self._status_task = asyncio.create_task(self._status_loop(conn))
        try:
            async for raw in conn:
                if not self.powered or self._conn is not conn:
                    break
                await self._handle(conn, raw)
        except ConnectionClosed:
            pass
        finally:
            if self._conn is conn:
                self._conn = None
                self._stop_status()

    async def _status_loop(self, conn: ServerConnection) -> None:
        with contextlib.suppress(ConnectionClosed, asyncio.CancelledError):
            while self._conn is conn and self.powered:
                await asyncio.sleep(self.status_interval)
                if self._conn is not conn or not self.powered:
                    break
                await self._send(conn, self._status_msg())

    def _stop_status(self) -> None:
        if self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None

    def _spawn_send(self, msg: dict[str, Any]) -> None:
        if self.connected:
            asyncio.get_running_loop().create_task(self._send(self._conn, msg))

    async def _send(self, conn: ServerConnection | None, msg: dict[str, Any]) -> None:
        if conn is None:
            return
        try:
            await conn.send(json.dumps(msg))
            self.sent.append(msg)
        except ConnectionClosed:
            pass

    async def _error(self, conn: ServerConnection, code: str, detail: str) -> None:
        await self._send(conn, {"type": "error", "code": code, "detail": detail})

    async def _handle(self, conn: ServerConnection, raw: str | bytes) -> None:
        """Validation and errors per docs/protocol.md; an error is never followed by an ack."""
        if isinstance(raw, bytes):
            await self._error(conn, "bad_json", "binary frames are not accepted")
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            await self._error(conn, "bad_json", "could not parse JSON")
            return
        if not isinstance(msg, dict):
            await self._error(conn, "bad_json", "expected a JSON object")
            return
        self.received.append(msg)
        t = msg.get("type")
        if not isinstance(t, str):
            await self._error(conn, "bad_field", "type must be a string")
            return
        if t not in ("activate", "deactivate", "sync"):
            await self._error(conn, "unknown_type", f"unknown type {t!r}")
            return
        if t == "sync" and "sessionId" not in msg:
            await self._error(conn, "bad_field", "sync needs a sessionId key (string or null)")
            return
        sid = msg.get("sessionId")
        if not (t == "sync" and sid is None) and not valid_session_id(sid):
            await self._error(conn, "bad_field", "sessionId must be a string of 1-64 characters")
            return
        if t == "deactivate" and msg.get("reason") not in DEACTIVATE_REASONS:
            await self._error(conn, "bad_field", "reason must be completed, aborted or reset")
            return
        ignored = False
        if t == "activate":
            self._activate(sid)
        elif t == "deactivate":
            if self.session_id is None or self.session_id == sid:
                self._deactivate()
            else:
                ignored = True
        elif sid is None:  # sync null: clear regardless of the held id
            self._deactivate()
        else:
            self._activate(sid)
        if self.send_acks:
            await self._send(conn, {"type": "ack", "of": t, "sessionId": self.session_id,
                                    "indicator": self.indicator, "ignored": ignored})

    def _activate(self, sid: str) -> None:
        if self.session_id == sid:
            # Same session: change nothing (no cycle/timer restart and no
            # re-light after a safety timeout). The ack reports the current indicator.
            return
        self.session_id = sid
        self.indicator = True
        self.indicator_timed_out = False
        self._activated_at = time.monotonic()

    def _deactivate(self) -> None:
        if self.session_id is None and not self.indicator:
            return  # nothing changes, so indicatorTimedOut is kept
        self.session_id = None
        self.indicator = False
        self.indicator_timed_out = False
        self._activated_at = None
