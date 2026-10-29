# WakeQuest service API (v1)

This is the contract between the local service (`wakequest/`) and the website (`web/`, Inc 4). The device side is specified separately in [protocol.md](protocol.md).

- **Base URL:** `http://127.0.0.1:8000`. The service binds to localhost only.
- **Start:** `uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000`
- **Static site:** if a `web/` folder exists at the repo root, it is served at `/` (`index.html` for `/`). API routes always take precedence.
- **The service is the single source of truth.** The website renders the latest **snapshot** and holds no business logic. Every state change pushes a full snapshot over `/ws`, so a reconnecting browser simply re-renders.
- **Times:** every timestamp is the service's local wall-clock time in ISO 8601 with milliseconds and UTC offset, e.g. `"2026-10-30T07:00:00.120+01:00"`. Alarm input uses local wall time without an offset, `YYYY-MM-DDTHH:MM`, interpreted in the host's timezone.
- **JSON everywhere** (except the CSV exports). Request bodies are JSON objects; `Content-Type: application/json`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `WAKEQUEST_DATA_DIR` | `./data` | Holds `alarms.json` (atomic writes) and `events.jsonl` (append-only). |
| `WAKEQUEST_DEVICE` | real | `fake` starts the in-process fake device on `127.0.0.1` and points the link at it. |
| `WAKEQUEST_DEVICE_HOST` | `wakequest.local` | Device host name or IPv4 address (the mDNS fallback is the IP printed on serial). |
| `WAKEQUEST_DEVICE_PORT` | `81` | Device WebSocket port. |
| `WAKEQUEST_TEST_MODE` | off | `1` enables the `/api/_test/*` hooks and sets `testMode: true` in the snapshot. |
| `WAKEQUEST_TEST_ALARM_DELAY_S` | `10` | Delay of "Test alarm in 10 seconds". |
| `WAKEQUEST_FAKE_DEVICE_PORT` | `0` (any free port) | Port of the fake device. |

## Errors

Every refused request returns a JSON body of this shape:

```json
{ "error": "not_ready", "message": "Cannot arm the alarm yet. Enable and test sound in the browser first.", "missing": [ { "code": "audio_not_tested", "message": "Enable and test sound in the browser first." } ] }
```

- `error` is a stable machine code. `message` is a complete sentence the website can show as-is.
- Some errors add fields: `field` (`"name"` or `"localTime"`) for alarm validation, and `missing` (the same items as `readiness.missing`) for `not_ready`.

| HTTP | `error` | When |
|---|---|---|
| 422 | `invalid_alarm` | Alarm name or time missing, invalid, or in the past (message says which). |
| 422 | `invalid_request` | The body is missing or not JSON. |
| 422 | `invalid_mode` | `?mode=` is not `study` or `demo`. |
| 422 | `invalid_delay` | Test hook: bad `seconds`. |
| 404 | `alarm_not_found` | Unknown alarm id. |
| 409 | `not_ready` | Arming or test alarm while the device is not connected, the sensor is not stable, or audio is not tested. |
| 409 | `alarm_in_past` / `alarm_used` | Arming an alarm whose time has passed, or one that already rang or was missed. |
| 409 | `run_active` | Arming or a test alarm while a run is ringing; a second test alarm while one is pending or ringing. |
| 409 | `nothing_to_stop` | Abort when nothing is ringing or pending. |
| 409 | `study_run_active` | Demo reset while a *study* alarm is ringing (use abort). |
| 404 | `no_fake_device` | Fake-device test hook without `WAKEQUEST_DEVICE=fake`. |

## REST endpoints

### State

| Method & path | Response |
|---|---|
| `GET /api/state` | `200` the full [snapshot](#snapshot). Same object as pushed over `/ws`. |

### Alarms (FR-01, FR-03, US-01)

| Method & path | Request | Response |
|---|---|---|
| `GET /api/alarms` | — | `200 {"alarms": [Alarm…], "armedAlarmId": "a-…" \| null}` sorted by `scheduledAt`. |
| `POST /api/alarms` | `{"name": "Weekday wake-up", "localTime": "2026-10-30T07:00"}` | `201 {"alarm": Alarm}` with `status: "saved"`. Errors: `422 invalid_alarm`. |
| `DELETE /api/alarms/{id}` | — | `200 {"deleted": "a-…"}`. Deleting the armed alarm disarms it. `404 alarm_not_found`. |
| `POST /api/alarms/{id}/arm` | — | `200 {"armedAlarm": Alarm, "replacedAlarm": Alarm \| null}`. Errors: `404`, `409 not_ready` (with `missing`), `409 alarm_in_past`, `409 alarm_used`, `409 run_active`. Arming the already-armed alarm is a no-op `200` with `replacedAlarm: null`. |
| `POST /api/alarms/disarm` | — | `200 {"disarmedAlarm": Alarm \| null}`. |

Validation rules for `POST /api/alarms`:

- `name`: a string, trimmed, 1–60 characters.
- `localTime`: exactly `YYYY-MM-DDTHH:MM` (24-hour), a real calendar date and time, interpreted in the host's local timezone, and strictly in the future.

Example messages: `"Please give the alarm a name."`, `"Please choose a date and time for the alarm."`, `"The time must look like 2026-10-30T07:00 (date, then 24-hour time)."`, `"2026-02-30T07:00 is not a real date and time."`, `"That time is in the past (it is now 21:49 on 29 Oct 2026). Choose a future date and time."`

**Alarm object** (persisted in `data/alarms.json`; the alarms survive restarts):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `"a-"` + 8 hex characters. |
| `name` | string | 1–60 characters, trimmed. |
| `localTime` | string | `YYYY-MM-DDTHH:MM` as entered. |
| `scheduledAt` | string | ISO 8601 with offset. |
| `createdAt` | string | ISO 8601 with offset. |
| `status` | `"saved"` \| `"armed"` \| `"rang"` \| `"missed"` | `rang`: it fired, and `sessionId` names the run. `missed`: it was armed but the service was not running at its time (marked on startup). A saved alarm whose time has passed keeps `status: "saved"` but can no longer be armed; compare `scheduledAt` with `serverTime` to grey it out. |
| `sessionId` | string \| null | Set when the alarm rang. |

**Only one alarm is armed at a time.** Arming another sets the previous one back to `saved`. The response's `replacedAlarm` and the snapshot's `lastArmChange` say which one was replaced. An alarm that rings is consumed: its status becomes `rang`, and `armedAlarm` becomes `null`.

### Device (FR-02, FR-09, US-02)

| Method & path | Response |
|---|---|
| `POST /api/device/connect` | `202 {"device": DeviceView}`. Status becomes `connecting`, and then `connected` once a valid `hello` (protocol 1) arrives, or `disconnected` with `lastError`. If the first attempt fails, the service does **not** keep retrying: call this endpoint again to retry. It is a no-op while connected. While `reconnecting`, it restarts the attempt immediately. |
| `POST /api/device/disconnect` | `200 {"device": DeviceView}`. Stops the link and its automatic reconnection. |

After a successful connect, any loss of the link (no pong within 2 s of a ping sent every 1 s, or no device message for 3 s) is detected within 3 s. Status becomes `reconnecting` with a `lastError` that explains it, and the service reconnects automatically (backoff 0.5 s, 1 s, 2 s …; at most 2 s apart during a run, otherwise at most 5 s). After every `hello` the service sends `sync` with the current ringing `sessionId`, or `null`.

### Presenter (FR-08, US-08)

| Method & path | Response |
|---|---|
| `POST /api/test-alarm` | `202 {"sessionId": "s-…", "fireAt": "<ISO>", "delayS": 10}`. Run state becomes `test_pending`, and at `fireAt` a **demo** session rings with exactly the same flow as a study alarm. It has the same readiness requirements as arming (`409 not_ready`). It is refused with `409 run_active` while a run is pending or ringing. It never changes the armed study alarm or the study points. |
| `POST /api/session/abort` | `200 {"run": RunView}`. Stops the ringing run as `aborted` with `abortReason: "manual"`, no reward, and sends `deactivate` (reason `aborted`) to the device. A pending test alarm is cancelled instead (`run.state` goes back to `idle`/`armed`). `409 nothing_to_stop` otherwise. |
| `POST /api/demo/reset` | `200 {"run": RunView}`. Instantly clears the current run back to ready: a ringing **demo** run is ended as `aborted` (`abortReason: "reset"`, device `deactivate` reason `reset`), a pending test alarm is cancelled, and a finished (completed/aborted) run of either mode is dismissed. It keeps any armed study alarm and never changes the study points. `409 study_run_active` if a study alarm is ringing. |

If the armed study alarm becomes due while a demo is pending or ringing, the demo is cancelled or aborted (`preempted_by_study_alarm`) and the study alarm rings.

### Log and export (FR-07, US-07)

| Method & path | Response |
|---|---|
| `GET /api/events[?mode=study\|demo]` | `200 {"events": [Event…]}`, the raw log in order. |
| `GET /api/events.csv[?mode=study\|demo]` | `text/csv` attachment, one row per event (columns below). |
| `GET /api/sessions[?mode=study\|demo]` | `200 {"sessions": [SessionSummary…]}`, one row per run derived from the log. |
| `GET /api/sessions.csv[?mode=study\|demo]` | `text/csv` attachment, one row per run. |

`?mode=study` excludes the classroom-demo runs from study results.

**Event** (one line of `data/events.jsonl`): `{"ts": "<ISO>", "event": "<name>", "sessionId": "s-…" | null, "mode": "study" | "demo" | null, …event fields}`

| `event` | Extra fields | Notes |
|---|---|---|
| `service_started`, `service_stopped` | — | |
| `alarm_created` | `alarmId`, `alarmName`, `scheduledFor` | |
| `alarm_armed` | `alarmId`, `alarmName`, `scheduledFor`, `replacedAlarmId` | |
| `alarm_disarmed` | `alarmId`, `reason` (`user`\|`deleted`) | |
| `alarm_deleted` | `alarmId`, `alarmName` | |
| `alarm_missed` | `alarmId`, `alarmName`, `scheduledFor`, `reason` | technical failure |
| `test_alarm_scheduled` | `scheduledFor`, `delayS` | demo |
| `test_alarm_cancelled` | `reason` (`manual`\|`reset`\|`preempted_by_study_alarm`) | demo |
| `ring_started` | `alarmId`, `alarmName`, `scheduledFor`, `lateMs` | |
| `sound_started` | — | from the browser |
| `sound_failed` | `error` | technical failure |
| `activation_sent` | `via` (`activate`\|`sync`) | |
| `activation_acked` | `of`, `late` | `late: true` if it came after `activation_missing` |
| `activation_missing` | `reason` (`no_ack`\|`device_not_connected`), `timeoutMs` | technical failure |
| `tap_received` | `accepted`, `reason` (`idle`\|`armed`\|`stale_session`\|`not_activated`\|`duplicate`\|null), `seq`, `tapSessionId`, `deviceId` | `sessionId` is the ringing session, if any |
| `completed` | `scheduledFor`, `responseTimeMs`, `responseTimeFlag`, `points` (10), `pointsArePreview` | successful tap |
| `aborted` | `scheduledFor`, `reason` (`manual`\|`reset`\|`preempted_by_study_alarm`\|`service_shutdown`), `points` (0) | `manual`/`reset` count as manual abort; the others are technical failures |
| `deactivation_sent`, `deactivation_acked` | `reason` / `deviceSessionId`, `ignored` | |
| `demo_reset` | `clearedSessionId` | |
| `device_connect_requested` | `host`, `port` | |
| `device_connected` | `deviceId`, `firmware`, `sensorStable` | |
| `device_connect_failed` | `error`, `host` | technical failure |
| `device_disconnected` | `reason` (`user`) | |
| `connection_lost` | `reason`, `deviceId` | technical failure; carries the `sessionId` if it happened during a run |
| `connection_restored` | `deviceId`, `downMs` | |
| `device_rebooted` | `deviceId`, `previousUptimeMs`, `uptimeMs`, `detectedBy` (`uptimeMs`\|`tapSeq`) | resets duplicate-seq tracking |
| `sync_sent` | `syncSessionId`, `reason`? | |
| `indicator_timed_out` | — | technical failure (15-min device safety timeout) |
| `device_error` | `code`, `detail` | technical failure |
| `browser_lost_during_run` | — | technical failure: the last browser disconnected while ringing |
| `audio_test_ok`, `audio_test_failed` | `error` | |

**Events CSV columns:** `ts, event, sessionId, mode, runType, category, accepted, reason, responseTimeMs, points, scheduledFor, details`

- `runType`: `study` or `classroom_demo`.
- `category`: `successful_tap`, `manual_abort`, `technical_failure` or empty.
- `details`: the remaining fields as compact JSON.

**Sessions CSV columns:** `sessionId, mode, runType, alarmName, scheduledFor, ringStartedAt, soundStartedAt, activationAckedAt, tapAcceptedAt, endedAt, outcome, abortReason, responseTimeMs, points, pointsArePreview, ignoredTaps, technicalIssues`

- `outcome`: `successful_tap`, `manual_abort`, `technical_failure` or `incomplete`.
- `technicalIssues` is `;`-separated.

### Test hooks (`WAKEQUEST_TEST_MODE=1` only; for the Playwright suite)

These routes do not exist unless test mode is on. The `fake-device/*` hooks also need `WAKEQUEST_DEVICE=fake` (otherwise `404 no_fake_device`).

| Method & path | Body | Effect |
|---|---|---|
| `GET /api/_test/fake-device` | — | Fake-device state: `connected`, `powered`, `sessionId`, `indicator`, `indicatorTimedOut`, `tapSeq`, `sensorStable`, `sendAcks`, `connections`, `received` (last 50 commands). |
| `POST /api/_test/fake-device/tap` | `{}` \| `{"sessionId": "…" \| null}` \| `{"seq": n}` | Emit a tap with the device's current session (default), or a chosen one; `seq` replays a sequence number. Response `{"sent": bool, "tap": {...}}`. Taps are not queued while disconnected (`sent: false`). |
| `POST /api/_test/fake-device/drop` | — | Abruptly drop the TCP connection (device keeps its state). |
| `POST /api/_test/fake-device/power-off` | — | Simulate power loss: no pongs, no messages, connections refused. |
| `POST /api/_test/fake-device/power-on` | `{"reboot": true}` | Power back on; `reboot: true` (default) clears session, indicator and `tapSeq`, like a real board. |
| `POST /api/_test/fake-device/no-ack` | `{"enabled": true}` | Withhold acks (`false` restores them). Commands are still applied. |
| `POST /api/_test/fake-device/sensor-unstable` | — | Report `sensorStable: false`. |
| `POST /api/_test/fake-device/sensor-stable` | — | Report `sensorStable: true`. |
| `POST /api/_test/test-alarm-delay` | `{"seconds": 2}` | Change the test-alarm delay (0–3600 s). |

## WebSocket `/ws` (browser)

One connection per browser tab, `ws://127.0.0.1:8000/ws`, with JSON text frames.

### Service → browser

| `type` | When | Body |
|---|---|---|
| `snapshot` | Immediately after connecting, and after every state change (including device status changes, but not every 1 s device heartbeat). | The full [snapshot](#snapshot). `rev` increases monotonically; ignore one with a lower `rev` than the one you already have. |
| `error` | A browser message was refused. | `{"type": "error", "error": "<code>", "message": "…", "inReplyTo": "<type>"}`. Codes: `bad_json`, `bad_message`, `unknown_type`, `stale_session`. |

### Browser → service

| `type` | Body | Meaning |
|---|---|---|
| `audio_test_ok` | — | "Enable and test sound" played audibly. It sets `audio.tested` / `readiness.audioTested`. **It only lasts while this connection is open:** when the tab disconnects, it is reset and the test must be repeated. |
| `audio_test_failed` | `{"error": "NotAllowedError"}` | The test playback failed. It clears this browser's audio-ready flag and is logged. |
| `sound_started` | `{"sessionId": "s-…"}` | The looping alarm sound actually started for the ringing session. Its receipt time is the start of the **alarm response time**. Only the first one per session counts. |
| `sound_failed` | `{"sessionId": "s-…", "error": "…"}` | Playback failed (for example because of autoplay). It is logged and shown as `session.sound.status: "failed"`. A later `sound_started` (for example after the user clicks to retry) still counts. |
| `get_snapshot` | — | Ask for a fresh snapshot. |

`sound_started`/`sound_failed` with a `sessionId` that is not the ringing session get an `error` with `stale_session`.

**Browser behaviour expected by the service:**

- While `run.state == "ringing"`, play the looping sound and keep playing it, including while `device.status` is `reconnecting`. When the snapshot shows a new ringing `session.sessionId` (or after the tab reconnects while ringing), start the sound and send `sound_started` or `sound_failed` for that id. Stop the sound when `run.state` leaves `ringing`.
- There is no dismiss or snooze action. The only way out of `ringing` is an accepted tap, `POST /api/session/abort` (presenter) or `POST /api/demo/reset` (demo only).

## Snapshot

`GET /api/state` returns the snapshot, and `/ws` pushes the same object.

| Field | Type | Meaning |
|---|---|---|
| `type` | `"snapshot"` | |
| `rev` | int | Increases with every push. |
| `serverTime` | ISO string | The service clock; use it to compute countdowns (`testAlarm.fireAt`, `armedAlarm.scheduledAt`) and elapsed ring time without trusting the browser clock. |
| `timezone` | `{name, utcOffset}` | The host timezone (e.g. `"+01:00"`). |
| `testMode` | bool | `WAKEQUEST_TEST_MODE`. |
| `device` | DeviceView | See below. |
| `audio` | `{tested: bool, browsersConnected: int}` | |
| `readiness` | Readiness | Whether the *hardware and sound* are ready to arm or start a test alarm. It does not consider whether a run is active. |
| `alarms` | Alarm[] | All saved alarms, sorted by `scheduledAt`. |
| `armedAlarm` | Alarm \| null | The one armed alarm ("next alarm"). |
| `lastArmChange` | `{at, armedAlarmId, replacedAlarmId, message}` \| null | The last arm or disarm; `replacedAlarmId` says which alarm an arm replaced, and `message` is ready-made text. |
| `run` | RunView | See below. |
| `points` | `{study, studySessionsCompleted, perCompletion}` | `study` = 10 × completed study sessions, **derived from the event log** (never stored as a counter). Demo runs never change it. |

**DeviceView**

| Field | Type | Meaning |
|---|---|---|
| `status` | `"disconnected"` \| `"connecting"` \| `"connected"` \| `"reconnecting"` | `connected` only after a valid `hello`. `reconnecting` = the link was lost and automatic reconnection is running (not connected). |
| `host`, `port` | string, int | The configured device address. |
| `deviceId` | string \| null | e.g. `"wq-a1b2c3"`, from `hello`. It is kept after a loss so the UI can name the device. |
| `firmware` | string \| null | |
| `sensorStable` | bool \| null | The tap input has been idle ≥ 2 s (from `hello`/`status`). |
| `indicator` | bool \| null | Whether the device reports its colour cycle on. |
| `indicatorTimedOut` | bool | The device's 15-min safety timeout switched the light off. |
| `deviceSessionId` | string \| null | The session the device holds. |
| `tapSeq`, `rssi` | int \| null | Diagnostics. |
| `lastError` | string \| null | User-facing explanation of the last failure (connect failed, link lost, reconnect attempts). It is `null` when connected. |
| `connectedAt`, `lostAt`, `lastMessageAt` | ISO \| null | `lostAt` is set while `reconnecting`. |
| `lastTap` | `{at, seq, tapSessionId, accepted, reason}` \| null | The last tap the service received, with the ignore `reason`. |

**Readiness:** `{deviceConnected, sensorStable, audioTested, canArm, missing: [{code, message}]}`

- `code` is one of `device_not_connected`, `sensor_not_stable` (only when connected) or `audio_not_tested`.
- `message` is display text.
- `canArm == (missing is empty)`.

**RunView:** `{state, session, testAlarm}`

| `state` | Meaning | `session` | `testAlarm` |
|---|---|---|---|
| `idle` | Nothing armed or running. | null | null |
| `armed` | A study alarm is armed and waiting (the device light stays off). | null | null |
| `test_pending` | "Test alarm in 10 seconds" is counting down. | null | `{sessionId, fireAt}` |
| `ringing` | The alarm is active: play the sound and show "Pick up your device and tap it". | Session (`phase: "ringing"`) | null |
| `completed` | An accepted tap finished the run: "Morning quest completed!". | Session with `result` | null |
| `aborted` | The run was stopped without a reward. | Session with `result.points: 0` | null |

Priority when several apply: `ringing` > `test_pending` > the finished session (`completed`/`aborted`) > `armed` > `idle`. A finished session stays visible until the next arm, test alarm or demo reset; `armedAlarm` remains available in parallel.

**Session**

| Field | Type | Meaning |
|---|---|---|
| `sessionId` | string | Unique per run, e.g. `"s-20261030-070000-3f9c"` (≤ 64 characters). |
| `mode` | `"study"` \| `"demo"` | `demo` = presenter test alarm. Show it as a labelled test. |
| `alarmId`, `alarmName` | string \| null, string | The test alarm has `alarmId: null` and `alarmName: "Test alarm"`. |
| `scheduledFor`, `ringStartedAt` | ISO | |
| `phase` | `"ringing"` \| `"completed"` \| `"aborted"` | |
| `sound` | `{status: "pending"\|"started"\|"failed", startedAt, error}` | From the browser's `sound_started`/`sound_failed`. |
| `activation` | `{status: "pending"\|"sent"\|"acked"\|"missing", sentAt, ackedAt}` | `missing` = no ack within 2 s, or the device was not connected at ring time. **Show this visibly.** It can still become `acked` later (e.g. after reconnect + sync). Taps only count once it is `acked`. |
| `tapAcceptedAt`, `endedAt` | ISO \| null | |
| `abortReason` | `"manual"` \| `"reset"` \| `"preempted_by_study_alarm"` \| `"service_shutdown"` \| null | |
| `result` | null while ringing; `{points, pointsArePreview, responseTimeMs, responseTimeFlag}` when finished | Completed: `points: 10`. `pointsArePreview: true` for demo runs (show it as a preview; it is not added to `points.study`). `responseTimeMs` = accepted tap receipt − `sound_started` receipt; label it **"Alarm response time"**. If the sound was never confirmed, `responseTimeMs: null` and `responseTimeFlag: "sound_not_confirmed"` (show it as unavailable). Aborted: `points: 0`. |
| `connectionLostDuringRun` | bool | The device link dropped at some point during this run. |
| `technicalIssues` | string[] | Any of `activation_missing`, `connection_lost`, `sound_failed`, `indicator_timed_out`, `browser_lost_during_run`. |

### Example: idle (fresh start, device not connected)

```json
{
  "type": "snapshot",
  "rev": 1,
  "serverTime": "2026-10-29T22:00:00.000+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "disconnected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": null,
    "firmware": null,
    "sensorStable": null,
    "indicator": null,
    "indicatorTimedOut": false,
    "deviceSessionId": null,
    "tapSeq": null,
    "rssi": null,
    "lastError": null,
    "connectedAt": null,
    "lostAt": null,
    "lastMessageAt": null,
    "lastTap": null
  },
  "audio": {
    "tested": false,
    "browsersConnected": 0
  },
  "readiness": {
    "deviceConnected": false,
    "sensorStable": false,
    "audioTested": false,
    "canArm": false,
    "missing": [
      {
        "code": "device_not_connected",
        "message": "Connect the device first: it is not connected."
      },
      {
        "code": "audio_not_tested",
        "message": "Enable and test sound in the browser first."
      }
    ]
  },
  "alarms": [],
  "armedAlarm": null,
  "lastArmChange": null,
  "run": {
    "state": "idle",
    "session": null,
    "testAlarm": null
  },
  "points": {
    "study": 0,
    "studySessionsCompleted": 0,
    "perCompletion": 10
  }
}
```

### Example: armed (device connected, sound tested, a second alarm replaced the first)

```json
{
  "type": "snapshot",
  "rev": 2,
  "serverTime": "2026-10-29T22:00:00.000+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "connected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": false,
    "indicatorTimedOut": false,
    "deviceSessionId": null,
    "tapSeq": 0,
    "rssi": -58,
    "lastError": null,
    "connectedAt": "2026-10-29T22:00:00.000+01:00",
    "lostAt": null,
    "lastMessageAt": "2026-10-29T22:00:00.000+01:00",
    "lastTap": null
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": true,
    "sensorStable": true,
    "audioTested": true,
    "canArm": true,
    "missing": []
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "armed",
      "sessionId": null
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": {
    "id": "a-7c1e0d42",
    "name": "Weekday wake-up",
    "localTime": "2026-10-30T07:00",
    "scheduledAt": "2026-10-30T07:00:00.000+01:00",
    "createdAt": "2026-10-29T22:00:00.000+01:00",
    "status": "armed",
    "sessionId": null
  },
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "armed",
    "session": null,
    "testAlarm": null
  },
  "points": {
    "study": 0,
    "studySessionsCompleted": 0,
    "perCompletion": 10
  }
}
```

### Example: ringing (study alarm; sound started, activation acknowledged)

```json
{
  "type": "snapshot",
  "rev": 3,
  "serverTime": "2026-10-30T07:00:10.420+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "connected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": true,
    "indicatorTimedOut": false,
    "deviceSessionId": "s-20261030-070000-3f9c",
    "tapSeq": 0,
    "rssi": -60,
    "lastError": null,
    "connectedAt": "2026-10-29T22:00:00.000+01:00",
    "lostAt": null,
    "lastMessageAt": "2026-10-30T07:00:10.420+01:00",
    "lastTap": null
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": true,
    "sensorStable": true,
    "audioTested": true,
    "canArm": true,
    "missing": []
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "rang",
      "sessionId": "s-20261030-070000-3f9c"
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": null,
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "ringing",
    "session": {
      "sessionId": "s-20261030-070000-3f9c",
      "mode": "study",
      "alarmId": "a-7c1e0d42",
      "alarmName": "Weekday wake-up",
      "scheduledFor": "2026-10-30T07:00:00.000+01:00",
      "ringStartedAt": "2026-10-30T07:00:00.120+01:00",
      "phase": "ringing",
      "sound": {
        "status": "started",
        "startedAt": "2026-10-30T07:00:00.420+01:00",
        "error": null
      },
      "activation": {
        "status": "acked",
        "sentAt": "2026-10-30T07:00:00.120+01:00",
        "ackedAt": "2026-10-30T07:00:00.170+01:00"
      },
      "tapAcceptedAt": null,
      "endedAt": null,
      "abortReason": null,
      "result": null,
      "connectionLostDuringRun": false,
      "technicalIssues": []
    },
    "testAlarm": null
  },
  "points": {
    "study": 0,
    "studySessionsCompleted": 0,
    "perCompletion": 10
  }
}
```

### Example: ringing with the device disconnected (link lost, reconnecting)

The run stays `ringing` and the sound keeps playing. `device.status` is `reconnecting`, with `lastError` and `lostAt` set. The UI must report this visibly and never treat it as completion.

```json
{
  "type": "snapshot",
  "rev": 4,
  "serverTime": "2026-10-30T07:00:14.420+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "reconnecting",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": true,
    "indicatorTimedOut": false,
    "deviceSessionId": "s-20261030-070000-3f9c",
    "tapSeq": 0,
    "rssi": -60,
    "lastError": "Connection to the device was lost (no message from the device for 3 s). Reconnecting automatically…",
    "connectedAt": "2026-10-29T22:00:00.000+01:00",
    "lostAt": "2026-10-30T07:00:14.420+01:00",
    "lastMessageAt": "2026-10-30T07:00:10.420+01:00",
    "lastTap": null
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": false,
    "sensorStable": false,
    "audioTested": true,
    "canArm": false,
    "missing": [
      {
        "code": "device_not_connected",
        "message": "Connect the device first: it is not connected."
      }
    ]
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "rang",
      "sessionId": "s-20261030-070000-3f9c"
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": null,
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "ringing",
    "session": {
      "sessionId": "s-20261030-070000-3f9c",
      "mode": "study",
      "alarmId": "a-7c1e0d42",
      "alarmName": "Weekday wake-up",
      "scheduledFor": "2026-10-30T07:00:00.000+01:00",
      "ringStartedAt": "2026-10-30T07:00:00.120+01:00",
      "phase": "ringing",
      "sound": {
        "status": "started",
        "startedAt": "2026-10-30T07:00:00.420+01:00",
        "error": null
      },
      "activation": {
        "status": "acked",
        "sentAt": "2026-10-30T07:00:00.120+01:00",
        "ackedAt": "2026-10-30T07:00:00.170+01:00"
      },
      "tapAcceptedAt": null,
      "endedAt": null,
      "abortReason": null,
      "result": null,
      "connectionLostDuringRun": true,
      "technicalIssues": [
        "connection_lost"
      ]
    },
    "testAlarm": null
  },
  "points": {
    "study": 0,
    "studySessionsCompleted": 0,
    "perCompletion": 10
  }
}
```

### Example: completed (after reconnect + sync, then an accepted tap)

```json
{
  "type": "snapshot",
  "rev": 5,
  "serverTime": "2026-10-30T07:00:36.960+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "connected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": false,
    "indicatorTimedOut": false,
    "deviceSessionId": null,
    "tapSeq": 1,
    "rssi": -60,
    "lastError": null,
    "connectedAt": "2026-10-30T07:00:16.920+01:00",
    "lostAt": null,
    "lastMessageAt": "2026-10-30T07:00:36.960+01:00",
    "lastTap": {
      "at": "2026-10-30T07:00:36.920+01:00",
      "seq": 1,
      "tapSessionId": "s-20261030-070000-3f9c",
      "accepted": true,
      "reason": null
    }
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": true,
    "sensorStable": true,
    "audioTested": true,
    "canArm": true,
    "missing": []
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "rang",
      "sessionId": "s-20261030-070000-3f9c"
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": null,
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "completed",
    "session": {
      "sessionId": "s-20261030-070000-3f9c",
      "mode": "study",
      "alarmId": "a-7c1e0d42",
      "alarmName": "Weekday wake-up",
      "scheduledFor": "2026-10-30T07:00:00.000+01:00",
      "ringStartedAt": "2026-10-30T07:00:00.120+01:00",
      "phase": "completed",
      "sound": {
        "status": "started",
        "startedAt": "2026-10-30T07:00:00.420+01:00",
        "error": null
      },
      "activation": {
        "status": "acked",
        "sentAt": "2026-10-30T07:00:00.120+01:00",
        "ackedAt": "2026-10-30T07:00:00.170+01:00"
      },
      "tapAcceptedAt": "2026-10-30T07:00:36.920+01:00",
      "endedAt": "2026-10-30T07:00:36.920+01:00",
      "abortReason": null,
      "result": {
        "points": 10,
        "pointsArePreview": false,
        "responseTimeMs": 36500,
        "responseTimeFlag": null
      },
      "connectionLostDuringRun": true,
      "technicalIssues": [
        "connection_lost"
      ]
    },
    "testAlarm": null
  },
  "points": {
    "study": 10,
    "studySessionsCompleted": 1,
    "perCompletion": 10
  }
}
```

### Example: test_pending (presenter "Test alarm in 10 seconds")

```json
{
  "type": "snapshot",
  "rev": 6,
  "serverTime": "2026-10-30T07:00:36.960+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "connected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": false,
    "indicatorTimedOut": false,
    "deviceSessionId": null,
    "tapSeq": 1,
    "rssi": -60,
    "lastError": null,
    "connectedAt": "2026-10-30T07:00:16.920+01:00",
    "lostAt": null,
    "lastMessageAt": "2026-10-30T07:00:36.960+01:00",
    "lastTap": {
      "at": "2026-10-30T07:00:36.920+01:00",
      "seq": 1,
      "tapSessionId": "s-20261030-070000-3f9c",
      "accepted": true,
      "reason": null
    }
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": true,
    "sensorStable": true,
    "audioTested": true,
    "canArm": true,
    "missing": []
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "rang",
      "sessionId": "s-20261030-070000-3f9c"
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": null,
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "test_pending",
    "session": null,
    "testAlarm": {
      "sessionId": "s-20261030-071230-81ad",
      "fireAt": "2026-10-30T07:00:46.960+01:00"
    }
  },
  "points": {
    "study": 10,
    "studySessionsCompleted": 1,
    "perCompletion": 10
  }
}
```

### Example: aborted (demo run stopped with "Stop run (aborted)")

```json
{
  "type": "snapshot",
  "rev": 7,
  "serverTime": "2026-10-30T07:00:52.050+01:00",
  "timezone": {
    "name": "UTC+01:00",
    "utcOffset": "+01:00"
  },
  "testMode": false,
  "device": {
    "status": "connected",
    "host": "wakequest.local",
    "port": 81,
    "deviceId": "wq-a1b2c3",
    "firmware": "0.1.0",
    "sensorStable": true,
    "indicator": false,
    "indicatorTimedOut": false,
    "deviceSessionId": null,
    "tapSeq": 1,
    "rssi": -60,
    "lastError": null,
    "connectedAt": "2026-10-30T07:00:16.920+01:00",
    "lostAt": null,
    "lastMessageAt": "2026-10-30T07:00:52.050+01:00",
    "lastTap": {
      "at": "2026-10-30T07:00:36.920+01:00",
      "seq": 1,
      "tapSessionId": "s-20261030-070000-3f9c",
      "accepted": true,
      "reason": null
    }
  },
  "audio": {
    "tested": true,
    "browsersConnected": 1
  },
  "readiness": {
    "deviceConnected": true,
    "sensorStable": true,
    "audioTested": true,
    "canArm": true,
    "missing": []
  },
  "alarms": [
    {
      "id": "a-7c1e0d42",
      "name": "Weekday wake-up",
      "localTime": "2026-10-30T07:00",
      "scheduledAt": "2026-10-30T07:00:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "rang",
      "sessionId": "s-20261030-070000-3f9c"
    },
    {
      "id": "a-19bb03fe",
      "name": "Early lecture",
      "localTime": "2026-10-30T07:30",
      "scheduledAt": "2026-10-30T07:30:00.000+01:00",
      "createdAt": "2026-10-29T22:00:00.000+01:00",
      "status": "saved",
      "sessionId": null
    }
  ],
  "armedAlarm": null,
  "lastArmChange": {
    "at": "2026-10-29T22:00:00.000+01:00",
    "armedAlarmId": "a-7c1e0d42",
    "replacedAlarmId": "a-19bb03fe",
    "message": "“Weekday wake-up” is now armed for 07:00 on 30 Oct 2026; it replaces “Early lecture”."
  },
  "run": {
    "state": "aborted",
    "session": {
      "sessionId": "s-20261030-071230-81ad",
      "mode": "demo",
      "alarmId": null,
      "alarmName": "Test alarm",
      "scheduledFor": "2026-10-30T07:00:46.960+01:00",
      "ringStartedAt": "2026-10-30T07:00:46.960+01:00",
      "phase": "aborted",
      "sound": {
        "status": "pending",
        "startedAt": null,
        "error": null
      },
      "activation": {
        "status": "acked",
        "sentAt": "2026-10-30T07:00:46.960+01:00",
        "ackedAt": "2026-10-30T07:00:47.010+01:00"
      },
      "tapAcceptedAt": null,
      "endedAt": "2026-10-30T07:00:52.010+01:00",
      "abortReason": "manual",
      "result": {
        "points": 0,
        "pointsArePreview": true,
        "responseTimeMs": null,
        "responseTimeFlag": null
      },
      "connectionLostDuringRun": false,
      "technicalIssues": []
    },
    "testAlarm": null
  },
  "points": {
    "study": 10,
    "studySessionsCompleted": 1,
    "perCompletion": 10
  }
}
```
