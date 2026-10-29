# WakeQuest device protocol (v1)

Contract between the local service (`wakequest/device_link.py`) and the ESP32 firmware (`firmware/wakequest/`). Both sides, and the fake device used in tests (`wakequest/fake_device.py`), implement exactly this document. Change it here first, then in all three.

## Roles and transport

- The **device** is a WebSocket **server** at `ws://wakequest.local:81/`. The fallback is `ws://<IP printed on serial>:81/`.
- The **service** is the only **client**. If a second client connects, the device closes the older connection.
- Messages are UTF-8 JSON objects sent as text frames, one per frame. Every message has a `"type"` string. Unknown fields must be ignored, so either side can add optional fields.
- Times are integers in milliseconds. `uptimeMs` is the device clock. The service never compares device time with its own clock. It stamps every event with its own wall clock on receipt.

## Liveness

| Side | Behaviour |
|---|---|
| Service | Sends a WebSocket ping every 1 s, and treats a missing pong after 2 s as a dead link. It also treats the link as dead if no device message arrives for 3 s. Both rules keep loss detection ≤ 5 s (QR-04). After a dead link it closes the socket and reconnects with backoff (0.5 s, 1 s, 2 s, max 2 s while a run is active and max 5 s otherwise). |
| Device | Answers pings (the library does this). Sends `status` every 1000 ms while a client is connected. |

## Session rules the device follows

- The device holds at most one `sessionId` (a string chosen by the service) and one `indicator` flag.
- While `indicator` is true, it runs the 7-colour cycle (red, green, blue, yellow, cyan, magenta, white) at 400 ms per colour. While it is false, all LED channels are off. Colours carry no meaning.
- **Safety timeout:** 15 min after `activate`, the device switches the indicator off by itself, but it keeps the `sessionId`. Its next `status` then reports `"indicator": false, "indicatorTimedOut": true`.
- **Losing the client does not change the session or the indicator.** The light keeps cycling, so the alarm stays visible while the service reconnects.
- The device never decides whether a tap completes anything. It reports every accepted tap edge with the `sessionId` it holds at that moment. The service decides.
- Taps that happen while no client is connected are **not queued**. They increase `tapSeq` but are not sent, so the user simply taps again after reconnection.

## Tap detection (device side)

- Tap input: GPIO27. Idle level HIGH, tap level LOW. This was confirmed on the bench (docs/test-log.md B2/B3).
- A tap is the first idle→active edge. Further edges are ignored for `lockoutMs` = 250 ms, because the longest burst measured on the bench was 21 ms.
- `tapSeq` starts at 0 at boot and increases by 1 for each accepted tap.
- `sensorStable` is true when the input has been at the idle level, with no edges, for at least 2000 ms.

## Device → service

| type | Fields | When |
|---|---|---|
| `hello` | `protocol` (1), `deviceId` (`"wq-"` + last 3 MAC bytes in hex, e.g. `"wq-a1b2c3"`), `firmware` (semver string), `tapActiveLevel` (`"LOW"`), `sensorStable`, `indicator`, `sessionId` (string or null), `tapSeq`, `uptimeMs` | Immediately after a client connects. This is the device's first message. |
| `status` | `sensorStable`, `indicator`, `indicatorTimedOut` (bool), `sessionId`, `tapSeq`, `uptimeMs`, `rssi` (int dBm) | Every 1000 ms. |
| `ack` | `of` (`"activate"`, `"deactivate"` or `"sync"`), `sessionId` (the device's session *after* handling the command), `indicator`, `ignored` (bool) | After every command. |
| `tap` | `seq` (= new `tapSeq`), `sessionId` (string or null), `uptimeMs` | Each accepted tap, sent immediately. |
| `error` | `code` (`"bad_json"`, `"unknown_type"` or `"bad_field"`), `detail` | Malformed command. The connection stays open. |

## Service → device

| type | Fields | Device behaviour |
|---|---|---|
| `activate` | `sessionId` | Sets `sessionId`, turns the indicator on (restarting the cycle at red), restarts the 15-min timer, and acks with `ignored: false`. If the device already holds this same `sessionId`, it changes nothing (no cycle or timer restart, and **no re-light after a safety timeout**) and acks with its current `indicator`. |
| `deactivate` | `sessionId`, `reason` (`"completed"`, `"aborted"` or `"reset"`) | If `sessionId` equals the held one, or the device holds none, it turns the indicator off, clears `sessionId`, and acks with `ignored: false`. If it holds a *different* session, it changes nothing and acks with `ignored: true`. |
| `sync` | `sessionId` (string or null) | Sent by the service right after every `hello`. With a string, it behaves like `activate` for that id; with null, it behaves like `deactivate` regardless of the held id. The ack carries `of: "sync"`. |

## Validation and errors

- **`sessionId`:** must be a string of 1–64 characters. Anything else gives `error` with `bad_field`.
- **`deactivate`:** needs a string `sessionId`. Only `sync` accepts null, meaning "clear regardless of the held id".
- **`sync`:** must contain the `sessionId` key; the value may be null.
- **`bad_json`:** a frame that isn't a JSON object, or a binary frame.
- **`bad_field`:** a missing or non-string `type`, or a missing or invalid field.
- **`unknown_type`:** any other `type`.
- An `error` is never followed by an `ack`.
- `indicatorTimedOut` is cleared by any command that changes the session or indicator.
- The device only acts on commands from its newest client.
- The client must not request a WebSocket subprotocol.

## Example exchange

```
device  → {"type":"hello","protocol":1,"deviceId":"wq-a1b2c3","firmware":"0.1.0","tapActiveLevel":"LOW","sensorStable":true,"indicator":false,"sessionId":null,"tapSeq":0,"uptimeMs":5123}
service → {"type":"sync","sessionId":null}
device  → {"type":"ack","of":"sync","sessionId":null,"indicator":false,"ignored":false}
device  → {"type":"status","sensorStable":true,"indicator":false,"indicatorTimedOut":false,"sessionId":null,"tapSeq":0,"uptimeMs":6123,"rssi":-58}
service → {"type":"activate","sessionId":"s-20261030-070000-3f9c"}
device  → {"type":"ack","of":"activate","sessionId":"s-20261030-070000-3f9c","indicator":true,"ignored":false}
device  → {"type":"tap","seq":1,"sessionId":"s-20261030-070000-3f9c","uptimeMs":91234}
service → {"type":"deactivate","sessionId":"s-20261030-070000-3f9c","reason":"completed"}
device  → {"type":"ack","of":"deactivate","sessionId":null,"indicator":false,"ignored":false}
```

## What the service does with device messages (summary; detail lives in `wakequest/state.py`)

- **Connected:** shown as "Connected" only after a valid `hello` with `protocol == 1`.
- **Activation:** only an `ack` for `activate`/`sync` with a matching `sessionId` and `indicator: true` counts as acknowledged. If none arrives within 2 s of `activate`, `activation_missing` is logged and shown.
- **Accepting a tap:** a `tap` completes a session only if every one of these holds:
  - the service session is `ringing`;
  - `tap.sessionId` equals that session's id;
  - activation was acknowledged;
  - the session is not already completed or aborted.

  Every other tap is logged with the reason it was ignored: `idle`, `armed`, `stale_session`, `not_activated` or `duplicate`.
- **Duplicates:** a repeated `seq` from the same `deviceId` boot is ignored. A reset is detected when `uptimeMs` decreases.
