# WakeQuest — working rules

Prototype: website alarm stopped only by tapping an ESP32 controller across the room. Plan of record: `Prompts/Phase 3 - Implementation Planning /PLAN.md` (folder name ends in a space). Requirements: docs/requirements.md. Process overview: Prompts/Visualisations/showcase.html.

## Hardware facts (from photo + datasheets; bench-confirm and record in docs/test-log.md)
- Board: 38-pin DevKitC-style, ESP32-WROOM-32D, CP2102 USB. 3.3 V logic; GPIO NOT 5 V tolerant. Never use the 5V pin.
- Tap Module: S→GPIO27, middle(+)→3V3, −→GND. Active level: LOW (idle HIGH via the module's 10 kΩ pull-up; bench B2/B3, 2026-10-29). Lockout 250 ms (max burst seen 21 ms).
- RGB LED module (Lesson 11, 150 Ω onboard): GPIO26/25/33 → R/G/B, printed labels confirmed correct on this module (B4), −→GND.
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
