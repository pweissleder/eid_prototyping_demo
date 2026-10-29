// Copy this file to secrets.h in the same folder and fill in your phone-hotspot
// credentials:
//   cp firmware/wakequest/secrets.example.h firmware/wakequest/secrets.h
// secrets.h is gitignored. Never commit it and never paste the password anywhere else.
// Use a 2.4 GHz hotspot (the ESP32 has no 5 GHz radio).
#pragma once

#define WIFI_SSID "your-hotspot-name"
#define WIFI_PASS "your-hotspot-password"
