# WakeQuest — Implementation Plan (Sprint 1)

## Context

`planning_prompt.md` asks for a concrete, reviewable plan before any code is written. The plan covers the WakeQuest prototype described in `context.md`: a website alarm that can only be silenced by walking to an ESP32 controller across the room and tapping it, followed by a small reward and a logged session.

Three findings from planning changed the inputs:

1. **The indicator module.** Your photo shows the kit's **RGB LED module** (4 pins printed `R G B -`, resistors R1–R3). It is not the 3-pin Seven-Color Flash Module. You decided to use the RGB module and to adapt the earlier prompts to match. This also removes any 5 V switching from the design, because everything runs at 3.3 V.
2. **The breadboard.** The 38-pin ESP32 board is pushed into the half-size breadboard. It covers columns b–i, so none of its pins can be reached. The board comes out of the breadboard, and the breadboard becomes the module "hub".
3. **The stack.** You chose Python FastAPI with vanilla JS, a phone hotspot for networking, and kit parts plus jumpers only (all jumper types, no multimeter).

After approval, the first action is to save this plan as `PLAN.md` in the repo root and create `CLAUDE.md` (draft in §8).

---

## 1. Scope and Sprint Goal

**Intended outcome:** one complete, logged path:

> create alarm → connect board → test sound → arm → ring (sound and RGB colour cycle) → walk over, pick up, tap → sound and light stop → "Morning quest completed!", 10 points and the alarm response time

It must also have a presenter test mode (alarm in 10 s), abort, and reset. It must run with real hardware on a phone-hotspot network.

**In scope (all of US-01 … US-08, FR-01 … FR-09, QR-01 … QR-07):**
- One board, one local service, one browser tab, one-off alarms in local time, and one armed alarm.
- A JSON/JSONL event log with CSV export.
- An evaluation-sheet template.

**Out of scope (backlog):**
- Repeating alarms, snooze, device discovery, onboarding, accounts, or cloud.
- Phone as the alarm browser.
- An enclosure beyond tape or cardboard, battery circuitry, and an accelerometer or verified pickup.
- The 12-morning Stage B study itself. It runs *after* the sprint, using the sprint's logging.

**Sprint Goal:**
> *By the end of the sprint I can show, with the real ESP32, Tap Module and RGB LED module, that an alarm only stops when someone walks to the device and taps it. Each run ends in exactly one reward and one logged session, so the morning comparison study can start with trustworthy data.*

---

## 2. Architecture

### Recommendation (smallest practical)

| Part | Technology | Responsibility |
|---|---|---|
| **Website** | Vanilla HTML/CSS/ES-module JS served by the service. No build step. Web Audio API. | Alarm form and list, connect/status, "Enable and test sound", arm, active-alarm takeover, celebration, presenter controls, log view. It renders a **state snapshot** pushed by the service and holds no business logic. |
| **Local service** | Python 3.12 (via uv), FastAPI 0.162, uvicorn 0.54, `websockets` 17.3 | **The single source of truth.** It owns the scheduler, the session state machine, arming preconditions, tap validation, points, the event log, and storage. It serves the website, a REST API, and `/ws` (browser). It also runs a WebSocket **client** to the device. |
| **Firmware** | Arduino-ESP32 core 3.3.12 via arduino-cli 1.5.1; libraries `WebSockets` (Links2004) 2.7.x, `ArduinoJson` 7.4.x, and the built-in `ESPmDNS` | It joins the hotspot (STA mode), advertises `wakequest.local`, and runs a WS server on port 81. It debounces the tap input and sends `tap` events. It runs the 7-colour cycle only when told to. Safety: the light switches off after 15 min. |
| **Comms** | WebSocket + JSON in both links. Service↔device: WS ping every 1 s with a 2 s timeout, plus a 1 s `status` heartbeat from the device. | Loss is detected in ≤ 3 s, inside QR-04's 5 s. Every browser change gets a full snapshot, so reconnecting is idempotent. |

**Why the service is a client to the device:**
- "Connect device" maps naturally onto *the service opening a connection and waiting for `hello`*. That gives FR-02 its "Connected only after a board response" behaviour.
- Reconnect logic lives in testable Python instead of firmware.
- The laptop's changing DHCP IP never has to be compiled into the firmware.

**Fallback if mDNS fails on the hotspot:** set `WAKEQUEST_DEVICE_HOST=<IP printed on serial>`. The service resolves IPv4 explicitly, which avoids a known macOS `.local` IPv6 stall.

```
 ┌──────────── Laptop (awake, caffeinate) ────────────┐            ┌──── ESP32 DevKitC-style ────┐
 │ Chrome tab  ⇄  /ws (state snapshots, sound events) │            │ WS server :81 (wakequest.local)│
 │   ↑ REST /api/* (alarms, connect, arm, test, abort) │            │  hello / status(1 s) / tap  →   │
 │ FastAPI service ── scheduler ── session state machine ── device_link (ws client) ⇄ ← activate/deactivate │
 │   └─ data/alarms.json · data/events.jsonl · CSV export │          │ GPIO27 ← Tap S   GPIO26/25/33 → R/G/B │
 └─────────────────────────────────────────────────────┘            └──────────────────────────────┘
                         phone hotspot (WPA2-Personal)
```

### Key data flow
1. **Scheduling:**
   - The scheduler fires at T, moves the session `armed → ringing`, logs `ring_started`, and pushes a snapshot.
   - The browser starts the looping sound and reports `sound_started` or `sound_failed`.
   - The service sends `activate{sessionId}`, and the device answers `ack`.
   - If no ack arrives within 2 s, `activation_missing` is logged and shown.
2. **Tap acceptance:**
   - The device ISR flags an edge, and the main loop applies a 250 ms lockout (tunable) and sends `tap{seq, sessionId}`.
   - The service accepts a tap **only if** `state == ringing`, `tap.sessionId == current`, the activation was acknowledged, and the session is not already completed.
   - The completion transition is atomic. It sends `deactivate` and pushes a snapshot, and the browser stops the sound and celebrates.
3. **Points:** never stored as a counter. They are **derived** as 10 × (completed *study* sessions), and demo sessions show a "demo preview" instead. This makes a duplicate award impossible.
4. **Response time:** the `tap_received` minus `sound_started`, both stamped by the service clock. If the sound was never confirmed, the response time is shown as "unavailable" and flagged.
5. **Reconnect:** the device's `hello` includes its current `sessionId` and indicator state. The service replies with `sync`, which tells the device to switch the light on or off for the current session. The browser re-renders from the snapshot.

### Proposed project structure
```
firmware/
  bench_probe/bench_probe.ino      # no-Wi-Fi bring-up: prints tap level/edges, drives R/G/B on serial commands
  wakequest/wakequest.ino          # main firmware
  wakequest/config.h               # pins, TAP_ACTIVE_LEVEL, lockout, RGB pin map, hostname
  wakequest/secrets.example.h      # WIFI_SSID / WIFI_PASS placeholders (you copy to secrets.h — gitignored)
  wakequest/tap_detector.h         # pure C++ debounce/lockout logic (host-testable)
  wakequest/rgb_cycle.h            # pure 7-colour sequence logic (host-testable)
  test/test_logic.cpp              # host unit tests (clang++)
wakequest/                         # Python package
  main.py  api.py  state.py  scheduler.py  device_link.py  fake_device.py  storage.py  clock.py  models.py
web/  index.html  app.js  styles.css  sound.js  fonts/
tests/                             # pytest (unit + integration with fake device)
e2e/  *.spec.ts                    # @playwright/test
docs/ protocol.md  wiring.md  test-log.md  evaluation-sheet.csv  usability-script.md
pyproject.toml  uv.lock  package.json  playwright.config.ts  PLAN.md  CLAUDE.md
data/                              # gitignored runtime data
```

### Tradeoffs
- **Python and Node both present.** FastAPI was your choice, so Node exists only for `@playwright/test`. That costs two package managers but keeps the service in Python.
- **Websocket instead of HTTP polling.** It gives < 1 s latency and fast loss detection. It costs a WS library on the ESP32, which is mature.
- **Server-side scheduling.** It avoids background-tab timer throttling. The browser still has to be open, visible and awake to play sound. That is a documented first-sprint constraint, mitigated by `caffeinate`, Wake Lock and an overnight rehearsal.
- **JSON/JSONL instead of SQLite.** Hundreds of rows don't need a database. Writes are atomic (`tmp` + `os.replace`).

### UI direction (frontend-design pass)
- **Concept:** "pre-dawn to daylight". The whole screen state *is* the alarm state, and every state also has a plain-text headline (QR-07).
  - Setup and armed: night ink.
  - Ringing: full-bleed dawn amber.
  - Completed: daylight.
- **Palette:**
  - Night `#1B2340`
  - Dawn amber `#F6B83C`
  - Daylight `#EEF4F7`
  - Ink `#15192B`
  - Leaf (success) `#2F7D57`
  - Alert `#C2363F` (errors only)
- **Type:** Atkinson Hyperlegible Next for body and UI, chosen for legibility to half-asleep eyes. Bricolage Grotesque, heavy and condensed, is used only for the giant state headline. Both are OFL and self-hosted in `web/fonts/`, so no internet is needed.
- **The single bold moment:** the ringing takeover. A huge "Pick up your device and tap it" is shown with an elapsed timer, and there is no dismiss or snooze button. The presenter "Stop run (aborted)" control sits in a separate small corner.
  - The celebration is a sun disk that bursts into 10 rays, one per point, under "Morning quest completed!".
  - Under `prefers-reduced-motion` it becomes a static sun with a fade.
- **Copy:** buttons say exactly what they do: "Connect device", "Enable and test sound", "Arm alarm", "Test alarm in 10 seconds", "Stop run (aborted)", "Reset demo". There is one polite `role="status"` region, and `role="alert"` is used only for blocked sound or a lost device.

---

## 3. Intuitive hardware setup

### 3.1 What's on your desk (from the photo + kit docs)

| Item | Identification | Confidence |
|---|---|---|
| ESP32 board | 38-pin DevKitC-V4-style clone with micro-USB, an `ESP32-WROOM-32D` module, a **Silicon Labs CP2102** USB chip, and `Boot` / `EN` buttons. The right header reads (USB end → antenna end) `5V, ?, D3, D2, 13, GND, 12, 14, 27, 26, 25, 33, 32, 35, 34, VN, VP, EN, 3V3`. The left header reads `CLK, D0, D1, 15, 2, 0, 4, 16, 17, 5, 18, 19, GND, 21, RX, TX, 22, 23, GND`. | High, from the photo and its match to the Espressif DevKitC V4 table. The 2nd right pin from the top is unreadable (CMD or GND), and we don't use it. |
| Tap Module (Hit Sensor / knock switch) | 3 pins. `S` is printed on the left and `-` on the right. The middle pin is unprinted and is "+" per the Elegoo datasheet. It is passive (spring contact + 10 kΩ). | Labels: high. The middle = "+" is from the datasheet. |
| RGB LED module (Lesson 11) | 4 pins printed `R G B -`, a common cathode on `-`. There are three 150 Ω series resistors (R1–R3, code "151"), and Vf is R 1.8–2.1 V and G/B 3.0–3.2 V. **The tutorial says the silkscreen swaps R and B.** | High. The photo matches the datasheet image. |
| Breadboard | Half-size: 30 rows, a–e and f–j halves, a ± rail on each side. | High. Whether the rails are split mid-length is **not established**. |

### 3.2 How the breadboard works (30-second version)
- Each **row** is split in two. Holes **a–e** in one row are connected to each other, and **f–j** in that row are connected to each other. The two halves are *not* connected across the middle gap.
- The long **rails** at the sides run vertically. Every hole along the red **+** line is connected, and every hole along the blue **−** line is connected.
  - Some boards split the rails halfway, so we keep everything in **rows 1–14**.
- A module pin pushed into, say, c3 is connected to anything else in a3, b3, d3 or e3.

### 3.3 Voltage plan
- **Everything runs at 3.3 V.** Nothing connects to the board's `5V` pin.
  - The Tap Module is powered from `3V3`. Because it is passive, its `S` pin can only ever be 0–3.3 V, which is safe for ESP32 GPIO (VIH max ≈ 3.6 V; source: Espressif WROOM-32D datasheet).
  - The RGB LED is driven directly from GPIOs through its own 150 Ω resistors.
    - Red: ≈ (3.3 − 1.9)/150 ≈ 9 mA.
    - Green/blue: ≈ 1–2 mA, so expect them dimmer.
  - The total is well under the per-pin practice limit (~20 mA) and the 40 mA typical drive.
- **No level shifter, transistor or relay is needed.**
- Power: USB from the laptop on the bench, and a USB power bank for the pick-up runs.

### 3.4 Pin choice and wiring table

Full-colour diagram: [docs/wiring.svg](docs/wiring.svg) (open it in a browser next to the hardware).

- GPIO 27, 26, 25 and 33 are adjacent on the right header.
- None of them is a strapping pin, flash pin, input-only pin or UART pin (source: ESP-IDF GPIO docs).

| # | From (ESP32 label) | Wire | To (breadboard) | Connects to | Purpose |
|---|---|---|---|---|---|
| W1 | `3V3` (right header, antenna end) | F–M | left **+** rail, row 14 | — | 3.3 V supply rail |
| W2 | `GND` (right header, between `13` and `12`) | F–M | left **−** rail, row 7 | — | common ground |
| W3 | left **+** rail, row 4 | M–M | a4 | Tap middle pin (+) | powers the tap switch's pull resistor |
| W4 | left **−** rail, row 5 | M–M | a5 | Tap `-` | tap ground |
| W5 | `27` | F–M | a3 | Tap `S` | tap signal → GPIO27 (input) |
| W6 | `26` | F–M | a10 | RGB pin printed `R` | colour channel 1 |
| W7 | `25` | F–M | a11 | RGB pin printed `G` | colour channel 2 |
| W8 | `33` | F–M | a12 | RGB pin printed `B` | colour channel 3 |
| W9 | left **−** rail, row 13 | M–M | a13 | RGB `-` | LED common cathode → ground |

Module placement:
- **Tap Module:** pins in **c3 (S), c4 (middle), c5 (−)**.
- **RGB module:** pins in **c10 (R), c11 (G), c12 (B), c13 (−)**.

```
 rail(+ −)   a    b    c         d  e   │gap│  f..j (unused)
 row 3          W5 ── ·  ── [Tap S]
 row 4  +──W3── ·     ·  ── [Tap +]
 row 5    −─W4─ ·     ·  ── [Tap −]
 row 7    −─W2 (from ESP32 GND)
 row10          W6 ── ·  ── [RGB R*]      * silkscreen may swap R/B — firmware maps it
 row11          W7 ── ·  ── [RGB G]
 row12          W8 ── ·  ── [RGB B*]
 row13    −─W9─ ·     ·  ── [RGB −]
 row14  +─W1 (from ESP32 3V3)
 ESP32 (out of the breadboard): F–M jumpers from 3V3, GND, 27, 26, 25, 33
```

### 3.5 Numbered wiring steps (USB **unplugged** throughout)
1. **Remove the ESP32 from the breadboard.** Lift it with gentle, alternating pressure at both short ends so the pins don't bend. Put it beside the breadboard, label side up.
2. Push the **Tap Module** pins into **c3, c4, c5**, with `S` in c3 and `-` in c5.
3. Push the **RGB module** pins into **c10–c13**, with printed `R` in c10 and `-` in c13.
4. Make W1 and W2. Slide the female ends onto the ESP32 pins `3V3` and `GND` (the GND next to `13`). Push the male ends into the left **+** rail and the left **−** rail at row 7 (W2). W1 goes into the **+** rail at row 14.
5. Make W3 and W4 with male–male jumpers: + rail → a4, and − rail → a5.
6. Make W5: `27` → a3.
7. Make W6–W8: `26` → a10, `25` → a11, `33` → a12.
8. Make W9: − rail → a13.
9. **Check before power** (the physical checks below), then plug in USB.
10. For pick-up runs, secure the ESP32 to the breadboard with tape or a rubber band, and later put both in a small cardboard box with the LED visible. Use a power bank with a cable that has some slack.

### 3.6 Checks — before power, then each module alone
**Before power (needs you, no tools):**
- **P1.** No wire touches the ESP32 `5V` pin. Every wire matches the table. No two module pins share a row.
- **P2.** Zoom a phone photo on the RGB module and confirm that resistors R1–R3 are present (code `151`). If one is missing, **stop**: that channel would be unprotected.
- **P3.** Confirm the Tap Module's middle pin is the unprinted one and that `-` is the outer pin, as on the board.

**After power, using `firmware/bench_probe` over serial at 115200:**
- **B1 — Port:**
  - `/dev/cu.usbserial-*` or `/dev/cu.SLAB_USBtoUART` appears (CP2102).
  - If it doesn't, try a data-capable cable. If it is still missing, you install the Silicon Labs VCP driver and approve it in System Settings. That is your action, because I won't change security settings.
- **B2 — Tap idle level:**
  - The probe prints the idle level with `INPUT`, then re-reads with the internal pull-up and then the pull-down.
  - If the level *follows* the internal pulls, the pin is floating, which means a wiring fault.
  - Record idle HIGH or LOW. This settles the datasheet-versus-code polarity conflict, and sets `TAP_ACTIVE_LEVEL`.
- **B3 — Taps:** 20 deliberate taps should give 20 counted events with no doubles. The probe also prints bounce duration, which tunes the lockout.
- **B4 — RGB:** commands `r`, `g` and `b` light one channel each. Record which *physical* colour each GPIO gives, to check the R/B swap. Then `c` runs the 7-colour cycle. Check it is visible from your bed distance in a dim room.
- **B5 — Idle stability:** with no touch for 60 s, there should be 0 edges. This is what "sensor ready" means for arming.

### 3.7 Not established by the photo or docs
- Whether the breadboard's rails are split mid-length, which is why everything stays in rows 1–14.
- The unreadable 2nd pin on the right header.
- The Tap Module's actual idle polarity (settled in B2).
- The RGB module's actual R/B mapping (settled in B4).
- Whether green and blue are bright enough at 3.3 V (settled in B4).
  - Fallback: run red-heavy colours, or slow the cycle with longer on-times. No hardware change is needed.
- Whether macOS 26 has a built-in CP2102 driver (settled in B1).
- Whether mDNS works on your hotspot (settled in Increment 2; the IP fallback is ready).

---

## 4. Prioritized implementation work

Each increment is demonstrable on its own. Arrows show the dependencies.

**Inc 0 — Adapt the prompts and docs to the RGB module** (your request)
- **Tasks:**
  - Replace the Seven-Color Flash Module with the **Elegoo RGB LED Module (Lesson 11)** in `context.md`, `initial_prompt.md`, `planning_prompt.md` (docs path → `code/Lesson 11 SMD RGB MODULE AND RGB MODULE/`), `README.md`, and the embedded prompt and context copies in `showcase.html`.
  - Rewrite the indicator semantics: the firmware runs a 7-colour cycle when enabled, and the colours still don't encode state (keeping QR-07). The electrical note becomes "3.3 V direct drive via onboard 150 Ω resistors; no 5 V switching".
- **Acceptance:**
  - `grep -niE "seven|7.color|flash module|lesson 21"` finds no stale references, apart from an intentional change note.
  - `showcase.html` still renders, which is checked in the browser.
- **Verify:** the grep, plus a Playwright MCP snapshot of `showcase.html`.

**Inc 1 — Toolchain and hardware bring-up** (foundation for US-02/04/05) → after Inc 0, or in parallel
- **Tasks:**
  - Install arduino-cli, core 3.3.12 and the libraries.
  - Write `bench_probe.ino` and wire per §3.
  - Run P1–P3 and B1–B5.
  - Record the results in `docs/test-log.md`.
- **Acceptance:**
  - Polarity, R/B mapping and lockout are recorded.
  - 20/20 taps are counted with no doubles.
  - The colour cycle is visible.
  - The idle period has 0 edges.
- **Verify:** compiles cleanly. **You confirm each hardware observation.**

**Inc 2 — Protocol contract and device firmware** (US-02, US-04, US-05 device side) → needs Inc 1
- **Tasks:**
  - Write `docs/protocol.md` covering `hello`, `status`, `activate`, `deactivate`, `ack`, `tap`, `sync` and error handling.
  - Write `tap_detector.h` and `rgb_cycle.h` with host tests.
  - Write `wakequest.ino`: Wi-Fi STA from `secrets.h` (you fill in the SSID and password yourself), mDNS, WS server, heartbeat, and a 15-minute safety timeout.
- **Acceptance:**
  - Host tests pass: a bounce burst gives 1 event, and the lockout and polarity behave.
  - A Python probe script gets `hello` with the device ID.
  - `activate` starts the cycle and `ack` comes back within 2 s.
  - `deactivate` stops the cycle.
  - A tap gives one `tap` message.
- **Verify:** clang++ tests and a scripted WS probe. **You confirm the LED and taps.**

**Inc 3 — Service core** (US-01, US-02, US-03, US-05, US-07, FR-09) → in parallel with Inc 2 once `protocol.md` is fixed
- **Tasks:**
  - `models`/`storage`: alarms with validation, atomic writes, JSONL events, CSV export.
  - `state.py`: a pure state machine covering idle/armed/ringing/completed/aborted, arming preconditions, tap rules and derived points.
  - `scheduler.py`: injected clock.
  - `device_link.py`: ws client with ping 1 s / timeout 2 s, status watchdog, reconnect with backoff, and IPv4 resolution.
  - `fake_device.py`: same protocol, scriptable faults.
  - REST + `/ws` snapshots, and test hooks enabled only with `WAKEQUEST_TEST_MODE=1`.
- **Acceptance:**
  - Past or invalid times are rejected with an understandable message.
  - "Connected" appears only after `hello`, and loss is detected in ≤ 5 s.
  - Arming is refused unless the device is connected, the sensor is stable and audio is tested.
  - Arming another alarm replaces the previous one.
  - Idle or armed taps, stale-session taps and duplicate taps never complete a session.
  - An abort gives no reward, and points are awarded only once.
  - Reconnect gives `sync` with no new reward.
  - Every FR-07 event is logged with a session ID and mode.
- **Verify:** `uv run pytest`, with mutation-style negative cases for every rule.

**Inc 4 — Website** (US-01 … US-08 UI) → needs Inc 3's API, and can start on a stub snapshot
- **Tasks:**
  - Setup view: alarm form and list, connect plus retry, enable and test sound, arm, and next alarm.
  - Ringing takeover.
  - Celebration.
  - Presenter panel: test in 10 s, stop run (aborted), reset.
  - Log table with a CSV download.
  - Error states for blocked audio, lost device and missing activation.
  - Wake Lock with re-request on visibility change.
- **Acceptance:**
  - All story criteria are visible as text.
  - There is no dismiss or snooze on the ringing screen.
  - The response time is labelled "Alarm response time".
  - Demo rewards are labelled as a preview.
  - Reduced motion is honoured.
- **Verify:** the Playwright e2e suite against the fake device, plus a Playwright MCP visual review.

**Inc 5 — Real-hardware integration and Stage A functional checks** (QR-01 … QR-04, QR-07) → needs Inc 2, 3 and 4
- **Tasks:**
  - Run end-to-end on the hotspot.
  - 10 scheduled runs.
  - 20 deliberate taps (filmed in slow motion).
  - 20 nuisance trials.
  - Idle and armed taps.
  - Power-off the board mid-run, then reconnect.
  - Tune the lockout.
- **Acceptance:** the QR thresholds in §5, recorded in `docs/test-log.md`.
- **Verify:** event-log CSV plus video. **You run and confirm.**

**Inc 6 — Classroom readiness** (US-08, QR-05, QR-06, DoD) → needs Inc 5
- **Tasks:**
  - Make the cardboard housing.
  - Run 5 consecutive rehearsals with reset ≤ 30 s.
  - Run the overnight rehearsal (`caffeinate`).
  - Write the usability script and run sessions with 5 classmates.
  - Prepare the evaluation sheet for Stage B.
- **Acceptance:** the DoD in `context.md`: all 8 stories are shown on real hardware, the tests have recorded outcomes, and there are 5 consecutive successful rehearsals.

How the increments add up to the Sprint Goal:
- Inc 1–2 make the *physical* interaction real.
- Inc 3 makes it *trustworthy*: no false completions, one reward, logged.
- Inc 4 makes it *understandable*.
- Inc 5–6 *prove* it on real hardware and in class.

**Backlog (later):**
- Stage B morning study.
- The four later user stories from the brief (README).
- Phone as the client.
- Repeating alarms.
- A better enclosure.
- An accelerometer for verified pickup.

---

## 5. Test design (no test has been run; all criteria are defined before implementation)

### A. Does the implementation work correctly?
| Layer | Tool | Cases | Hardware? |
|---|---|---|---|
| Firmware logic | clang++ host tests (`firmware/test`) | Bounce burst gives 1 event. Lockout boundary. Both polarities. Colour-sequence order and on/off. | No |
| Service | pytest + pytest-asyncio, fake clock, fake device | Validation (past, invalid, missing). Arm preconditions and replacement. Scheduler fires within 1 s of T (fake clock). Tap rules: idle, armed, stale, duplicate and post-completion taps are rejected. Abort gives 0 points. Derived points. Loss detected ≤ 5 s. Reconnect `sync` gives no re-award. Missing ack is flagged. Every FR-07 field is logged. CSV export. Atomic storage. | No |
| UI end-to-end | `@playwright/test` (Chromium, `--autoplay-policy=no-user-gesture-required`; a second project *without* the flag for the blocked-audio case), service in test mode with the fake device | Create alarm and error messages. Connect success and failure plus retry. Arm gating. Test alarm in 10 s shows the ringing screen within 2 s, with sound state exposed on `window` and no dismiss button. Simulated tap shows the celebration, 10 pts preview and response-time label. Duplicate tap gives no extra points. Abort shows no reward. Device drop shows the alert within 5 s, then reconnect restores the state. Reset ≤ 30 s. Reduced motion. Text status present for every state. | No |
| Hardware-in-loop | Manual, logged in `docs/test-log.md` | B1–B5 bench checks. QR-01: 10 runs, start ≤ 2 s from the scheduled time (event timestamps plus video). QR-02: ≥ 19/20 taps give stop and feedback ≤ 1 s (slow-mo video frame count). QR-03: 0 completions from idle or armed taps, and 20 nuisance trials recorded (reported, not pass/fail). QR-04: unplug power mid-run, alert ≤ 5 s, no duplicate reward. QR-06: reset ≤ 30 s. Overnight rehearsal: rings and dismisses after ≥ 8 h. | **Yes** |

### B. Do users understand the interaction? (H4, QR-05, H5)
- **Participants and setup:** 5 first-time classmates, a pre-configured device, and a short task introduction (`docs/usability-script.md`). There is no coaching.
- **Task:** connect, create an alarm (or use the test alarm), then walk, pick up and tap.
- **Evidence:**
  - Time to finish.
  - Mistakes and hesitations.
  - Requests for help.
  - A 1–5 rating for motivation and annoyance, plus a short reason.
- **Success:**
  - ≥ 4/5 finish within 2 min.
  - Motivation median ≥ 4/5 and annoyance ≤ 2/5 (exploratory).

### C. Are the behavioural hypotheses supported? (H1, H2; Stage B, after the sprint)
- **Design:** 12 mornings, 6 baseline and 6 WakeQuest, with a randomised order within each pair.
- **Measurement:** the same independent observation (a consented recording or an observer) of audible onset → standing upright. The tap time is logged separately and is **never** used as bed-exit time.
- **Decision rules:**
  - H1: ≥ 30% lower median, also reporting the absolute seconds and all non-completions.
  - H2: ≥ 5/6 prototype mornings without returning to bed within 10 min, compared with baseline.
- **Records:** `docs/evaluation-sheet.csv` uses participant code `P01`. Demo runs are excluded.

---

## 6. Open decisions

**Answered during clarification:**

| Decision | Answer |
|---|---|
| Photo | Provided. The board, both modules and the breadboard are identified (§3.1). |
| Indicator | **RGB LED module.** Adapt the earlier prompts (Inc 0). |
| Parts | Kit plus jumpers only. No transistor or multimeter. |
| Network | Phone hotspot (WPA2-Personal). |
| Stack | Python FastAPI + vanilla JS. |
| Jumpers | M–M, M–F and F–F are all available. |
| Board | Pushed into the breadboard, to be removed (§3.5 step 1). |

**Remaining, with recommended defaults (non-blocking):**

| Decision | Recommended default |
|---|---|
| Python version | Python 3.12 via uv (3.11 also works). |
| Browser | Desktop Chrome on the laptop. |
| Alarm sound | A synthesized Web Audio tone, so no audio asset or licence is needed. |
| Colour cycle | 7 colours at 400 ms each. Colours carry no meaning. |
| Tap lockout | 250 ms, tuned in B3. |
| Device hostname | `wakequest.local`, with the IP as fallback. |
| Service address | Service at `127.0.0.1:8000`, so it isn't exposed on the hotspot. |
| Safety timeout | The light switches off after 15 min. |
| Fonts | Self-hosted fonts. |
| Unknowns | Course deadline and power bank: unknown, and they don't affect Sprint 1 design. |

**Blocking — physical checks required before wiring or operation:**
- P1–P3 before the first power-up.
- B1: a serial port must appear before any upload.
- B2: tap polarity must be measured before `TAP_ACTIVE_LEVEL` is final.
- You create `firmware/wakequest/secrets.h` with your hotspot SSID and password. I won't handle the password.

---

## 7. Plan review

| Criterion | Assessment | Gaps → resolution |
|---|---|---|
| **Feasibility** | High. Every part is a kit part already on your desk. Everything is 3.3 V, so there is no electrical-conversion risk. The libraries are current and support core 3.x (verified releases). | Overnight browser audio isn't documented to work hours later. This is mitigated with server scheduling, a visible tab, Wake Lock and `caffeinate`, and is **verified only by the overnight rehearsal**. mDNS on the hotspot is unverified, and the IP fallback is built in. |
| **Simplicity** | One service, one tab, one device. No build step. JSON files instead of a database. Derived points. | Node exists only for Playwright, which is accepted because the prompt mandates `@playwright/test`. |
| **Clarity of hardware instructions** | Uses the printed labels read from your photo, gives a row-level breadboard table, wiring steps, and pre-power checks. | The R/B swap and polarity can't be known in advance, so they become explicit bench steps (B2, B4) instead of guesses. The rail split is avoided by design. |
| **Traceability** | Every increment names its US, FR and QR. Every QR has a measurable check in §5. | The DoD maps to Inc 6. |
| **Testability** | Pure logic is isolated: the firmware headers and `state.py`. The clock is injected. The fake device has fault injection. There are test-mode hooks. | Physical latency (QR-02) needs slow-motion video, because software timestamps can't see the physical tap. That is stated, not glossed over. |

**Unverified and stated honestly:**
- CP2102 driver presence on macOS 26.
- Green and blue brightness at 3.3 V.
- Hotspot mDNS.
- Hours-later audio start.
- Whether the breadboard rails are split.
- The middle tap pin = "+" (from the datasheet, not printed).

---

## 8. Implementation workflow in Claude Code (applies after approval)

### Sequencing
- **Sequential first:** save `PLAN.md` and `CLAUDE.md`, then do Inc 0 (Sonnet subagent), then the toolchain install (needs your OK for `brew install`), then `docs/protocol.md` (main model, contract-critical).
- **Then in parallel:**
  - Track A: firmware (Inc 1 → 2), which pauses at each hardware check for you.
  - Track B: the service (Inc 3).
  - Track C: the website (Inc 4), against the stub snapshot and then the real API.
- **Sequential last:** Inc 5 → Inc 6. These need you at the hardware.

### Delegation
| Model | Work |
|---|---|
| `model: "sonnet"` subagents (well-specified) | Inc 0 doc and prompt edits. Firmware host tests. Storage, validation and CSV with tests. `fake_device.py`. Playwright specs written from §5's case list. `bench_probe.ino`. Usability script and evaluation sheet. |
| Default (Opus) | `protocol.md`. `state.py` plus tap-acceptance rules. `device_link.py` reconnect/heartbeat. The main firmware. Electrical review. The UI design pass using `frontend-design:frontend-design`. |

You can **switch this session to Sonnet 5.5** for Inc 4 styling iterations and Inc 0. Keep Opus for Inc 2 and Inc 3 and for any debugging of hardware or timing behaviour.

Before each component, the implementation checks current official docs with WebSearch/WebFetch (Espressif, arduino-cli, FastAPI, websockets, Playwright, MDN). Each increment is graded against the §7 rubric before it is presented. Untested work counts as unfinished. **Hardware checks are never marked passed without your confirmation.**

### Exact commands
```bash
# toolchain (once; needs your approval)
brew install arduino-cli
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32@3.3.12
arduino-cli lib install "WebSockets@2.7.2" "ArduinoJson@7.4.3"
# firmware
ls /dev/cu.*                                    # expect /dev/cu.usbserial-* (CP2102)
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/bench_probe
arduino-cli upload  --fqbn esp32:esp32:esp32 -p /dev/cu.usbserial-XXXX firmware/bench_probe   # hold BOOT if stuck at "Connecting..."
arduino-cli monitor -p /dev/cu.usbserial-XXXX --config 115200
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/wakequest
clang++ -std=c++17 -Wall -Wextra -I firmware/wakequest firmware/test/test_logic.cpp -o build/test_logic && ./build/test_logic
# service
uv sync
uv run pytest
uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000            # real device
WAKEQUEST_DEVICE=fake WAKEQUEST_TEST_MODE=1 uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000
WAKEQUEST_DEVICE_HOST=192.168.x.y uv run uvicorn wakequest.main:app --port 8000   # mDNS fallback
# e2e
npm ci && npx playwright install chromium
npx playwright test
# overnight
caffeinate -dimsu -w $(pgrep -f "uvicorn wakequest.main")
```

### Checks that need you at the hardware
- P1–P3.
- B1–B5.
- Firmware upload (port and BOOT button).
- LED and tap confirmations in Inc 2.
- All of Inc 5, which includes slow-motion video.
- The overnight rehearsal.
- 5 classroom rehearsals.
- The 5 usability sessions.
- Filling in `secrets.h`.

### Proposed `CLAUDE.md`
```markdown
# WakeQuest — working rules

Prototype: website alarm stopped only by tapping an ESP32 controller across the room. Plan of record: PLAN.md.

## Hardware facts (verified on bench — update docs/test-log.md when they change)
- Board: 38-pin DevKitC-style, ESP32-WROOM-32D, CP2102 USB. 3.3 V logic; GPIO NOT 5 V tolerant. Never use the 5V pin.
- Tap Module: S→GPIO27, middle(+)→3V3, −→GND. Active level: <TAP_ACTIVE_LEVEL from bench B2>.
- RGB LED module (Lesson 11, 150 Ω onboard): GPIO26/25/33 → printed R/G/B (silkscreen may swap R/B), −→GND.
- Elegoo bundle folder is gitignored reference only; never reference it from committed code.

## Commands
- Firmware: `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/wakequest`; upload with `-p /dev/cu.usbserial-*`; monitor at 115200.
- Firmware logic tests: `clang++ -std=c++17 -I firmware/wakequest firmware/test/test_logic.cpp -o build/test_logic && ./build/test_logic`
- Service: `uv sync`, `uv run pytest`, `uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000`
- Fake device: `WAKEQUEST_DEVICE=fake WAKEQUEST_TEST_MODE=1` before the uvicorn command.
- E2E: `npx playwright test` (Chromium).

## Rules
- Service is the single source of truth; points are derived, never incremented.
- Iterate until each increment meets its acceptance criteria; when you announce a next step, do it.
- Check current official docs (WebSearch/WebFetch) before implementing a component; tell subagents to do the same.
- Parallelize independent work with Agent subagents; delegate well-specified tasks with model: "sonnet".
- UI work uses frontend-design:frontend-design; UI tests use @playwright/test + Playwright MCP.
- Untested work is unfinished. Never claim a hardware check passed without the user's confirmation from the physical board.
- Grade each increment against the PLAN.md §7 rubric before presenting it.
- Never handle Wi-Fi passwords: firmware/wakequest/secrets.h is user-created and gitignored.
- Do not commit data/, secrets.h, node_modules/, .venv/, test-results/.
```

### Sources relied on
- Espressif ESP32-WROOM-32D datasheet (electrical levels, current): https://documentation.espressif.com/esp32-wroom-32d_esp32-wroom-32u_datasheet_en.pdf
- ESP-IDF GPIO (strapping, input-only, flash pins): https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html
- DevKitC V4 user guide (pin headers, power): https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html
- Arduino-ESP32 install and 2.x→3.0 migration: https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html · https://docs.espressif.com/projects/arduino-esp32/en/latest/migration_guides/2.x_to_3.0.html · releases https://github.com/espressif/arduino-esp32/releases
- arduino-cli: https://arduino.github.io/arduino-cli/1.5/getting-started/
- arduinoWebSockets: https://github.com/Links2004/arduinoWebSockets · ArduinoJson: https://github.com/bblanchon/ArduinoJson/releases
- FastAPI (lifespan, WebSockets, testing): https://fastapi.tiangolo.com/advanced/events/ · https://fastapi.tiangolo.com/advanced/websockets/ · https://fastapi.tiangolo.com/advanced/testing-websockets/
- websockets client and keepalive: https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html · https://websockets.readthedocs.io/en/stable/topics/keepalive.html
- uv projects: https://docs.astral.sh/uv/guides/projects/
- Playwright webServer and clock: https://playwright.dev/docs/test-webserver · https://playwright.dev/docs/clock
- MDN autoplay, Web Audio, Wake Lock, aria-live, reduced motion: https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay · https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API · https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- Chrome timer throttling: https://developer.chrome.com/blog/timer-throttling-in-chrome-88
- Elegoo kit docs (local, gitignored): Hit Sensor datasheet, RGB LED Module datasheet, and tutorial Lessons 5 and 11.
