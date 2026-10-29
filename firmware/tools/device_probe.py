"""Talk to the WakeQuest controller directly, without the service (hardware check).

Usage:
    uv run --no-project --with websockets python firmware/tools/device_probe.py \
        ws://wakequest.local:81/ [seconds]

Connects, prints hello, sends sync null, sends activate with a test sessionId,
prints every ack/status/tap/error for `seconds` (default 20), then sends
deactivate and prints its ack. Tap the module while it runs. Stop the service
first: the device keeps only one client and closes the older one.
Needs Python >= 3.11 (asyncio.timeout) and websockets >= 13 (asyncio client).
"""

import argparse
import asyncio
import json
import socket
import sys
import time
from urllib.parse import urlsplit

from websockets.asyncio.client import connect


def resolve_ipv4(host: str, port: int) -> str:
    # macOS .local lookups can stall on IPv6; ask for IPv4 only.
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return infos[0][4][0]


def show(direction: str, message: dict) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} {direction} {json.dumps(message, separators=(',', ':'))}", flush=True)


async def send(ws, message: dict) -> None:
    show("->", message)
    await ws.send(json.dumps(message))


async def recv(ws, timeout: float) -> dict | None:
    try:
        async with asyncio.timeout(timeout):
            raw = await ws.recv()
    except TimeoutError:
        return None
    message = json.loads(raw)
    show("<-", message)
    return message


async def wait_for_ack(ws, of: str, timeout: float = 3.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        message = await recv(ws, left)
        if message is None:
            break
        if message.get("type") == "ack" and message.get("of") == of:
            return message
    print(f"!! no ack for {of} within {timeout:.0f} s", flush=True)
    return None


async def run(url: str, seconds: float) -> int:
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 81
    ip = resolve_ipv4(host, port)
    print(f"connecting to {url} ({ip}:{port})", flush=True)

    async with connect(url, host=ip, port=port, open_timeout=10, ping_interval=1, ping_timeout=2) as ws:
        hello = await recv(ws, 5.0)
        if not hello or hello.get("type") != "hello":
            print("!! first message was not hello", flush=True)
            return 1
        if hello.get("protocol") != 1:
            print(f"!! unexpected protocol {hello.get('protocol')}", flush=True)

        await send(ws, {"type": "sync", "sessionId": None})
        await wait_for_ack(ws, "sync")

        session_id = "probe-" + time.strftime("%Y%m%d-%H%M%S")
        await send(ws, {"type": "activate", "sessionId": session_id})
        await wait_for_ack(ws, "activate")

        print(f"listening for {seconds:.0f} s: tap the module now (the LED should cycle)", flush=True)
        taps = 0
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            message = await recv(ws, left)
            if message and message.get("type") == "tap":
                taps += 1

        await send(ws, {"type": "deactivate", "sessionId": session_id, "reason": "reset"})
        await wait_for_ack(ws, "deactivate")
        print(f"done: {taps} tap message(s) received", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", default="ws://wakequest.local:81/")
    parser.add_argument("seconds", nargs="?", type=float, default=20.0)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.url, args.seconds))
    except (OSError, TimeoutError) as exc:
        print(f"!! connection failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
