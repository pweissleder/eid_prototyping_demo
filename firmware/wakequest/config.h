// WakeQuest firmware constants. Pins and levels are bench-confirmed
// (docs/test-log.md B2-B5); protocol values follow docs/protocol.md.
#pragma once

#include <stdint.h>

// Pins (ESP32-WROOM-32D DevKitC-style, 3.3 V logic, nothing on 5V).
const int TAP_PIN = 27;              // Tap Module S, idle HIGH (module pull-up), tap = LOW
const int TAP_IDLE_LEVEL = 1;        // HIGH
const int RGB_PIN_R = 26;            // common cathode, HIGH = on, 150 ohm onboard
const int RGB_PIN_G = 25;
const int RGB_PIN_B = 33;

// Tap detection.
const uint32_t TAP_LOCKOUT_MS = 250;        // longest bench burst was 21 ms
const uint32_t SENSOR_STABLE_MS = 2000;     // idle with no edges for this long

// Indicator.
const uint32_t CYCLE_STEP_MS = 400;                     // per colour
const uint32_t SAFETY_TIMEOUT_MS = 15UL * 60UL * 1000UL; // indicator off 15 min after activate

// Network.
const char *const HOSTNAME = "wakequest";   // -> wakequest.local
const uint16_t WS_PORT = 81;
const uint32_t STATUS_INTERVAL_MS = 1000;
const uint32_t IP_LOG_INTERVAL_MS = 10000;  // repeat IP on serial while no client
const uint32_t WIFI_RETRY_MS = 30000;       // re-begin if auto-reconnect gives up
const uint32_t WS_PING_INTERVAL_MS = 5000;  // device-side heartbeat, frees dead clients
const uint32_t WS_PONG_TIMEOUT_MS = 3000;
const uint8_t WS_PONG_MISSES = 2;

// Versions.
const char *const FIRMWARE_VERSION = "0.1.0";
const int PROTOCOL_VERSION = 1;

// Longest sessionId the device stores (service ids look like s-20261030-070000-3f9c).
const unsigned SESSION_ID_MAX_LEN = 64;
