// WakeQuest controller firmware — WebSocket server for the local service.
// Contract: docs/protocol.md (v1). Wiring and bench results: docs/test-log.md.
//
// Wiring: Tap S -> GPIO27, Tap middle (+) -> 3V3, Tap - -> GND.
// RGB R/G/B -> GPIO26/25/33, RGB - -> GND. Nothing on 5V.
//
// Serial monitor at 115200 baud: boot banner, Wi-Fi state, IP address
// (repeated every 10 s while no client is connected), commands and taps.
//
// Logic lives in the pure headers (tap_detector.h, rgb_cycle.h,
// device_session.h) and is unit-tested by firmware/test/test_logic.cpp.

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Missing firmware/wakequest/secrets.h: copy secrets.example.h to secrets.h and fill in your hotspot SSID and password."
#endif

#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>
#include <esp_mac.h>

#include "config.h"
#include "tap_detector.h"
#include "device_session.h"

WebSocketsServer ws(WS_PORT);
TapDetector tap(TAP_LOCKOUT_MS, SENSOR_STABLE_MS, TAP_IDLE_LEVEL);
DeviceSession<SESSION_ID_MAX_LEN> session(CYCLE_STEP_MS, SAFETY_TIMEOUT_MS);

char deviceId[16];
int activeClient = -1;          // the one client we talk to, -1 = none
uint32_t lastStatusAt = 0;
uint32_t lastIpLogAt = 0;
bool wifiUp = false;
uint32_t wifiDownSince = 0;
bool netStarted = false;        // WebSocket server + mDNS started once
Rgb shownColour = {0, 0, 0};

// ---- Tap ISR: record edge times only; lockout is decided in loop() ----

const int EDGE_BUF = 32;        // bench bursts had at most 24 edges
volatile uint32_t edgeTimes[EDGE_BUF];
volatile int edgeCount = 0;
volatile uint32_t edgesDropped = 0;
portMUX_TYPE edgeMux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR onTapEdge() {
  portENTER_CRITICAL_ISR(&edgeMux);
  int n = edgeCount;
  if (n < EDGE_BUF) {
    edgeTimes[n] = millis();
    edgeCount = n + 1;
  } else {
    edgesDropped = edgesDropped + 1;  // later bounce edges only; the first edge is kept
  }
  portEXIT_CRITICAL_ISR(&edgeMux);
}

// ---- LEDs ----

void writeRgb(Rgb c) {
  digitalWrite(RGB_PIN_R, c.r ? HIGH : LOW);
  digitalWrite(RGB_PIN_G, c.g ? HIGH : LOW);
  digitalWrite(RGB_PIN_B, c.b ? HIGH : LOW);
  shownColour = c;
}

void updateLeds(uint32_t now) {
  Rgb c = session.colour(now);
  if (c.r != shownColour.r || c.g != shownColour.g || c.b != shownColour.b) writeRgb(c);
}

// ---- Messages to the service ----

void sendDoc(JsonDocument &doc) {
  if (activeClient < 0) return;
  char buf[384];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  ws.sendTXT((uint8_t)activeClient, buf, n);
}

void putSessionId(JsonDocument &doc) {
  const char *id = session.sessionId();
  if (id) {
    doc["sessionId"] = id;
  } else {
    doc["sessionId"] = nullptr;
  }
}

void sendHello() {
  uint32_t now = millis();
  JsonDocument doc;
  doc["type"] = "hello";
  doc["protocol"] = PROTOCOL_VERSION;
  doc["deviceId"] = deviceId;
  doc["firmware"] = FIRMWARE_VERSION;
  doc["tapActiveLevel"] = "LOW";
  doc["sensorStable"] = tap.sensorStable(now);
  doc["indicator"] = session.indicator();
  putSessionId(doc);
  doc["tapSeq"] = tap.tapSeq();
  doc["uptimeMs"] = now;
  sendDoc(doc);
}

void sendStatus() {
  uint32_t now = millis();
  JsonDocument doc;
  doc["type"] = "status";
  doc["sensorStable"] = tap.sensorStable(now);
  doc["indicator"] = session.indicator();
  doc["indicatorTimedOut"] = session.indicatorTimedOut();
  putSessionId(doc);
  doc["tapSeq"] = tap.tapSeq();
  doc["uptimeMs"] = now;
  doc["rssi"] = WiFi.RSSI();
  sendDoc(doc);
}

void sendAck(const char *of, bool ignored) {
  JsonDocument doc;
  doc["type"] = "ack";
  doc["of"] = of;
  putSessionId(doc);
  doc["indicator"] = session.indicator();
  doc["ignored"] = ignored;
  sendDoc(doc);
}

void sendTap(uint32_t seq, uint32_t at) {
  JsonDocument doc;
  doc["type"] = "tap";
  doc["seq"] = seq;
  putSessionId(doc);
  doc["uptimeMs"] = at;
  sendDoc(doc);
}

void sendError(const char *code, const char *detail) {
  JsonDocument doc;
  doc["type"] = "error";
  doc["code"] = code;
  doc["detail"] = detail;
  sendDoc(doc);
  Serial.printf("[ws] error %s: %s\n", code, detail);
}

// ---- Commands from the service ----

void handleText(const uint8_t *payload, size_t length) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    sendError("bad_json", err.c_str());
    return;
  }
  if (!doc.is<JsonObject>()) {
    sendError("bad_json", "message is not a JSON object");
    return;
  }
  JsonObjectConst msg = doc.as<JsonObjectConst>();
  JsonVariantConst type = msg["type"];
  if (!type.is<const char *>()) {
    sendError("bad_field", "type must be a string");
    return;
  }
  const char *t = type.as<const char *>();
  Command cmd;
  if (strcmp(t, "activate") == 0) {
    cmd = Command::Activate;
  } else if (strcmp(t, "deactivate") == 0) {
    cmd = Command::Deactivate;
  } else if (strcmp(t, "sync") == 0) {
    cmd = Command::Sync;
  } else {
    sendError("unknown_type", t);
    return;
  }

  // activate / deactivate need a string id; sync takes a string or null.
  JsonVariantConst idField = msg["sessionId"];
  const char *id = nullptr;
  if (idField.is<const char *>()) {
    id = idField.as<const char *>();
    if (!isValidSessionId(id, SESSION_ID_MAX_LEN)) {
      sendError("bad_field", "sessionId must be 1..64 characters");
      return;
    }
  } else if (!(cmd == Command::Sync && !idField.isUnbound() && idField.isNull())) {
    sendError("bad_field", cmd == Command::Sync ? "sessionId must be a string or null"
                                                : "sessionId must be a string");
    return;
  }

  const char *reason = msg["reason"] | "";
  bool ignored = session.apply(cmd, id, millis());
  updateLeds(millis());
  Serial.printf("[cmd] %s sessionId=%s%s%s -> indicator=%s sessionId=%s%s\n", t, id ? id : "null",
                reason[0] ? " reason=" : "", reason, session.indicator() ? "on" : "off",
                session.sessionId() ? session.sessionId() : "null", ignored ? " (ignored)" : "");
  sendAck(t, ignored);
}

void onWsEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      // Single client: the newest connection wins, older ones are closed.
      for (uint8_t i = 0; i < WEBSOCKETS_SERVER_CLIENT_MAX; i++) {
        if (i != num && ws.clientIsConnected(i)) {
          Serial.printf("[ws] closing older client #%u\n", i);
          ws.disconnect(i);
        }
      }
      activeClient = num;
      IPAddress ip = ws.remoteIP(num);
      Serial.printf("[ws] client #%u connected from %s\n", num, ip.toString().c_str());
      sendHello();
      lastStatusAt = millis();
      break;
    }
    case WStype_DISCONNECTED:
      Serial.printf("[ws] client #%u disconnected\n", num);
      if (num == activeClient) {
        activeClient = -1;
        lastIpLogAt = 0;          // print the IP again right away
      }
      break;
    case WStype_TEXT:
      if (num == activeClient) handleText(payload, length);
      break;
    case WStype_BIN:
      if (num == activeClient) sendError("bad_json", "binary frames are not supported");
      break;
    default:
      break;
  }
}

// ---- Wi-Fi ----

void startNetworkServices() {
  ws.begin();
  ws.onEvent(onWsEvent);
  ws.enableHeartbeat(WS_PING_INTERVAL_MS, WS_PONG_TIMEOUT_MS, WS_PONG_MISSES);
  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("ws", "tcp", WS_PORT);
    Serial.printf("[mdns] %s.local -> ws://%s.local:%u/\n", HOSTNAME, HOSTNAME, WS_PORT);
  } else {
    Serial.println("[mdns] start failed; use the IP address instead");
  }
  netStarted = true;
}

void printAddress() {
  Serial.printf("[wifi] IP %s  ws://%s:%u/  (ws://%s.local:%u/)  RSSI %d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str(), WS_PORT,
                HOSTNAME, WS_PORT, WiFi.RSSI());
}

void updateWifi(uint32_t now) {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiUp) {
    wifiUp = true;
    Serial.printf("[wifi] connected to \"%s\"\n", WIFI_SSID);
    if (!netStarted) startNetworkServices();
    printAddress();
    lastIpLogAt = now;
  } else if (!up && wifiUp) {
    wifiUp = false;
    wifiDownSince = now;
    Serial.println("[wifi] connection lost, reconnecting...");
  }

  if (wifiUp) {
    if (activeClient < 0 && (lastIpLogAt == 0 || now - lastIpLogAt >= IP_LOG_INTERVAL_MS)) {
      printAddress();
      Serial.println("[ws] waiting for the service to connect");
      lastIpLogAt = now;
    }
  } else if (now - wifiDownSince >= WIFI_RETRY_MS) {
    // Auto-reconnect stops on some reasons (e.g. auth failure); start over.
    Serial.printf("[wifi] still not connected (status %d), retrying \"%s\"\n", (int)WiFi.status(),
                  WIFI_SSID);
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    wifiDownSince = now;
  }
}

// ---- Taps ----

void processTaps(uint32_t now) {
  uint32_t edges[EDGE_BUF];
  int n;
  uint32_t dropped;
  portENTER_CRITICAL(&edgeMux);
  n = edgeCount;
  for (int i = 0; i < n; i++) edges[i] = edgeTimes[i];
  edgeCount = 0;
  dropped = edgesDropped;
  edgesDropped = 0;
  portEXIT_CRITICAL(&edgeMux);

  for (int i = 0; i < n; i++) {
    if (!tap.onActiveEdge(edges[i])) continue;
    const char *id = session.sessionId();
    if (activeClient >= 0) {
      sendTap(tap.tapSeq(), edges[i]);
      Serial.printf("[tap] seq=%lu at %lu ms sessionId=%s -> sent\n", (unsigned long)tap.tapSeq(),
                    (unsigned long)edges[i], id ? id : "null");
    } else {
      Serial.printf("[tap] seq=%lu at %lu ms -> not sent (no client)\n",
                    (unsigned long)tap.tapSeq(), (unsigned long)edges[i]);
    }
  }
  if (dropped > 0) tap.onOtherEdge(now);  // overflow (bounce edges only): keep it unstable
  tap.onLevelSample(digitalRead(TAP_PIN), now);
}

// ---- Setup / loop ----

void setup() {
  pinMode(RGB_PIN_R, OUTPUT);
  pinMode(RGB_PIN_G, OUTPUT);
  pinMode(RGB_PIN_B, OUTPUT);
  writeRgb(Rgb{0, 0, 0});

  Serial.begin(115200);
  delay(300);

  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(deviceId, sizeof(deviceId), "wq-%02x%02x%02x", mac[3], mac[4], mac[5]);

  Serial.println();
  Serial.printf("WakeQuest firmware %s, protocol %d, deviceId %s\n", FIRMWARE_VERSION,
                PROTOCOL_VERSION, deviceId);
  Serial.printf("Tap GPIO%d (idle HIGH, tap LOW), RGB GPIO%d/%d/%d\n", TAP_PIN, RGB_PIN_R,
                RGB_PIN_G, RGB_PIN_B);

  pinMode(TAP_PIN, INPUT);      // the module's own 10k pull-up holds it HIGH
  tap.begin(millis());
  attachInterrupt(digitalPinToInterrupt(TAP_PIN), onTapEdge, FALLING);

  WiFi.setHostname(HOSTNAME);   // must come before WiFi.mode()
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);         // lower latency for taps and acks
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  wifiDownSince = millis();
  Serial.printf("[wifi] connecting to \"%s\"...\n", WIFI_SSID);
}

void loop() {
  uint32_t now = millis();
  updateWifi(now);
  if (netStarted) ws.loop();

  now = millis();
  processTaps(now);
  if (session.update(now)) {
    Serial.println("[indicator] safety timeout: indicator off, session kept");
  }
  updateLeds(now);

  if (activeClient >= 0 && now - lastStatusAt >= STATUS_INTERVAL_MS) {
    lastStatusAt = now;
    sendStatus();
  }
}
