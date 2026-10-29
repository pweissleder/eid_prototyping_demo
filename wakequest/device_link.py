"""WebSocket client link to the WakeQuest device (docs/protocol.md v1).

- Connects only when the user asks (``request_connect``). The first attempt
  either reaches a valid ``hello`` (→ connected) or fails with an
  understandable error (→ disconnected; the user can retry).
- Once connected, any loss is reported immediately and the link reconnects
  automatically with backoff 0.5 s, 1 s, 2 s … capped at 2 s while a run is
  active and 5 s otherwise.
- Liveness: WebSocket ping every 1 s with a 2 s pong timeout (websockets'
  keepalive) plus a 3 s "no device message" watchdog, so loss is detected in
  ≤ 3 s (QR-04 budget: 5 s).
- The host is resolved to IPv4 explicitly (getaddrinfo AF_INET in the loop's
  executor): macOS ``.local`` lookups can stall on IPv6. If getaddrinfo fails
  for a ``.local`` name on macOS, the system resolver is asked via
  ``dns-sd``.
- Every message after ``hello`` is handed to the handler; the handler (the
  service) answers ``hello`` with ``sync``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
import sys
from typing import Any, Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .clock import Clock, SystemClock
from .settings import Settings

log = logging.getLogger("wakequest.device_link")


class LinkHandler(Protocol):
    async def on_link_attempt(self, reconnecting: bool) -> None: ...
    async def on_link_hello(self, hello: dict[str, Any]) -> None: ...
    async def on_link_failed(self, error: str) -> None: ...
    async def on_link_retry_failed(self, error: str, attempt: int) -> None: ...
    async def on_link_lost(self, reason: str) -> None: ...
    async def on_device_message(self, msg: dict[str, Any]) -> None: ...
    def run_active(self) -> bool: ...


class LinkError(Exception):
    """A failure with a user-facing explanation."""


def validate_hello(msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        raise LinkError("The device answered, but its first message was not a WakeQuest hello. "
                        "Is the WakeQuest firmware running on it?")
    if msg.get("protocol") != 1:
        raise LinkError(f"The device speaks protocol {msg.get('protocol')!r}, but this service needs "
                        "protocol 1. Update the firmware or the service.")
    if not isinstance(msg.get("deviceId"), str) or not msg["deviceId"]:
        raise LinkError("The device's hello has no device identifier.")
    return msg


async def system_resolve_local(host: str, timeout_s: float) -> str | None:
    """Ask macOS's mDNS responder for a ``.local`` name via ``dns-sd -G v4``.

    On the bench Mac (2026-10-29) Python's getaddrinfo failed for mDNS names
    that ``dns-sd`` answered in milliseconds (``dscacheutil`` took 5 s).
    ``dns-sd`` keeps running, so the first IPv4 answer is read and the process
    stopped. Returns None when unavailable.
    """
    if sys.platform != "darwin" or not host.endswith(".local"):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "dns-sd", "-G", "v4", host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        return await asyncio.wait_for(_first_dns_sd_ipv4(proc.stdout, host), timeout_s)
    except TimeoutError:
        return None
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


async def _first_dns_sd_ipv4(stream: asyncio.StreamReader | None, host: str) -> str | None:
    # Answer lines look like: "22:16:27.967  Add  40000002  14  wakequest.local.  172.20.10.2  120"
    if stream is None:
        return None
    while line := await stream.readline():
        fields = line.decode(errors="replace").split()
        if len(fields) >= 7 and fields[1] == "Add" and fields[4].rstrip(".") == host:
            with contextlib.suppress(ValueError):
                return str(ipaddress.IPv4Address(fields[5]))
    return None


class DeviceLink:
    def __init__(self, handler: LinkHandler, settings: Settings, clock: Clock | None = None) -> None:
        self.handler = handler
        self.settings = settings
        self.clock = clock or SystemClock()
        self.host = settings.device_host
        self.port = settings.device_port
        self._task: asyncio.Task[None] | None = None
        self._ws: ClientConnection | None = None
        self.resolved_ip: str | None = None

    def set_target(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}/"

    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def request_connect(self) -> None:
        """User-triggered connect / retry. No-op while already connected."""
        if self.running and self._ws is not None:
            return
        await self._cancel_task()
        self._task = asyncio.create_task(self._run(), name="wakequest-device-link")

    async def stop(self) -> None:
        await self._cancel_task()

    async def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        ws, self._ws = self._ws, None
        if ws is not None:
            self._abort(ws)

    async def send(self, msg: dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(msg))
            return True
        except ConnectionClosed:
            return False

    # ------------------------------------------------------------------
    async def resolve_ipv4(self) -> str:
        try:
            ipaddress.IPv4Address(self.host)
            return self.host
        except ValueError:
            pass
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(self.host, self.port, family=socket.AF_INET, type=socket.SOCK_STREAM),
                self.settings.resolve_timeout_s,
            )
        except (TimeoutError, socket.gaierror, OSError) as exc:
            ip = await system_resolve_local(self.host, self.settings.resolve_timeout_s)
            if ip is not None:
                return ip
            raise LinkError(
                f"Could not find the device at {self.host} ({type(exc).__name__}). Check that it is "
                "powered and on the same Wi-Fi; if the name does not resolve, set "
                "WAKEQUEST_DEVICE_HOST to the IP address printed on its serial monitor."
            ) from None
        if not infos:
            raise LinkError(f"Could not find an IPv4 address for {self.host}.")
        return infos[0][4][0]

    async def _open(self) -> tuple[ClientConnection, dict[str, Any]]:
        ip = await self.resolve_ipv4()
        self.resolved_ip = ip
        s = self.settings
        try:
            ws = await connect(
                self.uri,
                host=ip,
                port=self.port,
                proxy=None,
                open_timeout=s.open_timeout_s,
                ping_interval=s.ping_interval_s,
                ping_timeout=s.ping_timeout_s,
                close_timeout=1,
                max_size=64 * 1024,
            )
        except ConnectionRefusedError:
            raise LinkError(f"The device at {self.host} ({ip}:{self.port}) refused the connection. "
                            "Is the WakeQuest firmware running?") from None
        except TimeoutError:
            raise LinkError(f"No answer from the device at {self.host} ({ip}:{self.port}) within "
                            f"{s.open_timeout_s:g} s. Is it powered and on the same Wi-Fi?") from None
        except (OSError, InvalidHandshake, InvalidURI) as exc:
            raise LinkError(f"Could not connect to the device at {self.host} ({ip}:{self.port}): "
                            f"{exc}") from None
        try:
            raw = await asyncio.wait_for(ws.recv(), s.hello_timeout_s)
        except TimeoutError:
            self._abort(ws)
            raise LinkError(f"The device at {self.host} accepted the connection but sent no hello within "
                            f"{s.hello_timeout_s:g} s.") from None
        except ConnectionClosed:
            raise LinkError(f"The device at {self.host} closed the connection before saying hello.") from None
        try:
            hello = validate_hello(json.loads(raw))
        except ValueError:
            self._abort(ws)
            raise LinkError("The device sent something that is not JSON. Is the WakeQuest firmware "
                            "running on it?") from None
        except LinkError:
            self._abort(ws)
            raise
        return ws, hello

    @staticmethod
    def _abort(ws: ClientConnection) -> None:
        with contextlib.suppress(Exception):
            ws.transport.abort()

    async def _run(self) -> None:
        ever_connected = False
        attempt = 0
        while True:
            await self.handler.on_link_attempt(reconnecting=ever_connected)
            connected_now = False
            reason = "unknown error"
            try:
                ws, hello = await self._open()
                self._ws = ws
                connected_now = True
                ever_connected = True
                attempt = 0
                await self.handler.on_link_hello(hello)
                reason = await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except LinkError as exc:
                reason = str(exc)
            except Exception as exc:  # defensive: keep the link alive
                log.exception("device link error")
                reason = f"unexpected error: {exc}"
            finally:
                if connected_now:
                    ws_done, self._ws = self._ws, None
                    if ws_done is not None:
                        self._abort(ws_done)
            if connected_now:
                log.warning("device link lost: %s", reason)
                await self.handler.on_link_lost(reason)
            elif not ever_connected:
                log.warning("device connect failed: %s", reason)
                await self.handler.on_link_failed(reason)
                return
            else:
                await self.handler.on_link_retry_failed(reason, attempt + 1)
            delay = self.backoff_delay(attempt, self.handler.run_active())
            attempt += 1
            await self.clock.sleep(delay)

    def backoff_delay(self, attempt: int, run_active: bool) -> float:
        """0.5 s, 1 s, 2 s, ... capped at 2 s during a run and 5 s otherwise."""
        cap = self.settings.backoff_cap_active_s if run_active else self.settings.backoff_cap_idle_s
        return min(self.settings.backoff_initial_s * (2 ** attempt), cap)

    async def _receive_loop(self, ws: ClientConnection) -> str:
        """Returns a human-readable reason when the link dies."""
        watchdog = self.settings.watchdog_s
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), watchdog)
            except TimeoutError:
                return f"no message from the device for {watchdog:g} s"
            except ConnectionClosed as exc:
                rcvd = exc.rcvd
                sent = exc.sent
                if sent is not None and sent.code == 1011 and "keepalive" in (sent.reason or ""):
                    return f"no reply to ping within {self.settings.ping_timeout_s:g} s"
                if rcvd is not None:
                    return f"the device closed the connection (code {rcvd.code})"
                return "the connection dropped"
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning("ignoring non-JSON device message: %r", raw[:200])
                continue
            if isinstance(msg, dict):
                await self.handler.on_device_message(msg)
