# WakeQuest — Hardware and Software Prototyping Demo

University course project · 30 October 2026

WakeQuest is an alarm that you can only stop by getting out of bed. A website plays the alarm. You stop it by walking across the room, picking up an ESP32 controller and tapping it. While the alarm rings, the controller's RGB LED cycles through seven colours. An accepted tap stops the sound, switches the LED off and shows a reward on the website: "Morning quest completed!", 10 points and your alarm response time.

**Main research question:** Do I get out of bed faster with this prototype than with my usual bedside alarm?

## The demo process: start with the showcase

The whole process from idea to evidence is shown in **[`Prompts/Visualisations/showcase.html`](Prompts/Visualisations/showcase.html)**. Open it in a browser:

```bash
open Prompts/Visualisations/showcase.html
```

It walks through each stage of the course process and the material produced at each one:

| Stage in the showcase | What it shows | Source files |
|---|---|---|
| 01 The initial input | The first prompt that started the project | [`Prompts/Phase 1 - Idea/initial_prompt.md`](Prompts/Phase%201%20-%20Idea/initial_prompt.md) |
| 02–04 Problem → scenarios → requirements → user stories | The problem description, scenarios, functional and quality requirements, and prioritised user stories | [`Prompts/Phase 2 - Requirements Engineering/context.md`](Prompts/Phase%202%20-%20Requirements%20Engineering/context.md), full brief in [`docs/requirements.md`](docs/requirements.md) |
| 05–06 Planning → implementation | The sprint plan: architecture, hardware setup, increments, test design | [`planning_prompt.md`](Prompts/Phase%203%20-%20Implementation%20Planning%20/planning_prompt.md), [`PLAN.md`](Prompts/Phase%203%20-%20Implementation%20Planning%20/PLAN.md) |
| 07 Hypotheses → testing → re-evaluation | How the hypotheses are tested and the idea re-evaluated | [`docs/requirements.md` §8](docs/requirements.md#8-hypotheses-and-evaluation), [`docs/test-log.md`](docs/test-log.md) |
| 08 The next iteration | How another team can reuse the same process | — |

The companion page [`Prompts/Visualisations/test_protocol.html`](Prompts/Visualisations/test_protocol.html) is a fillable test protocol for the Stage A functional and classroom tests and the Stage B morning comparison.

## Current status

| Part | Status |
|---|---|
| Hardware bring-up (wiring, tap polarity, debounce, RGB mapping) | Done, confirmed on the bench; see [`docs/test-log.md`](docs/test-log.md) |
| Firmware (Wi-Fi, mDNS, WebSocket, tap detection, LED cycle) | Done, checked with the device probe |
| Local service (scheduler, session state machine, device link, event log) | Done, covered by pytest |
| Website (setup, alarms, ringing screen, reward, presenter controls, log) | Done, covered by the Playwright suite |
| First end-to-end run on real hardware | Done (test alarm: sound, LED, tap and reward all worked) |
| Stage A test series, classmate sessions, overnight rehearsal, Stage B mornings | Still open; listed at the end of [`docs/test-log.md`](docs/test-log.md) |

## How it works

```
 Browser (web/)  ⇄  local service (wakequest/, FastAPI)  ⇄  ESP32 controller (firmware/wakequest/)
   renders state      single source of truth:               WebSocket server on :81, wakequest.local
   plays the sound    alarms, scheduler, state machine,      sends tap events, runs the LED cycle
                      tap validation, points, event log      only while told to
```

- **Website:** plain HTML/CSS/JS served by the service, no build step. It renders the state snapshots that the service pushes over `/ws` and holds no business logic.
- **Local service:** Python 3.12 with FastAPI and uvicorn. It schedules alarms, decides when a tap counts, derives points from completed study runs (they are never incremented), and writes `data/alarms.json` and `data/events.jsonl`. It connects to the controller as a WebSocket client.
- **Firmware:** Arduino-ESP32. It joins a 2.4 GHz Wi-Fi network (a phone hotspot), advertises `wakequest.local`, debounces the tap input (250 ms lockout) and runs the colour cycle while an alarm rings. As a safety measure, the LED switches off after 15 minutes.

The REST/WebSocket contract is in [`docs/api.md`](docs/api.md), and the device protocol is in [`docs/protocol.md`](docs/protocol.md).

## Hardware

| Part | Details |
|---|---|
| Board | 38-pin DevKitC-style board with an ESP32-WROOM-32D and a CP2102 USB chip. 3.3 V logic; the GPIOs are **not** 5 V tolerant. Never use the `5V` pin. |
| Tap Module (Elegoo Lesson 5) | `S` → GPIO27, middle pin (+) → `3V3`, `−` → `GND`. Idle HIGH through the module's pull-up; a tap reads LOW. |
| RGB LED Module (Elegoo Lesson 11, through-hole, 150 Ω onboard) | `R` → GPIO26, `G` → GPIO25, `B` → GPIO33, `−` → `GND`. The printed labels are correct on this module. |
| Power | USB cable or power bank with some slack, so the controller can be picked up. |

Wiring diagram: [`docs/wiring.svg`](docs/wiring.svg). Breadboard positions, step-by-step wiring and the checks before first power-up are in [`PLAN.md` §3](Prompts/Phase%203%20-%20Implementation%20Planning%20/PLAN.md#3-intuitive-hardware-setup).

## Setup (once)

You need macOS (the commands below use Homebrew; Linux works with the equivalent packages), [uv](https://docs.astral.sh/uv/), Node.js 20 or newer, and a 2.4 GHz Wi-Fi network that both the laptop and the ESP32 can join. A phone hotspot works; on an iPhone, turn on "Maximize Compatibility".

### 1. Service and website

```bash
uv sync
```

This installs Python 3.12 (from `.python-version`) and the service dependencies into `.venv/`.

### 2. End-to-end tests (optional)

```bash
npm ci
```

```bash
npx playwright install chromium
```

### 3. Firmware toolchain

```bash
brew install arduino-cli
```

```bash
arduino-cli config init
```

```bash
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

```bash
arduino-cli core update-index
```

```bash
arduino-cli core install esp32:esp32@3.3.12
```

```bash
arduino-cli lib install "WebSockets@2.7.2" "ArduinoJson@7.4.3"
```

### 4. Wi-Fi credentials

Copy the example file and enter your hotspot's name and password in `secrets.h`:

```bash
cp firmware/wakequest/secrets.example.h firmware/wakequest/secrets.h
```

`secrets.h` is gitignored. Never commit it.

### 5. Flash the controller

Plug in the board and find its port (`/dev/cu.usbserial-*`; the CP2102 driver is built into macOS):

```bash
ls /dev/cu.*
```

Compile and upload (replace the port). If the upload hangs at "Connecting...", hold the board's BOOT button.

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/wakequest
```

```bash
arduino-cli upload --fqbn esp32:esp32:esp32 -p /dev/cu.usbserial-0001 firmware/wakequest
```

Watch the serial output. After the board joins the network, it prints its IP address:

```bash
arduino-cli monitor -p /dev/cu.usbserial-0001 --config 115200
```

For first bring-up without Wi-Fi, `firmware/bench_probe` prints the tap level and edges and lights single colours on serial commands.

## Startup

### Option A — without hardware (fake device)

The service starts a simulated controller in-process, so the full flow runs on a laptop alone:

```bash
WAKEQUEST_DEVICE=fake WAKEQUEST_TEST_MODE=1 uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Test mode adds the `/api/_test/*` hooks. With them, you can send a fake tap while the alarm rings:

```bash
curl -X POST http://127.0.0.1:8000/api/_test/fake-device/tap -H 'Content-Type: application/json' -d '{}'
```

The other hooks (drop the connection, power off, withhold acks) are listed in [`docs/api.md`](docs/api.md#test-hooks-wakequest_test_mode1-only-for-the-playwright-suite).

### Option B — with the real controller

1. Turn on the hotspot and connect the laptop to it.
2. Power the controller (USB or power bank). It joins the hotspot and advertises `wakequest.local`.
3. Start the service:

```bash
uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000
```

4. Open <http://127.0.0.1:8000>.

If `wakequest.local` does not resolve, use the IP address the board printed on serial:

```bash
WAKEQUEST_DEVICE_HOST=172.20.10.2 uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000
```

On macOS, the app that starts the service needs **Local Network** permission (System Settings → Privacy & Security → Local Network). Without it, the connection to the board fails with "No route to host". Terminal.app asks for this permission the first time.

For an overnight run, keep the laptop awake while the service runs:

```bash
caffeinate -dimsu -w $(pgrep -f "uvicorn wakequest.main")
```

### Running a demo in the website

1. **Connect your device:** wait until the status shows "Connected" with the device ID.
2. **Enable and test sound:** the browser only plays audio after a click.
3. **Arm an alarm:** create a named alarm for a future time and arm it, or use **Presenter → Test alarm in 10 seconds** for a classroom run.
4. When it rings, the LED cycles and the page says "Pick up your device and tap it". Tap the controller.
5. The page shows "Morning quest completed!" and the alarm response time. Test alarms are labelled as demo runs and do not change the study points.
6. **Reset demo** prepares the next run. **Stop run (aborted)** ends a run without a reward. The run log can be downloaded as CSV (all runs, study runs only, or all events).

Runtime data is written to `data/` (gitignored). Set `WAKEQUEST_DATA_DIR` to use a different folder; all variables are listed in [`docs/api.md`](docs/api.md#configuration-environment-variables).

## Tests

| What | Command |
|---|---|
| Service unit and integration tests | `uv run pytest` |
| Website end-to-end (Chromium, fake device; starts its own service on port 8765) | `npx playwright test` |
| Firmware logic on the host (tap detector, colour cycle, session rules) | `clang++ -std=c++17 -I firmware/wakequest firmware/test/test_logic.cpp -o build/test_logic && ./build/test_logic` |
| Firmware compiles | `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/wakequest` |
| Real controller, without the service (stop the service first) | `uv run --no-project --with websockets python firmware/tools/device_probe.py ws://wakequest.local:81/` |

Hardware results only count once the person at the board has confirmed them. They are recorded with date and name in [`docs/test-log.md`](docs/test-log.md).

## Repository layout

```
README.md                   this file
CLAUDE.md                   working rules for Claude Code
docs/
  requirements.md           requirements and evaluation brief (problem, FR/QR, user stories, hypotheses)
  api.md                    service REST + WebSocket contract
  protocol.md               service ↔ device WebSocket protocol
  test-log.md               bench, firmware and end-to-end results
  wiring.svg                wiring diagram
Prompts/                    the process material, phase by phase
  Phase 1 - Idea/                         initial_prompt.md
  Phase 2 - Requirements Engineering/     context.md
  Phase 3 - Implementation Planning /     planning_prompt.md, PLAN.md
  Visualisations/                         showcase.html, test_protocol.html
firmware/
  wakequest/                main firmware (config.h has the pins; secrets.h is yours)
  bench_probe/              no-Wi-Fi bring-up sketch
  test/test_logic.cpp       host unit tests for the firmware logic
  tools/device_probe.py     talks to the controller directly
wakequest/                  Python service (FastAPI)
web/                        website served by the service
tests/                      pytest suite
e2e/                        Playwright suite (one spec per user story, plus audio and accessibility)
```

The Elegoo kit tutorial folder is a local, gitignored reference.

## Known limitations

- The tap switch detects a knock, not lifting or standing. A tap while the controller stays on the shelf also stops the alarm, and a strong bump can too. Whether you actually got up is recorded separately ([`docs/requirements.md` §8](docs/requirements.md#8-hypotheses-and-evaluation)).
- The laptop, service and browser tab must stay running and awake. Closing the page, host sleep and power loss are outside the first sprint.
- One user, one board, one connected browser session; one-off alarms only.

## License

[MIT](LICENSE)
