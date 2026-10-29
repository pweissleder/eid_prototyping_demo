// Tap decisions, pure C++ (host-testable, no Arduino.h).
//
// The ISR only records active-edge timestamps; the loop feeds them here in
// order, plus a level sample each pass. Times are millis() values; all
// comparisons use unsigned differences so they survive the 49-day wrap.
#pragma once

#include <stdint.h>

class TapDetector {
 public:
  TapDetector(uint32_t lockoutMs, uint32_t stableMs, int idleLevel)
      : lockoutMs_(lockoutMs), stableMs_(stableMs), idleLevel_(idleLevel) {}

  // Call once at boot. The sensor counts as unstable until stableMs have passed.
  void begin(uint32_t now) {
    lastDisturbance_ = now;
    levelIdle_ = true;
  }

  // One idle->active edge at time t. Returns true if it is an accepted tap
  // (first edge, or at least lockoutMs after the last accepted tap).
  bool onActiveEdge(uint32_t t) {
    disturbAt(t);
    if (haveAccepted_ && t - lastAccepted_ < lockoutMs_) return false;
    haveAccepted_ = true;
    lastAccepted_ = t;
    tapSeq_++;
    return true;
  }

  // An edge that is not a tap candidate (e.g. dropped by a full ISR buffer):
  // it only resets sensorStable.
  void onOtherEdge(uint32_t t) { disturbAt(t); }

  // Current pin level read at time now.
  void onLevelSample(int level, uint32_t now) {
    levelIdle_ = level == idleLevel_;
    if (!levelIdle_) disturbAt(now);
  }

  // Idle level with no edges for at least stableMs.
  bool sensorStable(uint32_t now) const {
    return levelIdle_ && now - lastDisturbance_ >= stableMs_;
  }

  uint32_t tapSeq() const { return tapSeq_; }

 private:
  // Keep the latest disturbance; edges from the ISR can arrive slightly
  // older than the last level sample.
  void disturbAt(uint32_t t) {
    if ((int32_t)(t - lastDisturbance_) > 0) lastDisturbance_ = t;
  }

  uint32_t lockoutMs_;
  uint32_t stableMs_;
  int idleLevel_;
  bool haveAccepted_ = false;
  uint32_t lastAccepted_ = 0;
  uint32_t lastDisturbance_ = 0;
  bool levelIdle_ = true;
  uint32_t tapSeq_ = 0;
};
