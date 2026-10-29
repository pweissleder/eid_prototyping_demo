// Host unit tests for the WakeQuest firmware logic (no Arduino, no frameworks).
// Run from the repo root:
//   clang++ -std=c++17 -Wall -Wextra -I firmware/wakequest firmware/test/test_logic.cpp \
//     -o build/test_logic && ./build/test_logic

#include <stdio.h>
#include <string.h>

#include "config.h"
#include "device_session.h"
#include "rgb_cycle.h"
#include "tap_detector.h"

static int checks = 0;
static int failures = 0;

#define CHECK(cond)                                                    \
  do {                                                                 \
    checks++;                                                          \
    if (!(cond)) {                                                     \
      failures++;                                                      \
      printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);         \
    }                                                                  \
  } while (0)

#define RUN(test)              \
  do {                         \
    int before = failures;     \
    test();                    \
    printf("%s %s\n", failures == before ? "ok  " : "FAIL", #test); \
  } while (0)

static bool isColour(Rgb c, int r, int g, int b) { return c.r == r && c.g == g && c.b == b; }
static bool sameId(const char *a, const char *b) {
  if (a == nullptr || b == nullptr) return a == b;
  return strcmp(a, b) == 0;
}

static TapDetector newTap(uint32_t boot = 0) {
  TapDetector t(TAP_LOCKOUT_MS, SENSOR_STABLE_MS, TAP_IDLE_LEVEL);
  t.begin(boot);
  return t;
}

// ---- Tap detector ----

static void test_bounce_burst_is_one_tap() {
  TapDetector t = newTap();
  // Longest bench burst: 24 edges within 21 ms.
  int accepted = 0;
  for (int i = 0; i < 24; i++) accepted += t.onActiveEdge(5000 + (uint32_t)i * 21 / 23);
  CHECK(accepted == 1);
  CHECK(t.tapSeq() == 1);
}

static void test_lockout_boundary() {
  TapDetector t = newTap();
  CHECK(t.onActiveEdge(1000));
  CHECK(!t.onActiveEdge(1001));
  CHECK(!t.onActiveEdge(1000 + TAP_LOCKOUT_MS - 1));
  CHECK(t.onActiveEdge(1000 + TAP_LOCKOUT_MS));  // exactly at lockout: accepted
  CHECK(t.tapSeq() == 2);
}

static void test_lockout_measured_from_accepted_tap() {
  TapDetector t = newTap();
  CHECK(t.onActiveEdge(1000));
  CHECK(!t.onActiveEdge(1200));  // rejected edge does not extend the lockout
  CHECK(t.onActiveEdge(1250));
}

static void test_tapseq_increments() {
  TapDetector t = newTap();
  CHECK(t.tapSeq() == 0);
  for (uint32_t i = 1; i <= 5; i++) {
    CHECK(t.onActiveEdge(1000 * i));
    CHECK(t.tapSeq() == i);
  }
}

static void test_first_edge_at_time_zero_accepted() {
  TapDetector t = newTap();
  CHECK(t.onActiveEdge(0));
  CHECK(t.tapSeq() == 1);
}

static void test_lockout_across_millis_wrap() {
  TapDetector t = newTap(0xFFFFFF00u);
  CHECK(t.onActiveEdge(0xFFFFFFF0u));
  CHECK(!t.onActiveEdge(0x00000010u));  // 32 ms later, after the wrap
  CHECK(t.onActiveEdge(0xFFFFFFF0u + TAP_LOCKOUT_MS));
}

static void test_sensor_stable_after_boot() {
  TapDetector t = newTap(100);
  t.onLevelSample(1, 100);
  CHECK(!t.sensorStable(100));
  CHECK(!t.sensorStable(100 + SENSOR_STABLE_MS - 1));
  t.onLevelSample(1, 100 + SENSOR_STABLE_MS);
  CHECK(t.sensorStable(100 + SENSOR_STABLE_MS));
}

static void test_sensor_unstable_after_edge() {
  TapDetector t = newTap();
  t.onLevelSample(1, 5000);
  CHECK(t.sensorStable(5000));
  t.onActiveEdge(6000);
  t.onLevelSample(1, 6001);
  CHECK(!t.sensorStable(6001));
  CHECK(!t.sensorStable(6000 + SENSOR_STABLE_MS - 1));
  CHECK(t.sensorStable(6000 + SENSOR_STABLE_MS));
}

static void test_rejected_edge_still_disturbs() {
  TapDetector t = newTap();
  t.onActiveEdge(5000);
  t.onActiveEdge(5100);  // inside lockout, rejected
  CHECK(!t.sensorStable(5000 + SENSOR_STABLE_MS));
  CHECK(t.sensorStable(5100 + SENSOR_STABLE_MS));
}

static void test_sensor_unstable_while_active_level() {
  TapDetector t = newTap();
  t.onLevelSample(0, 5000);  // held LOW (active)
  CHECK(!t.sensorStable(5000));
  t.onLevelSample(0, 9000);
  CHECK(!t.sensorStable(9000));
  t.onLevelSample(1, 9001);  // back to idle: 2000 ms from the last active sample
  CHECK(!t.sensorStable(9001));
  CHECK(!t.sensorStable(9000 + SENSOR_STABLE_MS - 1));
  CHECK(t.sensorStable(9000 + SENSOR_STABLE_MS));
}

static void test_late_isr_edge_does_not_move_back_disturbance() {
  TapDetector t = newTap();
  t.onLevelSample(0, 7000);  // active sample at 7000
  t.onActiveEdge(6990);      // ISR edge processed after, but older
  t.onLevelSample(1, 7001);
  CHECK(!t.sensorStable(6990 + SENSOR_STABLE_MS));
  CHECK(t.sensorStable(7000 + SENSOR_STABLE_MS));
}

static void test_other_edge_only_disturbs() {
  TapDetector t = newTap();
  t.onOtherEdge(5000);
  CHECK(t.tapSeq() == 0);
  CHECK(!t.sensorStable(5000 + SENSOR_STABLE_MS - 1));
  CHECK(t.sensorStable(5000 + SENSOR_STABLE_MS));
}

// ---- RGB cycle ----

static void test_cycle_off_is_dark() {
  RgbCycle c(CYCLE_STEP_MS);
  CHECK(isColour(c.colour(0), 0, 0, 0));
  CHECK(isColour(c.colour(123456), 0, 0, 0));
}

static void test_cycle_order_and_timing() {
  RgbCycle c(CYCLE_STEP_MS);
  const uint32_t t0 = 10000;
  c.start(t0);
  const int expected[7][3] = {
      {1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {1, 1, 0}, {0, 1, 1}, {1, 0, 1}, {1, 1, 1},
  };
  for (int i = 0; i < 7; i++) {
    uint32_t begin = t0 + i * CYCLE_STEP_MS;
    CHECK(isColour(c.colour(begin), expected[i][0], expected[i][1], expected[i][2]));
    CHECK(isColour(c.colour(begin + CYCLE_STEP_MS - 1), expected[i][0], expected[i][1],
                   expected[i][2]));
  }
  CHECK(isColour(c.colour(t0 + 7 * CYCLE_STEP_MS), 1, 0, 0));  // wraps to red
  CHECK(isColour(c.colour(t0 + 8 * CYCLE_STEP_MS), 0, 1, 0));
}

static void test_cycle_restart_at_red() {
  RgbCycle c(CYCLE_STEP_MS);
  c.start(0);
  CHECK(isColour(c.colour(900), 0, 0, 1));  // blue
  c.start(900);
  CHECK(isColour(c.colour(900), 1, 0, 0));
  c.stop();
  CHECK(!c.on());
  CHECK(isColour(c.colour(1000), 0, 0, 0));
}

// ---- Session rules ----

typedef DeviceSession<SESSION_ID_MAX_LEN> Session;

static Session newSession() { return Session(CYCLE_STEP_MS, SAFETY_TIMEOUT_MS); }

static void test_boot_state() {
  Session s = newSession();
  CHECK(s.sessionId() == nullptr);
  CHECK(!s.indicator());
  CHECK(!s.indicatorTimedOut());
  CHECK(isColour(s.colour(0), 0, 0, 0));
}

static void test_activate_sets_session_and_cycle() {
  Session s = newSession();
  CHECK(!s.apply(Command::Activate, "s-1", 1000));
  CHECK(sameId(s.sessionId(), "s-1"));
  CHECK(s.indicator());
  CHECK(isColour(s.colour(1000), 1, 0, 0));
  CHECK(isColour(s.colour(1400), 0, 1, 0));
}

static void test_activate_same_session_keeps_cycle_and_timer() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 1000);
  CHECK(!s.apply(Command::Activate, "s-1", 1900));
  CHECK(isColour(s.colour(1900), 0, 0, 1));  // still blue, not restarted
  CHECK(!s.update(1000 + SAFETY_TIMEOUT_MS - 1));
  CHECK(s.update(1000 + SAFETY_TIMEOUT_MS));  // timer from the first activate
}

static void test_activate_other_session_replaces_and_restarts() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 1000);
  CHECK(!s.apply(Command::Activate, "s-2", 1900));
  CHECK(sameId(s.sessionId(), "s-2"));
  CHECK(isColour(s.colour(1900), 1, 0, 0));
}

static void test_safety_timeout_keeps_session() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 1000);
  CHECK(!s.update(1000 + SAFETY_TIMEOUT_MS - 1));
  CHECK(s.indicator());
  CHECK(s.update(1000 + SAFETY_TIMEOUT_MS));
  CHECK(!s.indicator());
  CHECK(s.indicatorTimedOut());
  CHECK(sameId(s.sessionId(), "s-1"));
  CHECK(isColour(s.colour(1000 + SAFETY_TIMEOUT_MS), 0, 0, 0));
  CHECK(!s.update(1000 + SAFETY_TIMEOUT_MS + 5000));  // fires once
}

static void test_activate_after_timeout_restarts() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  s.update(SAFETY_TIMEOUT_MS);
  uint32_t later = SAFETY_TIMEOUT_MS + 777;
  // Same id after the safety timeout (e.g. a re-sync after reconnect): stays dark.
  CHECK(!s.apply(Command::Sync, "s-1", later));
  CHECK(!s.apply(Command::Activate, "s-1", later));
  CHECK(!s.indicator());
  CHECK(s.indicatorTimedOut());
  CHECK(strcmp(s.sessionId(), "s-1") == 0);
  // A different session lights up and gets a fresh timer.
  CHECK(!s.apply(Command::Activate, "s-2", later));
  CHECK(s.indicator());
  CHECK(!s.indicatorTimedOut());
  CHECK(isColour(s.colour(later), 1, 0, 0));
  CHECK(!s.update(later + SAFETY_TIMEOUT_MS - 1));
  CHECK(s.update(later + SAFETY_TIMEOUT_MS));
}

static void test_deactivate_matching() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  CHECK(!s.apply(Command::Deactivate, "s-1", 100));
  CHECK(s.sessionId() == nullptr);
  CHECK(!s.indicator());
  CHECK(isColour(s.colour(100), 0, 0, 0));
}

static void test_deactivate_other_session_ignored() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  CHECK(s.apply(Command::Deactivate, "s-old", 100));  // ignored: true
  CHECK(sameId(s.sessionId(), "s-1"));
  CHECK(s.indicator());
}

static void test_deactivate_when_holding_none() {
  Session s = newSession();
  CHECK(!s.apply(Command::Deactivate, "s-any", 100));
  CHECK(s.sessionId() == nullptr);
  CHECK(!s.indicator());
}

static void test_deactivate_clears_timed_out_flag() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  s.update(SAFETY_TIMEOUT_MS);
  CHECK(!s.apply(Command::Deactivate, "s-1", SAFETY_TIMEOUT_MS + 1));
  CHECK(!s.indicatorTimedOut());
  CHECK(s.sessionId() == nullptr);
}

static void test_sync_null_clears_any_session() {
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  CHECK(!s.apply(Command::Sync, nullptr, 100));
  CHECK(s.sessionId() == nullptr);
  CHECK(!s.indicator());
}

static void test_sync_string_acts_like_activate() {
  Session s = newSession();
  CHECK(!s.apply(Command::Sync, "s-1", 500));
  CHECK(sameId(s.sessionId(), "s-1"));
  CHECK(s.indicator());
  CHECK(isColour(s.colour(500), 1, 0, 0));
  CHECK(!s.apply(Command::Sync, "s-1", 1300));  // same id, on: keep cycle
  CHECK(isColour(s.colour(1300), 0, 0, 1));
}

static void test_session_survives_without_commands() {
  // Losing the client sends no command, so nothing changes until the timeout.
  Session s = newSession();
  s.apply(Command::Activate, "s-1", 0);
  CHECK(!s.update(60000));
  CHECK(s.indicator());
  CHECK(sameId(s.sessionId(), "s-1"));
}

static void test_session_id_validation() {
  char longId[SESSION_ID_MAX_LEN + 2];
  memset(longId, 'a', sizeof(longId));
  longId[SESSION_ID_MAX_LEN] = '\0';
  CHECK(isValidSessionId(longId, SESSION_ID_MAX_LEN));
  longId[SESSION_ID_MAX_LEN] = 'a';
  longId[SESSION_ID_MAX_LEN + 1] = '\0';
  CHECK(!isValidSessionId(longId, SESSION_ID_MAX_LEN));
  CHECK(!isValidSessionId("", SESSION_ID_MAX_LEN));
  CHECK(!isValidSessionId(nullptr, SESSION_ID_MAX_LEN));
  CHECK(isValidSessionId("s-20261030-070000-3f9c", SESSION_ID_MAX_LEN));
}

static void test_max_length_id_stored_intact() {
  char id[SESSION_ID_MAX_LEN + 1];
  memset(id, 'x', SESSION_ID_MAX_LEN);
  id[SESSION_ID_MAX_LEN] = '\0';
  Session s = newSession();
  s.apply(Command::Activate, id, 0);
  CHECK(sameId(s.sessionId(), id));
}

int main() {
  RUN(test_bounce_burst_is_one_tap);
  RUN(test_lockout_boundary);
  RUN(test_lockout_measured_from_accepted_tap);
  RUN(test_tapseq_increments);
  RUN(test_first_edge_at_time_zero_accepted);
  RUN(test_lockout_across_millis_wrap);
  RUN(test_sensor_stable_after_boot);
  RUN(test_sensor_unstable_after_edge);
  RUN(test_rejected_edge_still_disturbs);
  RUN(test_sensor_unstable_while_active_level);
  RUN(test_late_isr_edge_does_not_move_back_disturbance);
  RUN(test_other_edge_only_disturbs);
  RUN(test_cycle_off_is_dark);
  RUN(test_cycle_order_and_timing);
  RUN(test_cycle_restart_at_red);
  RUN(test_boot_state);
  RUN(test_activate_sets_session_and_cycle);
  RUN(test_activate_same_session_keeps_cycle_and_timer);
  RUN(test_activate_other_session_replaces_and_restarts);
  RUN(test_safety_timeout_keeps_session);
  RUN(test_activate_after_timeout_restarts);
  RUN(test_deactivate_matching);
  RUN(test_deactivate_other_session_ignored);
  RUN(test_deactivate_when_holding_none);
  RUN(test_deactivate_clears_timed_out_flag);
  RUN(test_sync_null_clears_any_session);
  RUN(test_sync_string_acts_like_activate);
  RUN(test_session_survives_without_commands);
  RUN(test_session_id_validation);
  RUN(test_max_length_id_stored_intact);
  printf("\n%d checks, %d failed\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
