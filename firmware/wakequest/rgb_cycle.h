// 7-colour indicator cycle, pure C++ (host-testable, no Arduino.h).
// Red, green, blue, yellow, cyan, magenta, white; each for stepMs.
#pragma once

#include <stdint.h>

struct Rgb {
  uint8_t r, g, b;  // 1 = channel on
};

class RgbCycle {
 public:
  static const int COLOURS = 7;

  explicit RgbCycle(uint32_t stepMs) : stepMs_(stepMs) {}

  // Turn on and restart at red.
  void start(uint32_t now) {
    on_ = true;
    startedAt_ = now;
  }

  void stop() { on_ = false; }

  bool on() const { return on_; }

  Rgb colour(uint32_t now) const {
    if (!on_) return Rgb{0, 0, 0};
    static const Rgb SEQ[COLOURS] = {
        {1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {1, 1, 0}, {0, 1, 1}, {1, 0, 1}, {1, 1, 1},
    };
    return SEQ[((now - startedAt_) / stepMs_) % COLOURS];
  }

 private:
  uint32_t stepMs_;
  bool on_ = false;
  uint32_t startedAt_ = 0;
};
