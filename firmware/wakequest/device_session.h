// Session and indicator rules from docs/protocol.md, pure C++
// (host-testable, no Arduino.h). The .ino parses JSON and calls apply().
#pragma once

#include <stdint.h>
#include <string.h>

#include "rgb_cycle.h"

enum class Command { Activate, Deactivate, Sync };

// A sessionId the device will store: non-empty and at most maxLen chars.
inline bool isValidSessionId(const char *id, unsigned maxLen) {
  if (id == nullptr || id[0] == '\0') return false;
  return strlen(id) <= maxLen;
}

template <unsigned MaxLen>
class DeviceSession {
 public:
  DeviceSession(uint32_t stepMs, uint32_t safetyTimeoutMs)
      : cycle_(stepMs), safetyTimeoutMs_(safetyTimeoutMs) {}

  // Handles one command and returns the ack's `ignored` flag.
  // id == nullptr means JSON null (only meaningful for sync). The caller has
  // already checked non-null ids with isValidSessionId().
  bool apply(Command cmd, const char *id, uint32_t now) {
    switch (cmd) {
      case Command::Activate:
        activate(id, now);
        return false;
      case Command::Deactivate:
        if (hasSession_ && (id == nullptr || strcmp(id, sessionId_) != 0)) return true;
        clear();
        return false;
      case Command::Sync:
        if (id == nullptr) {
          clear();
        } else {
          activate(id, now);
        }
        return false;
    }
    return false;
  }

  // Call every loop pass. Returns true once, when the safety timeout fires.
  bool update(uint32_t now) {
    if (!cycle_.on() || now - activatedAt_ < safetyTimeoutMs_) return false;
    cycle_.stop();
    timedOut_ = true;
    return true;
  }

  const char *sessionId() const { return hasSession_ ? sessionId_ : nullptr; }
  bool indicator() const { return cycle_.on(); }
  bool indicatorTimedOut() const { return timedOut_; }
  Rgb colour(uint32_t now) const { return cycle_.colour(now); }

 private:
  void activate(const char *id, uint32_t now) {
    // Same session: keep the cycle and the timer, and never re-light a session
    // whose indicator hit the safety timeout.
    if (hasSession_ && strcmp(id, sessionId_) == 0) return;
    strncpy(sessionId_, id, MaxLen);
    sessionId_[MaxLen] = '\0';
    hasSession_ = true;
    cycle_.start(now);
    activatedAt_ = now;
    timedOut_ = false;
  }

  void clear() {
    hasSession_ = false;
    sessionId_[0] = '\0';
    cycle_.stop();
    timedOut_ = false;
  }

  RgbCycle cycle_;
  uint32_t safetyTimeoutMs_;
  char sessionId_[MaxLen + 1] = {0};
  bool hasSession_ = false;
  uint32_t activatedAt_ = 0;
  bool timedOut_ = false;
};
