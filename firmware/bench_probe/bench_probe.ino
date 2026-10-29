// WakeQuest bench probe — hardware bring-up without Wi-Fi.
//
// Wiring (PLAN.md §3.4): Tap S -> GPIO27, Tap middle (+) -> 3V3, Tap - -> GND.
// RGB module printed R/G/B -> GPIO26/25/33, RGB - -> GND. Nothing on 5V.
//
// Serial monitor at 115200 baud. Commands (press Enter after each):
//   h        help
//   p        re-run tap pin probe (idle level, floating check)       [B2]
//   s        tap statistics                                          [B3]
//   z        reset tap statistics
//   l<ms>    set tap lockout, e.g. l150
//   r g b    light one RGB channel (GPIO26 / GPIO25 / GPIO33)        [B4]
//   c        start the 7-color cycle
//   o        all LEDs off
//   i        60 s idle test: count edges while nobody touches it     [B5]

const int TAP_PIN = 27;
const int RGB_PINS[3] = {26, 25, 33};             // printed R, G, B
const char *RGB_LABELS[3] = {"R", "G", "B"};

const unsigned long BURST_QUIET_MS = 50;          // burst ends after this long back at idle
const unsigned long CYCLE_STEP_MS = 400;
const unsigned long IDLE_TEST_MS = 60000;

// Red, green, blue, yellow, cyan, magenta, white.
const uint8_t CYCLE[7][3] = {
  {1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {1, 1, 0}, {0, 1, 1}, {1, 0, 1}, {1, 1, 1},
};

int idleLevel = HIGH;
bool pinFloating = false;
unsigned long lockoutMs = 250;

int lastLevel;
bool inBurst = false;
unsigned long burstStartUs = 0;
unsigned long lastEdgeUs = 0;
unsigned long lastEdgeMs = 0;
unsigned int burstEdges = 0;
bool burstAccepted = false;

unsigned long taps = 0;
unsigned long rejectedBursts = 0;
unsigned long lastAcceptedMs = 0;
bool haveAccepted = false;
float maxBounceMs = 0;
unsigned long minBurstGapMs = 0;
bool haveBurstGap = false;
unsigned long lastBurstStartMs = 0;
bool haveBurst = false;

bool cycling = false;
int cycleIndex = 0;
unsigned long cycleStepAt = 0;

bool idleTest = false;
unsigned long idleTestEndsAt = 0;
unsigned long idleTestEdges = 0;

String line;

int sampleLevel() {
  int highs = 0;
  for (int i = 0; i < 20; i++) {
    highs += digitalRead(TAP_PIN);
    delay(2);
  }
  return highs >= 10 ? HIGH : LOW;
}

const char *levelName(int level) {
  return level == HIGH ? "HIGH" : "LOW";
}

// B2: read with no internal pull, then with pull-up and pull-down. A pin that
// follows the internal pulls is not driven by the module's own resistor.
void probeTapPin() {
  pinMode(TAP_PIN, INPUT);
  delay(20);
  int plain = sampleLevel();
  pinMode(TAP_PIN, INPUT_PULLUP);
  delay(20);
  int withPullUp = sampleLevel();
  pinMode(TAP_PIN, INPUT_PULLDOWN);
  delay(20);
  int withPullDown = sampleLevel();
  pinMode(TAP_PIN, INPUT);
  delay(20);

  pinFloating = withPullUp == HIGH && withPullDown == LOW;
  idleLevel = plain;
  lastLevel = digitalRead(TAP_PIN);

  Serial.println();
  Serial.println("== Tap pin probe (B2) ==");
  Serial.printf("  INPUT: %s   INPUT_PULLUP: %s   INPUT_PULLDOWN: %s\n",
                levelName(plain), levelName(withPullUp), levelName(withPullDown));
  if (pinFloating) {
    Serial.println("  RESULT: pin follows the internal pulls -> FLOATING.");
    Serial.println("  Check W3 (3V3 -> a4), W4 (GND -> a5) and W5 (27 -> a3). Do not trust tap counts.");
  } else {
    Serial.printf("  RESULT: module holds the pin %s at rest -> idle level = %s, tap level = %s.\n",
                  levelName(idleLevel), levelName(idleLevel), levelName(idleLevel == HIGH ? LOW : HIGH));
    Serial.printf("  Record TAP_ACTIVE_LEVEL = %s in docs/test-log.md.\n", idleLevel == HIGH ? "LOW" : "HIGH");
  }
  Serial.println("  Keep the module still during the probe; run 'p' again if you touched it.");
}

void setRgb(uint8_t r, uint8_t g, uint8_t b) {
  digitalWrite(RGB_PINS[0], r ? HIGH : LOW);
  digitalWrite(RGB_PINS[1], g ? HIGH : LOW);
  digitalWrite(RGB_PINS[2], b ? HIGH : LOW);
}

void showChannel(int channel) {
  cycling = false;
  setRgb(channel == 0, channel == 1, channel == 2);
  Serial.printf("GPIO%d (printed %s) ON. Which color do you see? Record it in docs/test-log.md (B4).\n",
                RGB_PINS[channel], RGB_LABELS[channel]);
}

void printStats() {
  Serial.println();
  Serial.println("== Tap statistics (B3) ==");
  Serial.printf("  accepted taps: %lu   rejected bursts (inside lockout): %lu   lockout: %lu ms\n",
                taps, rejectedBursts, lockoutMs);
  Serial.printf("  longest burst (contact + bounce): %.1f ms\n", maxBounceMs);
  if (haveBurstGap) {
    Serial.printf("  shortest gap between burst starts: %lu ms\n", minBurstGapMs);
  }
  Serial.println("  Target: 20 deliberate taps -> 20 accepted, 0 rejected that you meant as taps.");
}

void resetStats() {
  taps = 0;
  rejectedBursts = 0;
  haveAccepted = false;
  maxBounceMs = 0;
  haveBurstGap = false;
  haveBurst = false;
  Serial.println("Tap statistics reset.");
}

void printHelp() {
  Serial.println();
  Serial.println("WakeQuest bench probe commands:");
  Serial.println("  p  probe tap pin      s  tap stats       z  reset stats     l<ms>  set lockout");
  Serial.println("  r/g/b  one RGB channel   c  7-color cycle   o  LEDs off       i  60 s idle test");
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;
  char c = cmd.charAt(0);
  switch (c) {
    case 'h': printHelp(); break;
    case 'p': probeTapPin(); break;
    case 's': printStats(); break;
    case 'z': resetStats(); break;
    case 'l': {
      long value = cmd.substring(1).toInt();
      if (value >= 10 && value <= 2000) {
        lockoutMs = value;
        Serial.printf("Lockout set to %lu ms.\n", lockoutMs);
      } else {
        Serial.println("Use l<ms> with 10..2000, e.g. l150.");
      }
      break;
    }
    case 'r': showChannel(0); break;
    case 'g': showChannel(1); break;
    case 'b': showChannel(2); break;
    case 'c':
      cycling = true;
      cycleIndex = 0;
      cycleStepAt = 0;
      Serial.println("7-color cycle running (red, green, blue, yellow, cyan, magenta, white). 'o' stops it.");
      break;
    case 'o':
      cycling = false;
      setRgb(0, 0, 0);
      Serial.println("LEDs off.");
      break;
    case 'i':
      idleTest = true;
      idleTestEdges = 0;
      idleTestEndsAt = millis() + IDLE_TEST_MS;
      Serial.println("Idle test (B5) running for 60 s. Do not touch the module or the table.");
      break;
    default:
      Serial.printf("Unknown command '%s'. Type h for help.\n", cmd.c_str());
  }
}

void readSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      handleCommand(line);
      line = "";
    } else if (line.length() < 16) {
      line += ch;
    }
  }
}

void onEdge(int level, unsigned long nowUs, unsigned long nowMs) {
  if (idleTest) idleTestEdges++;

  if (!inBurst && level != idleLevel) {
    inBurst = true;
    burstStartUs = nowUs;
    burstEdges = 0;
    if (haveBurst) {
      unsigned long gap = nowMs - lastBurstStartMs;
      if (!haveBurstGap || gap < minBurstGapMs) minBurstGapMs = gap;
      haveBurstGap = true;
    }
    haveBurst = true;
    lastBurstStartMs = nowMs;

    burstAccepted = !haveAccepted || nowMs - lastAcceptedMs >= lockoutMs;
    if (burstAccepted) {
      taps++;
      haveAccepted = true;
      lastAcceptedMs = nowMs;
      Serial.printf("TAP #%lu at %lu ms\n", taps, nowMs);
    } else {
      rejectedBursts++;
    }
  }
  if (inBurst) {
    burstEdges++;
    lastEdgeUs = nowUs;
    lastEdgeMs = nowMs;
  }
}

void pollTap() {
  int level = digitalRead(TAP_PIN);
  unsigned long nowUs = micros();
  unsigned long nowMs = millis();
  if (level != lastLevel) {
    lastLevel = level;
    onEdge(level, nowUs, nowMs);
  }
  if (inBurst && level == idleLevel && nowMs - lastEdgeMs > BURST_QUIET_MS) {
    inBurst = false;
    float bounceMs = (lastEdgeUs - burstStartUs) / 1000.0f;
    if (bounceMs > maxBounceMs) maxBounceMs = bounceMs;
    Serial.printf("  burst: %u edges over %.1f ms -> %s\n", burstEdges, bounceMs,
                  burstAccepted ? "accepted" : "REJECTED (inside lockout)");
  }
}

void updateCycle() {
  if (!cycling) return;
  unsigned long now = millis();
  if (cycleStepAt != 0 && now - cycleStepAt < CYCLE_STEP_MS) return;
  cycleStepAt = now;
  setRgb(CYCLE[cycleIndex][0], CYCLE[cycleIndex][1], CYCLE[cycleIndex][2]);
  cycleIndex = (cycleIndex + 1) % 7;
}

void updateIdleTest() {
  if (!idleTest || (long)(millis() - idleTestEndsAt) < 0) return;
  idleTest = false;
  Serial.printf("Idle test finished: %lu edges in 60 s -> %s\n", idleTestEdges,
                idleTestEdges == 0 ? "PASS candidate (confirm nobody touched it)" : "edges seen while idle");
}

void setup() {
  Serial.begin(115200);
  delay(500);
  for (int i = 0; i < 3; i++) {
    pinMode(RGB_PINS[i], OUTPUT);
  }
  setRgb(0, 0, 0);
  Serial.println();
  Serial.println("WakeQuest bench probe — no Wi-Fi. Pins: tap GPIO27, RGB GPIO26/25/33.");
  probeTapPin();
  printHelp();
}

void loop() {
  readSerial();
  pollTap();
  updateCycle();
  updateIdleTest();
}
