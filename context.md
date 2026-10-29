# WakeQuest — Prototype Context

Context for the simulated initial prompt. Extracted from the project brief: sections 1–6 and 8, with section 2 ending after the component table and the two test stages shortened. Section numbers are retained from the original brief.

This describes a planned university-course prototype using an ESP32-WROOM-32D, the Elegoo Tap Module, and the Seven-Color Flash Module. Acceptance thresholds are proposed targets, not measured results.

## 1. Problem description

I often struggle to get out of bed when my morning alarm rings. Because I can dismiss or snooze an alarm beside my bed, stopping the sound does not necessarily mean that I have stood up or started my day.

I want to explore whether placing an alarm controller across the room, and asking me to pick it up and tap it to stop the alarm, helps me get out of bed sooner. A website will let me set an alarm, check the hardware connection, and receive a small game-like reward after completing the physical interaction.

**Main research question:** Do I get out of bed faster with this prototype than with my usual bedside alarm?

**Secondary questions:** Is the tap interaction reliable and understandable? Do I stay out of bed afterwards? Does the success feedback feel motivating?

The intended user is initially me: a student who struggles with dismissing morning alarms and going back to sleep. Classmates can help evaluate usability and the classroom demonstration.

## 2. Concept and prototype boundary

The prototype uses an ESP32-WROOM-32D with the Elegoo kit's Tap Module (also called Hit Sensor / knock switch) and Seven-Color Flash Module, connected to a website. The website plays the alarm sound. While it rings, the flash module is enabled and cycles through its colors. The intended action is to get out of bed, pick up the controller across the room, and give it a deliberate tap. An accepted tap stops the sound, switches off the flash module, and triggers the website reward.

| Part | Role in the prototype |
| --- | --- |
| Development board carrying an ESP32-WROOM-32D | Connects over Wi-Fi, reads the tap input, and controls the indicator. The WROOM module has no integrated accelerometer. |
| Elegoo Tap Module / Hit Sensor, Lesson 5 | Supplies a digital knock/tap event. This is the initial-sprint input; no separate accelerometer is assumed. |
| Elegoo Seven-Color Flash Module, Lesson 21 | Automatically cycles colors when enabled during an active alarm. Software controls on/off, not individual colors. |
| Appropriate interface components and jumper wires | Adapt the kit's Arduino-oriented examples to 3.3 V ESP32 GPIO, including level conversion or switching where needed. |
| Simple hand-held housing or secured mounting | Keeps the board and modules together while the user picks up and taps the device. |
| USB power or a suitable power bank | Powers the development board while allowing the intended movement. |
| Website on a laptop or phone | Creates alarms, shows connection and alarm status, plays sound, and displays rewards. |
| Local service on the laptop | Holds the demo schedule, coordinates website/device messages, and records outcomes. |

## 3. Scenario

**The night before:** I place the ESP32-WROOM-32D controller with its Tap Module and Seven-Color Flash Module on a shelf across the room, beyond reach from bed. I open the website, connect the board, enable and test the sound, and create an alarm for 07:00 tomorrow. The website confirms readiness and the alarm time. The flash module remains off while the alarm is only scheduled.

**In the morning:** At 07:00, the website plays the alarm and the flash module starts its automatic color sequence. The screen says, “Pick up your device and tap it to stop the alarm.” I get out of bed, walk across the room, pick up the controller, and tap it. The accepted tap stops the sound and flash module. The website shows “Morning quest completed!” with a short animation, 10 points, and my alarm response time.

**Afterwards:** I can see that the interaction was completed. Whether I actually stood up and whether I returned to bed are recorded separately; the tap switch cannot establish either outcome.

**Classroom version:** I explain the morning problem, place the controller away from my chair, and use “Test alarm in 10 seconds.” I stand, pick up and tap the controller, and show the indicator and website response.

## 4. Requirements

All requirements in this section belong to the initial sprint.

### Functional requirements

| ID | Requirement |
| --- | --- |
| FR-01 | The user can create and view one-off alarms with a name and a future local date/time. Invalid or past times produce an understandable error. |
| FR-02 | The user can connect the website to one configured ESP32-WROOM-32D development board and see connecting, connected, or disconnected status based on communication with the board. |
| FR-03 | The user can arm an alarm after the board responds, the sensor is ready, and browser audio has been enabled and tested. The next scheduled alarm is clearly displayed. |
| FR-04 | At the scheduled time, the system starts the audible alarm, enables the Seven-Color Flash Module, and shows an active-alarm screen. |
| FR-05 | While an alarm is active, a validated tap event from the connected device completes that alarm, stops the sound, and disables the flash module. Movement before activation cannot complete a future alarm. |
| FR-06 | A completed alarm produces one success message, one short celebration, 10 points, and a clearly labeled alarm response time. Duplicate messages cannot award points twice. |
| FR-07 | The system records the alarm session, mode, scheduled time, sound-start confirmation, activation acknowledgment, tap receipt, completion or abort, and connection failures. |
| FR-08 | A presenter can run a labeled test alarm, reset the demonstration, and stop a run manually. A manual stop is recorded as aborted and earns no reward. |
| FR-09 | Connection loss or missing activation acknowledgment is visibly reported. It must never be presented as successful completion. Reconnection restores the current session state without repeating a reward. |

### Quality and interaction requirements

| ID | Proposed acceptance target |
| --- | --- |
| QR-01 | On the tested, awake setup, audible alarm and flash-module activation occur within 2 seconds of the scheduled time. Check 10 scheduled runs. |
| QR-02 | In at least 19 of 20 deliberate tap trials, the sound stops, the flash module is disabled, and website feedback appears within 1 second of the physical tap. |
| QR-03 | No tap completes an idle or merely armed alarm. During 20 active-alarm nuisance trials, record incidental dismissals from nearby movement, surface taps, and vibrations. A knock switch cannot identify the cause of an impact; report this limitation rather than claim verified pickup. |
| QR-04 | The website reports a lost connection within 5 seconds. State and points remain consistent after duplicate messages and reconnection. |
| QR-05 | At least 4 of 5 first-time testers can connect a preconfigured device, create an alarm, and complete it within 2 minutes without coaching after a short task introduction. |
| QR-06 | A presenter can reset and prepare another classroom run in at most 30 seconds. |
| QR-07 | The interface communicates state through text as well as color and animation. The flash module is enabled only while ringing and disabled otherwise. Its internal color and blink phases do not encode separate states. |

## 5. Constraints and assumptions

- **Small local prototype:** One user, one board, one local service, and one connected browser session. Use a prepared local Wi-Fi network; internet access is not required. ESP32 station mode supports connection to an access point. See [Espressif's Wi-Fi documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_wifi.html).
- **Prepared connection:** Wi-Fi credentials can be configured before the demo. “Connect device” means connecting to and verifying the configured board; automatic device discovery and a full onboarding flow are outside the first sprint.
- **Browser sound:** Include an explicit “Enable and test sound” action and handle playback errors. Browser autoplay restrictions can prevent sound without prior interaction. See [MDN's autoplay guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay).
- **Awake host:** The laptop/service and browser must remain running and awake during the experiment. Closing the page, host sleep, power loss, and offline scheduling are outside the supported first-sprint conditions. Overnight use must first pass an overnight technical rehearsal on the chosen setup.
- **Scheduling:** Use one-off alarms in the host's displayed local timezone. Allow multiple saved alarms but only one armed alarm at a time; arming another clearly replaces the previous selection. Repeating schedules, timezone changes, and daylight-saving transitions are outside the initial scope.
- **Physical placement:** Put the device beyond reach from bed, at the same location for all prototype study mornings. The user must be able to lift it without pulling loose wires or a short power cable. The system cannot enforce placement or prevent deliberate bypassing.
- **Tap input:** The Tap Module is a digital switch, not an accelerometer. Verify its idle/active polarity and debounce it so one physical tap produces at most one accepted event. The supplied prose and sample code disagree about polarity, so confirm the actual module on the bench. It cannot verify pickup or distinguish a deliberate tap from every accidental impact.
- **Electrical compatibility:** The kit tutorial targets 5 V UNO/Mega boards; ESP32 GPIO uses 3.3 V logic. Do not connect a 5 V module output directly to an ESP32 input. For the tap input, establish a verified 3.3 V-compatible contact/pull-up arrangement or use suitable level conversion. For the flash module, the tutorial uses 5 V: provide appropriate power and 3.3 V-controlled transistor/MOSFET switching as needed, with a common ground. Verify the actual module pin labels, current limiting, and load before wiring; do not copy Arduino D3/D13 assignments blindly. See the [Espressif electrical specifications](https://documentation.espressif.com/esp32-wroom-32d_esp32-wroom-32u_datasheet_en.html).
- **Indicator scope:** Only enable/disable the automatic seven-color sequence. Fixed colors, RGB channels, and color-specific status meanings are outside this module's capabilities.
- **Power and construction:** Use readily available low-voltage components and a secured breadboard or simple enclosure. Custom PCB development, battery charging circuitry, and a manufactured enclosure are outside scope.
- **Session persistence:** Keep the initial alarm list and event log locally for the demo/study. Automatic restart recovery, accounts, cloud synchronization, multiple users, and production deployment are outside scope.
- **Research data:** Store only the timestamps, outcomes, and brief ratings needed for evaluation. Any observation or recording requires the participant's consent; retained logs use participant codes.
- **Fixed parts:** Use an ESP32-WROOM-32D development board, the Elegoo Tap Module, and the Elegoo Seven-Color Flash Module. The exact carrier-board pin layout, interface components, course deadline, and available budget still need confirmation; no extra accelerometer is required for this scope.

## 6. Initial sprint: user stories and acceptance criteria

**Sprint goal:** Demonstrate one complete path from creating an alarm to dismissing it with the tap module across the room and receiving a reward, with enough logging to evaluate the interaction.

### US-01 — Create an alarm

As someone who struggles to get up, I want to create a named alarm for a future date and time so that I can prepare my next wake-up.

- Given valid inputs, saving adds the alarm to the list with its local date/time and status.
- Missing, invalid, or past times are rejected with an explanation.
- Only one alarm can be armed; selecting another clearly replaces the current armed alarm.
- Maps to FR-01 and FR-03.

### US-02 — Connect the hardware

As a user, I want to connect my ESP32-WROOM-32D board and see its status so that I know the physical controller is available.

- Connecting to the configured board shows “Connected” only after a board response, with a device identifier.
- An unavailable board shows a useful connection error and a retry action.
- Loss of communication changes the status within 5 seconds.
- Maps to FR-02 and FR-09.

### US-03 — Prepare and arm the alarm

As a user, I want to check the sound and sensor before arming so that I can confirm the prototype is ready.

- “Enable and test sound” produces an audible preview or a visible playback error.
- Arming requires a responding board, a tap input with a verified stable idle state, and a successful sound test.
- The screen confirms the next alarm time; the flash module remains disabled while waiting.
- Maps to FR-03.

### US-04 — Recognize an active alarm

As a sleepy user, I want a clear sound, flashing indicator, and pick-up-and-tap instruction so that I understand the alarm is active and what to do.

- At the scheduled time, sound loops, the flash module starts its color cycle, and the active-alarm instruction appears within the agreed timing target.
- The system logs the activation and reports missing device acknowledgment or audio playback failure.
- There is no ordinary snooze or successful dismiss button on the active-alarm screen.
- Maps to FR-04 and FR-09.

### US-05 — Stop the alarm with the tap module

As a user, I want to stop the alarm by picking up and tapping the controller across the room so that I leave the bed to reach it.

- A validated tap during the active session stops the sound, disables the flash module, and completes that session once. Pickup is part of the intended behavior, not a separately detected event.
- A tap while idle or armed does not pre-complete the next alarm.
- Nearby motion and bumps are tested and unintended dismissals are recorded. If a bump triggers the switch during ringing, it may dismiss the alarm; this is a known sensing limitation.
- Stale or duplicate events cannot complete another session.
- Maps to FR-05.

### US-06 — Receive a success reward

As a user, I want immediate, playful feedback after completing the interaction so that getting up feels rewarding.

- Completion shows “Morning quest completed!”, a short celebration, 10 points, and response time.
- Response time is labeled “Alarm response time,” measured from sound-start confirmation to accepted tap receipt; it is not labeled as time to get out of bed.
- Duplicate completion messages do not repeat the award. Aborted runs receive no reward.
- Maps to FR-06.

### US-07 — Record an evaluation run

As a student evaluating the prototype, I want a small event log so that I can inspect response times and distinguish success from failures.

- Each run has a unique session ID and contains the events specified in FR-07, using the laptop's timestamps for comparable intervals.
- Runs distinguish study, classroom demo, successful tap, manual abort, and technical failure.
- The log is available as a simple local table or file. Demo runs are excluded from study results.
- Separately observed bed-exit time and return-to-bed outcome can be recorded in the evaluation sheet.
- Maps to FR-07.

### US-08 — Repeat or interrupt the demonstration

As a presenter, I want a quick test mode and reset control so that I can reliably demonstrate the whole interaction in class.

- “Test alarm in 10 seconds” uses the same sound, flash-module, tap, and reward flow as a scheduled alarm.
- The test is clearly labeled and does not alter the real study schedule or study points; its reward is a demo preview.
- A separate presenter control can stop a run as aborted. Reset clears the active test and prepares the next run within 30 seconds.
- If the connection drops during a run, the screen reports it, sound continues until completion or manual stop, and reconnection synchronizes the board to the current session without duplicate rewards.
- Maps to FR-08 and FR-09.

**Definition of done:** All eight stories can be demonstrated with real hardware, the planned functional tests have recorded outcomes, and the complete flow succeeds in five consecutive classroom rehearsals. Simulation may help development, but the final demo must include real Tap Module input and a real Seven-Color Flash Module response.

## 8. Hypotheses and evaluation

The prototype tests desirability, interaction usability, and technical feasibility. A successful demonstration establishes that the interaction works in that setting; actual mornings are needed to investigate getting out of bed faster.

| ID | Hypothesis | Measure and proposed decision rule |
| --- | --- | --- |
| H1 — Faster bed exit | I get out of bed sooner with WakeQuest than with my usual bedside alarm. | Compare median time from actual audible alarm onset to first fully standing beside the bed. A reduction of at least 30% is a proposed meaningful improvement for this small study. Also report the absolute difference in seconds and all noncompletions. |
| H2 — Remaining up | Requiring the trip across the room helps me stay out of bed. | Record whether I return to bed within 10 minutes after first standing. Target at least 5 of 6 prototype mornings without returning; compare the count with baseline. |
| H3 — Reliable interaction | The tap switch can provide a responsive, usable dismissal interaction. | At least 19/20 deliberate taps accepted within 1 second and no duplicate completions. Measure incidental dismissals in 20 nuisance trials; the switch does not verify lifting or the cause of a knock. |
| H4 — Understandable flow | A first-time user can understand and complete setup and the tap interaction without coaching. | At least 4/5 testers complete the defined task within 2 minutes; record mistakes, hesitation, and requests for help. |
| H5 — Appealing reward | The success feedback feels encouraging rather than irritating. | After use, ask for motivation and annoyance ratings from 1–5 and a short explanation. Explore a median motivation rating of at least 4/5 and annoyance at most 2/5; this measures reported experience, not a proven behavioral effect. |

### Stage A — Simple functional and classroom tests

- Check the tap input's polarity and debounce, the flash module's on/off control, and 3.3 V/5 V interface compatibility. Run 10 short scheduled alarms: sound and indicator should start within 2 seconds. Complete five full demos and reset each within 30 seconds.
- Try 20 deliberate taps: accept at least 19 within 1 second without duplicate completion. Try 20 incidental movements or bumps while ringing and record unintended dismissals; the switch cannot prove pickup. Taps while idle or armed must not complete an alarm.
- Try an invalid alarm time, blocked audio, connection loss, duplicate events, and manual stop: show clear errors, report disconnection within 5 seconds, and never award duplicate points or reward an aborted run.
- Ask five classmates to connect the prepared board, create an alarm, and pick up and tap the controller across the room: at least four should finish within 2 minutes without coaching.
- Before morning testing, check that the chosen setup can remain running overnight and trigger and dismiss an alarm successfully.

### Stage B — Simple morning comparison

- Test 12 mornings: six with my usual bedside alarm and six with the prototype. For each pair of mornings, randomly choose which condition comes first.
- Keep wake-up time and alarm audibility similar. Put the controller in the same place across the room, use the intended pick-up-and-tap action, and note unusual sleep or morning conditions.
- In both conditions, use the same independent observation method, such as an observer or a consented recording, to measure time from audible alarm onset to standing upright. The tap timestamp is a separate measure.
- Record whether I return to bed within 10 minutes and briefly rate motivation and annoyance from 1–5. Mark technical failures and mornings when I do not stand within 5 minutes.
- Compare median time to stand and return-to-bed counts. The proposed target is 30% faster bed exit. Keep failures and noncompletions visible; do not claim success from completed mornings alone if a median cannot be estimated. Treat this as an exploratory result for one person.
