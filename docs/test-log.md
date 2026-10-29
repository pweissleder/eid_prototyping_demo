# WakeQuest test log

Record every hardware observation here with the date and who confirmed it. A check counts as passed only when the person at the board has confirmed it. Plan of record: `PLAN.md` §3.6 and §5.

## Bench bring-up (Inc 1)

Board: 38-pin DevKitC-style, ESP32-WROOM-32D, CP2102. Firmware: `firmware/bench_probe`.

| Check | What to do | Expected | Result | Date / confirmed by |
|---|---|---|---|---|
| P1 | Before USB: compare every wire with PLAN.md §3.4; nothing on `5V`; no two module pins share a row | All match | Confirmed before first power-up; RGB wires later corrected (see Notes) | 2026-10-29 / user |
| P2 | Zoom a photo on the RGB module: resistors R1–R3 present, code `151` | 3 resistors | Confirmed with the pre-power checklist | 2026-10-29 / user |
| P3 | Tap Module: `S` and `-` are the outer pins, middle pin unprinted | As described | Confirmed with the pre-power checklist | 2026-10-29 / user |
| B1 | Plug in USB, run `ls /dev/cu.*` | `/dev/cu.usbserial-*` or `/dev/cu.SLAB_USBtoUART` | Port: `/dev/cu.usbserial-0001` (built-in macOS CP210x driver, no install needed); upload + hash verify OK | 2026-10-29 / serial reading (Claude) |
| B2 | Probe output after boot (or `p`) | Not floating; idle level printed | `INPUT` HIGH, `INPUT_PULLUP` HIGH, `INPUT_PULLDOWN` HIGH → module's own pull-up holds idle HIGH, not floating → `TAP_ACTIVE_LEVEL` = LOW (matches Lesson 5 sample code, not the datasheet prose). Tap direction still to be seen in B3. | 2026-10-29 / serial reading (Claude) |
| B3 | 20 deliberate taps, then `s` | 20 accepted, no doubles | 20 TAP lines, 20 accepted, 0 rejected; every burst starts HIGH→LOW (tap = LOW confirmed); longest burst 21.2 ms (2–24 edges); shortest gap between taps ≈ 680 ms; lockout 250 ms kept | 2026-10-29 / user tapped, serial log read by Claude |
| B4 | `r`, `g`, `b` one at a time | One color each | GPIO26 → red · GPIO25 → green · GPIO33 → blue (printed labels correct on this module, no R/B swap) | 2026-10-29 / user |
| B4 | `c` from bed distance in a dim room | Cycle clearly visible | Visible: yes, clearly visible from bed in a dim room | 2026-10-29 / user |
| B5 | `i`, don't touch for 60 s | 0 edges | 0 edges in 60 s | 2026-10-29 / serial reading (Claude); user ran it |

## Wi-Fi firmware (Inc 2)

Firmware: `firmware/wakequest` 0.1.0, deviceId `wq-a396d0`, phone hotspot "Pascal's iPhone" (Maximize Compatibility on).

| Check | Expected | Result | Date / confirmed by |
|---|---|---|---|
| Joins hotspot | IP printed on serial | `172.20.10.2`, RSSI −15…−21 dBm, mDNS `wakequest.local` advertised | 2026-10-29 / serial log read by Claude |
| Name resolution from Mac | `wakequest.local` → board IP | `dns-sd`/`dscacheutil` resolve it; Python `getaddrinfo` does **not** on this Mac → service falls back to `dns-sd -G v4` (≈15 ms) | 2026-10-29 / Claude |
| Protocol probe (`device_probe.py`) | hello, acks, 1 s status | hello on connect; sync/activate/deactivate acked within the same second; status every 1.00 s; `indicator` true while active, false after deactivate | 2026-10-29 / probe output read by Claude |
| LED cycles on activate, stops on deactivate | Starts at red, 7 colours, off after deactivate | Cycled from activation, went dark after deactivate | 2026-10-29 / user |
| Taps sent while active | One `tap` message per tap | 5 taps → 5 `tap` messages, seq 19–23 consecutive, all with the active sessionId | 2026-10-29 / user tapped 5×, probe output read by Claude |
| Taps while no client connected | `tapSeq` grows, nothing sent | Hello showed `tapSeq: 5` with no tap messages before connecting (consistent); later hello showed `tapSeq: 18` after taps during a failed connection attempt, none sent | 2026-10-29 / serial + probe output read by Claude |

## End-to-end with real hardware (Inc 5, first run)

Setup: service against the real board (`wakequest.local` via the `dns-sd` fallback), website in the app's browser pane, phone hotspot.

| Run | What happened (service log, laptop clock) | Result | Date / confirmed by |
|---|---|---|---|
| Test alarm `s-20261029-230132-d58e` (demo) | Ring started 2 ms after its scheduled time; sound confirmed +5 ms; device light acknowledged +29 ms; tap accepted 4.9 s after the sound started; completed with 10 preview points; 0 ignored taps during the run; no technical issues | Sound, LED cycle, tap stop and reward all worked | 2026-10-29 / user ("all great"), log read by Claude |
| Before the run | 3 taps while idle (handling the device) were logged as ignored with reason `idle` and completed nothing (US-05) | As designed | 2026-10-29 / service log |

Still open for Stage A: 10 scheduled runs (QR-01), 20 deliberate taps with slow-motion video for the 1 s target (QR-02), 20 nuisance trials (QR-03), power-off during a run (QR-04), 5 timed resets (QR-06), overnight rehearsal, 5 classmate sessions (QR-05).

Known small issue: with two tabs of the site open, the setup headline can say "Everything is ready" because of the other tab's sound test, while step 2 in this tab says "Not tested". Each tab keeps its own sound test by design; only the headline wording mixes them.

## Notes

- 2026-10-29: the RGB channels did not light correctly at first; the user corrected the RGB wiring (tap wiring untouched), after which B4 passed. B3 and B5 were run on the unchanged tap wiring.
- Firmware constants from bring-up: `TAP_ACTIVE_LEVEL = LOW`, lockout 250 ms, RGB map GPIO26/25/33 = R/G/B.
- 2026-10-29: from the app's Terminal panel, connecting to 172.20.10.2 failed with "No route to host" while the same connection from Claude's shell succeeded. This matches macOS Local Network privacy denying that app; run the service from a context that has Local Network permission (e.g. Terminal.app, which asks once) or let Claude start it.
