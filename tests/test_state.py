"""Unit tests for the pure state machine (wakequest/state.py), driven by FakeClock."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from wakequest.state import CommandError, Machine, derived_study_points

from .helpers import add_alarm, hello, local_str, make_ready, names, new_machine, ring_study, tap


# ---------------------------------------------------------------- FR-01 alarms
class TestAlarmValidation:
    def test_valid_alarm_is_saved_with_local_time_and_status(self, m, clock):
        t = clock.now() + timedelta(hours=1)
        eff = m.create_alarm(clock.now(), {"name": "  Morning  ", "localTime": local_str(t)})
        a = eff.result
        assert a.name == "Morning"
        assert a.status == "saved"
        assert a.localTime == local_str(t)
        assert a.scheduledAt == "2026-10-30T07:00:00.000+01:00"
        assert eff.persist_alarms and eff.push
        assert names(eff.events) == ["alarm_created"]
        assert m.snapshot(clock.now()).alarms[0].id == a.id

    @pytest.mark.parametrize("payload, needle", [
        ({"localTime": "2026-10-30T07:00"}, "name"),
        ({"name": "   ", "localTime": "2026-10-30T07:00"}, "name"),
        ({"name": 5, "localTime": "2026-10-30T07:00"}, "name"),
        ({"name": "x" * 61, "localTime": "2026-10-30T07:00"}, "too long"),
        ({"name": "A"}, "choose a date and time"),
        ({"name": "A", "localTime": ""}, "choose a date and time"),
        ({"name": "A", "localTime": "07:00"}, "must look like"),
        ({"name": "A", "localTime": "2026-10-30 07:00"}, "must look like"),
        ({"name": "A", "localTime": "2026-10-30T7:00"}, "must look like"),
        ({"name": "A", "localTime": "2026-10-30T07:00:00"}, "must look like"),
        ({"name": "A", "localTime": 123}, "must look like"),
        ({"name": "A", "localTime": "2026-02-30T07:00"}, "not a real date"),
        ({"name": "A", "localTime": "2026-10-30T25:00"}, "not a real date"),
        ({"name": "A", "localTime": "2026-10-29T07:00"}, "in the past"),
        ({"name": "A", "localTime": "2026-10-30T06:00"}, "in the past"),  # == now
    ])
    def test_invalid_alarm_rejected_with_message(self, m, clock, payload, needle):
        with pytest.raises(CommandError) as ei:
            m.create_alarm(clock.now(), payload)
        assert ei.value.status == 422
        assert needle in ei.value.message
        assert m.alarms == {}

    def test_non_object_payload_rejected(self, m, clock):
        with pytest.raises(CommandError) as ei:
            m.create_alarm(clock.now(), ["x"])
        assert ei.value.status == 422

    def test_name_of_exactly_60_chars_ok(self, m, clock):
        eff = m.create_alarm(clock.now(), {"name": "n" * 60, "localTime": "2026-10-30T07:00"})
        assert len(eff.result.name) == 60

    def test_multiple_alarms_kept_sorted(self, m, clock):
        b = add_alarm(m, clock, minutes=90, name="B")
        a = add_alarm(m, clock, minutes=30, name="A")
        assert [x.id for x in m.alarms_list()] == [a, b]

    def test_delete_armed_alarm_disarms(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock)
        m.arm_alarm(clock.now(), aid)
        eff = m.delete_alarm(clock.now(), aid)
        assert names(eff.events) == ["alarm_disarmed", "alarm_deleted"]
        assert m.armed_alarm is None and aid not in m.alarms
        with pytest.raises(CommandError) as ei:
            m.delete_alarm(clock.now(), aid)
        assert ei.value.status == 404


# ---------------------------------------------------------------- FR-03 arming
class TestArming:
    def test_arm_refused_when_nothing_ready(self, m, clock):
        aid = add_alarm(m, clock)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        e = ei.value
        assert e.status == 409 and e.code == "not_ready"
        assert [x["code"] for x in e.extra["missing"]] == ["device_not_connected", "audio_not_tested"]
        assert "Connect the device" in e.message and "test sound" in e.message
        assert m.armed_alarm is None

    def test_arm_refused_without_audio_test(self, m, clock):
        m.link_hello(clock.now(), hello())
        aid = add_alarm(m, clock)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        assert [x["code"] for x in ei.value.extra["missing"]] == ["audio_not_tested"]

    def test_arm_refused_when_sensor_not_stable(self, m, clock):
        make_ready(m, clock)
        m.device_message(clock.now(), {"type": "status", "sensorStable": False, "indicator": False,
                                       "indicatorTimedOut": False, "sessionId": None, "tapSeq": 0,
                                       "uptimeMs": 6000, "rssi": -50})
        aid = add_alarm(m, clock)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        assert [x["code"] for x in ei.value.extra["missing"]] == ["sensor_not_stable"]

    def test_arm_refused_when_device_lost(self, m, clock):
        make_ready(m, clock)
        m.link_lost(clock.now(), "test")
        aid = add_alarm(m, clock)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        assert "device_not_connected" in [x["code"] for x in ei.value.extra["missing"]]

    def test_audio_test_resets_when_browser_disconnects(self, m, clock):
        make_ready(m, clock)
        assert m.readiness().canArm
        m.browser_disconnected(clock.now(), "b1")
        r = m.readiness()
        assert not r.audioTested and not r.canArm

    def test_audio_test_failed_clears_audio(self, m, clock):
        make_ready(m, clock)
        m.browser_message(clock.now(), "b1", {"type": "audio_test_failed", "error": "NotAllowedError"})
        assert not m.readiness().audioTested
        assert m.events[-1]["event"] == "audio_test_failed"

    def test_arm_ok_and_device_indicator_untouched(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock)
        eff = m.arm_alarm(clock.now(), aid)
        assert eff.device == []  # nothing is switched on while merely armed
        assert m.armed_alarm.id == aid
        assert m.run_state() == "armed"
        snap = m.snapshot(clock.now())
        assert snap.armedAlarm.id == aid and snap.run.state == "armed"

    def test_arming_another_replaces_previous(self, m, clock):
        make_ready(m, clock)
        a = add_alarm(m, clock, minutes=10, name="First")
        b = add_alarm(m, clock, minutes=20, name="Second")
        m.arm_alarm(clock.now(), a)
        eff = m.arm_alarm(clock.now(), b)
        assert eff.result["replacedAlarm"].id == a
        assert m.alarms[a].status == "saved" and m.alarms[b].status == "armed"
        assert sum(1 for x in m.alarms.values() if x.status == "armed") == 1
        change = m.snapshot(clock.now()).lastArmChange
        assert change.armedAlarmId == b and change.replacedAlarmId == a
        assert "replaces “First”" in change.message
        assert eff.events[-1]["replacedAlarmId"] == a

    def test_arm_past_alarm_refused(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        clock.advance_sync(120)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        assert ei.value.code == "alarm_in_past"

    def test_arm_unknown_alarm_404(self, m, clock):
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), "nope")
        assert ei.value.status == 404

    def test_disarm(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock)
        m.arm_alarm(clock.now(), aid)
        eff = m.disarm(clock.now())
        assert m.armed_alarm is None and names(eff.events) == ["alarm_disarmed"]
        assert m.run_state() == "idle"

    def test_startup_marks_overdue_armed_alarm_missed(self, clock):
        m1 = new_machine(clock)
        make_ready(m1, clock)
        aid = add_alarm(m1, clock, minutes=1)
        m1.arm_alarm(clock.now(), aid)
        clock.advance_sync(600)
        m2 = new_machine(clock, alarms=m1.alarms_list(), events=m1.events)
        eff = m2.startup(clock.now())
        assert m2.alarms[aid].status == "missed"
        assert "alarm_missed" in names(eff.events) and eff.persist_alarms


# ---------------------------------------------------------------- FR-04 ringing
class TestRinging:
    def test_alarm_rings_at_time_and_sends_activate(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        m.arm_alarm(clock.now(), aid)
        clock.advance_sync(59)
        assert m.tick(clock.now()).events == []
        assert m.run_state() == "armed"
        clock.advance_sync(1)
        eff = m.tick(clock.now())
        s = m.ringing
        assert s is not None and s.mode == "study"
        assert names(eff.events) == ["ring_started", "activation_sent"]
        assert eff.device == [{"type": "activate", "sessionId": s.sessionId}]
        assert eff.events[0]["scheduledFor"] == m.alarms[aid].scheduledAt
        assert eff.events[0]["lateMs"] == 0
        assert all(e["sessionId"] == s.sessionId and e["mode"] == "study" for e in eff.events)
        # alarm consumed
        assert m.alarms[aid].status == "rang" and m.alarms[aid].sessionId == s.sessionId
        assert m.armed_alarm is None and eff.persist_alarms
        assert m.snapshot(clock.now()).run.state == "ringing"

    def test_session_ids_unique_and_short(self, m, clock):
        ids = set()
        for _ in range(5):
            sid = ring_study(m, clock)
            assert 1 <= len(sid) <= 64
            ids.add(sid)
            m.abort(clock.now())
        assert len(ids) == 5

    def test_ack_within_timeout_marks_acked(self, m, clock):
        sid = ring_study(m, clock, ack=False, sound=False)
        clock.advance_sync(1.5)
        eff = m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid,
                                             "indicator": True, "ignored": False})
        assert names(eff.events) == ["activation_acked"]
        clock.advance_sync(5)
        assert m.tick(clock.now()).events == []
        assert m.ringing.activationStatus == "acked"

    def test_missing_ack_flagged_after_2s_once(self, m, clock):
        ring_study(m, clock, ack=False, sound=False)
        clock.advance_sync(1.9)
        assert m.tick(clock.now()).events == []
        clock.advance_sync(0.1)
        eff = m.tick(clock.now())
        assert names(eff.events) == ["activation_missing"] and eff.push
        assert m.ringing.activationStatus == "missing"
        assert "activation_missing" in m.snapshot(clock.now()).run.session.technicalIssues
        assert m.tick(clock.now()).events == []  # logged once

    def test_late_ack_recovers(self, m, clock):
        sid = ring_study(m, clock, ack=False, sound=False)
        clock.advance_sync(3)
        m.tick(clock.now())
        eff = m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid,
                                             "indicator": True, "ignored": False})
        assert eff.events[0]["late"] is True
        assert m.ringing.activationStatus == "acked"

    def test_ack_for_other_session_or_indicator_off_not_counted(self, m, clock):
        sid = ring_study(m, clock, ack=False, sound=False)
        m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": "other",
                                       "indicator": True, "ignored": False})
        m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid,
                                       "indicator": False, "ignored": False})
        assert m.ringing.activationStatus == "sent"

    def test_ring_while_device_disconnected_flags_activation_missing(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        m.arm_alarm(clock.now(), aid)
        m.link_lost(clock.now(), "gone")
        clock.advance_sync(60)
        eff = m.tick(clock.now())
        assert eff.device == []
        assert names(eff.events) == ["ring_started", "activation_missing"]
        assert eff.events[1]["reason"] == "device_not_connected"
        assert m.run_state() == "ringing"  # sound still rings in the browser

    def test_sound_started_and_failed(self, m, clock):
        sid = ring_study(m, clock, sound=False)
        eff = m.browser_message(clock.now(), "b1", {"type": "sound_failed", "sessionId": sid,
                                                     "error": "NotAllowedError"})
        assert names(eff.events) == ["sound_failed"]
        assert m.ringing.soundStatus == "failed"
        eff = m.browser_message(clock.now(), "b1", {"type": "sound_started", "sessionId": sid})
        assert names(eff.events) == ["sound_started"]
        assert m.browser_message(clock.now(), "b1", {"type": "sound_started", "sessionId": sid}).events == []

    def test_sound_message_for_stale_session_rejected(self, m, clock):
        ring_study(m, clock, sound=False)
        with pytest.raises(CommandError) as ei:
            m.browser_message(clock.now(), "b1", {"type": "sound_started", "sessionId": "old"})
        assert ei.value.code == "stale_session"
        assert m.ringing.soundStatus == "pending"

    def test_unknown_browser_message(self, m, clock):
        with pytest.raises(CommandError):
            m.browser_message(clock.now(), "b1", {"type": "dance"})
        with pytest.raises(CommandError):
            m.browser_message(clock.now(), "b1", "nope")

    def test_arm_refused_while_ringing(self, m, clock):
        ring_study(m, clock)
        aid = add_alarm(m, clock, minutes=30)
        with pytest.raises(CommandError) as ei:
            m.arm_alarm(clock.now(), aid)
        assert ei.value.code == "run_active"


# ---------------------------------------------------------------- FR-05 taps
class TestTaps:
    def test_idle_tap_ignored(self, m, clock):
        m.link_hello(clock.now(), hello())
        eff = tap(m, clock, 1, None)
        ev = eff.events[0]
        assert ev["event"] == "tap_received" and ev["accepted"] is False and ev["reason"] == "idle"
        assert m.snapshot(clock.now()).device.lastTap.reason == "idle"

    def test_armed_tap_ignored_and_cannot_precomplete(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        m.arm_alarm(clock.now(), aid)
        assert tap(m, clock, 1, None).events[0]["reason"] == "armed"
        clock.advance_sync(60)
        m.tick(clock.now())
        assert m.run_state() == "ringing"  # earlier tap did not pre-complete

    def test_tap_before_ack_is_not_activated(self, m, clock):
        sid = ring_study(m, clock, ack=False)
        ev = tap(m, clock, 1, sid).events[0]
        assert ev["reason"] == "not_activated" and m.run_state() == "ringing"

    def test_tap_with_wrong_or_null_session_is_stale(self, m, clock):
        ring_study(m, clock)
        assert tap(m, clock, 1, "s-old").events[0]["reason"] == "stale_session"
        assert tap(m, clock, 2, None).events[0]["reason"] == "stale_session"
        assert m.run_state() == "ringing"

    def test_valid_tap_completes_once_with_reward(self, m, clock):
        sid = ring_study(m, clock)  # sound confirmed 0.3 s after ring
        clock.advance_sync(4.2)
        eff = tap(m, clock, 1, sid)
        assert names(eff.events) == ["tap_received", "completed", "deactivation_sent"]
        assert eff.events[0]["accepted"] is True and eff.events[0]["reason"] is None
        done = eff.events[1]
        assert done["points"] == 10 and done["responseTimeMs"] == 4200 and done["pointsArePreview"] is False
        assert eff.device == [{"type": "deactivate", "sessionId": sid, "reason": "completed"}]
        snap = m.snapshot(clock.now())
        assert snap.run.state == "completed"
        assert snap.run.session.result.points == 10
        assert snap.run.session.result.responseTimeMs == 4200
        assert snap.points.study == 10

    def test_duplicate_seq_and_post_completion_taps_never_reward_twice(self, m, clock):
        sid = ring_study(m, clock)
        tap(m, clock, 1, sid)
        assert tap(m, clock, 1, sid).events[0]["reason"] == "duplicate"   # repeated seq
        assert tap(m, clock, 2, sid).events[0]["reason"] == "duplicate"   # new seq, already completed
        assert names(m.events).count("completed") == 1
        assert m.study_points() == (10, 1)

    def test_duplicate_seq_during_ringing(self, m, clock):
        sid = ring_study(m, clock, ack=False)
        tap(m, clock, 5, sid)  # not_activated, but seq 5 consumed
        m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        assert tap(m, clock, 5, sid).events[0]["reason"] == "duplicate"
        assert tap(m, clock, 6, sid).events[0]["accepted"] is True

    def test_device_reboot_resets_seq_tracking(self, m, clock):
        sid = ring_study(m, clock)
        tap(m, clock, 7, "stale", uptime=50_000)
        # reboot: uptime goes backwards, seq starts again at 1
        eff = tap(m, clock, 1, sid, uptime=900)
        assert "device_rebooted" in names(eff.events)
        tr = [e for e in eff.events if e["event"] == "tap_received"][0]
        assert tr["accepted"] is True
        assert m.run_state() == "completed"

    def test_tap_after_abort_is_stale_and_no_points(self, m, clock):
        sid = ring_study(m, clock)
        m.abort(clock.now())
        assert tap(m, clock, 1, sid).events[0]["reason"] == "stale_session"
        assert m.study_points() == (0, 0)

    def test_previous_session_tap_cannot_complete_next_session(self, m, clock):
        sid1 = ring_study(m, clock)
        m.abort(clock.now())
        sid2 = ring_study(m, clock)
        assert sid1 != sid2
        assert tap(m, clock, 3, sid1).events[0]["reason"] == "stale_session"
        assert m.run_state() == "ringing"

    def test_response_time_null_without_sound_confirmation(self, m, clock):
        sid = ring_study(m, clock, sound=False)
        eff = tap(m, clock, 1, sid)
        done = eff.events[1]
        assert done["responseTimeMs"] is None and done["responseTimeFlag"] == "sound_not_confirmed"
        assert m.snapshot(clock.now()).run.session.result.responseTimeFlag == "sound_not_confirmed"
        assert done["points"] == 10


# ---------------------------------------------------------------- FR-06 points
class TestPoints:
    def test_points_derived_from_log_not_incremented(self):
        evs = [
            {"event": "completed", "sessionId": "a", "mode": "study"},
            {"event": "completed", "sessionId": "a", "mode": "study"},  # duplicate row
            {"event": "completed", "sessionId": "b", "mode": "study"},
            {"event": "completed", "sessionId": "d", "mode": "demo"},
            {"event": "aborted", "sessionId": "c", "mode": "study"},
        ]
        assert derived_study_points(evs) == (20, 2)

    def test_points_survive_restart(self, m, clock):
        sid = ring_study(m, clock)
        tap(m, clock, 1, sid)
        m2 = new_machine(clock, events=m.events)
        assert m2.snapshot(clock.now()).points.study == 10

    def test_no_points_field_is_stored(self, m):
        assert not hasattr(m, "points")


# ---------------------------------------------------------------- FR-08 presenter
class TestPresenter:
    def test_abort_ringing_run(self, m, clock):
        sid = ring_study(m, clock)
        eff = m.abort(clock.now())
        assert names(eff.events) == ["aborted", "deactivation_sent"]
        assert eff.events[0]["reason"] == "manual" and eff.events[0]["points"] == 0
        assert eff.device == [{"type": "deactivate", "sessionId": sid, "reason": "aborted"}]
        snap = m.snapshot(clock.now())
        assert snap.run.state == "aborted" and snap.run.session.result.points == 0
        assert snap.points.study == 0

    def test_abort_with_nothing_running(self, m, clock):
        with pytest.raises(CommandError) as ei:
            m.abort(clock.now())
        assert ei.value.status == 409

    def test_test_alarm_requires_readiness(self, m, clock):
        with pytest.raises(CommandError) as ei:
            m.schedule_test_alarm(clock.now())
        assert ei.value.code == "not_ready"

    def test_test_alarm_flow_is_demo_and_does_not_touch_study(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=60)
        m.arm_alarm(clock.now(), aid)
        eff = m.schedule_test_alarm(clock.now())
        pt = eff.result
        assert names(eff.events) == ["test_alarm_scheduled"] and eff.events[0]["mode"] == "demo"
        assert m.run_state() == "test_pending"
        assert (pt.fireAt - clock.now()).total_seconds() == 10
        assert tap(m, clock, 1, None).events[0]["reason"] == "armed"
        clock.advance_sync(9.8)
        m.tick(clock.now())
        assert m.ringing is None
        clock.advance_sync(0.2)
        eff = m.tick(clock.now())
        s = m.ringing
        assert s.sessionId == pt.sessionId and s.mode == "demo" and s.alarmName == "Test alarm"
        assert eff.device == [{"type": "activate", "sessionId": s.sessionId}]
        m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": s.sessionId,
                                       "indicator": True, "ignored": False})
        m.browser_message(clock.now(), "b1", {"type": "sound_started", "sessionId": s.sessionId})
        clock.advance_sync(3)
        eff = tap(m, clock, 2, s.sessionId)
        done = [e for e in eff.events if e["event"] == "completed"][0]
        assert done["mode"] == "demo" and done["pointsArePreview"] is True and done["points"] == 10
        snap = m.snapshot(clock.now())
        assert snap.run.session.result.pointsArePreview is True
        assert snap.points.study == 0  # demo never changes the study total
        assert m.alarms[aid].status == "armed"  # study schedule untouched

    def test_second_test_alarm_refused_while_active(self, m, clock):
        make_ready(m, clock)
        m.schedule_test_alarm(clock.now())
        with pytest.raises(CommandError) as ei:
            m.schedule_test_alarm(clock.now())
        assert ei.value.code == "run_active"
        clock.advance_sync(10)
        m.tick(clock.now())
        with pytest.raises(CommandError):
            m.schedule_test_alarm(clock.now())

    def test_demo_reset_clears_active_demo_keeps_armed_study(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=60)
        m.arm_alarm(clock.now(), aid)
        m.schedule_test_alarm(clock.now())
        clock.advance_sync(10)
        m.tick(clock.now())
        sid = m.ringing.sessionId
        eff = m.demo_reset(clock.now())
        assert names(eff.events) == ["aborted", "deactivation_sent", "demo_reset"]
        assert eff.events[0]["reason"] == "reset"
        assert eff.device == [{"type": "deactivate", "sessionId": sid, "reason": "reset"}]
        assert m.session is None and m.run_state() == "armed"
        m.schedule_test_alarm(clock.now())  # ready for the next run immediately

    def test_demo_reset_cancels_pending_and_clears_finished(self, m, clock):
        make_ready(m, clock)
        m.schedule_test_alarm(clock.now())
        eff = m.demo_reset(clock.now())
        assert names(eff.events) == ["test_alarm_cancelled", "demo_reset"]
        assert m.run_state() == "idle"
        sid = ring_study(m, clock)
        tap(m, clock, 1, sid)
        assert m.run_state() == "completed"
        m.demo_reset(clock.now())
        assert m.run_state() == "idle"
        assert m.study_points() == (10, 1)  # reset never removes earned study points

    def test_demo_reset_refused_for_ringing_study(self, m, clock):
        ring_study(m, clock)
        with pytest.raises(CommandError) as ei:
            m.demo_reset(clock.now())
        assert ei.value.code == "study_run_active"

    def test_abort_pending_test(self, m, clock):
        make_ready(m, clock)
        m.schedule_test_alarm(clock.now())
        eff = m.abort(clock.now())
        assert names(eff.events) == ["test_alarm_cancelled"]
        assert m.run_state() == "idle"

    def test_study_alarm_preempts_demo(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        m.arm_alarm(clock.now(), aid)
        clock.advance_sync(55)
        m.schedule_test_alarm(clock.now())
        clock.advance_sync(5)
        eff = m.tick(clock.now())
        assert "test_alarm_cancelled" in names(eff.events)
        assert m.ringing.mode == "study"

    def test_ringing_demo_preempted_by_study(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock, minutes=1)
        m.arm_alarm(clock.now(), aid)
        clock.advance_sync(45)
        m.schedule_test_alarm(clock.now())
        clock.advance_sync(10)
        m.tick(clock.now())
        demo = m.ringing.sessionId
        clock.advance_sync(5)
        eff = m.tick(clock.now())
        aborted = [e for e in eff.events if e["event"] == "aborted"][0]
        assert aborted["sessionId"] == demo and aborted["reason"] == "preempted_by_study_alarm"
        assert m.ringing.mode == "study"


# ---------------------------------------------------------------- FR-02/FR-09 connection
class TestConnection:
    def test_connected_only_after_hello(self, m, clock):
        m.connect_requested(clock.now())
        assert m.device.status == "connecting"
        m.link_attempt(clock.now(), reconnecting=False)
        assert m.snapshot(clock.now()).device.status == "connecting"
        eff = m.link_hello(clock.now(), hello(deviceId="wq-0a0b0c"))
        d = m.snapshot(clock.now()).device
        assert d.status == "connected" and d.deviceId == "wq-0a0b0c" and d.lastError is None
        assert eff.device == [{"type": "sync", "sessionId": None}]
        assert names(eff.events) == ["device_connected", "sync_sent"]

    def test_connect_failure_shows_error(self, m, clock):
        m.connect_requested(clock.now())
        eff = m.link_failed(clock.now(), "Could not find the device at wakequest.local.")
        d = m.snapshot(clock.now()).device
        assert d.status == "disconnected" and "Could not find" in d.lastError
        assert names(eff.events) == ["device_connect_failed"]
        m.connect_requested(clock.now())  # retry possible
        assert m.device.status == "connecting" and m.device.lastError is None

    def test_loss_during_ringing_reported_never_completion(self, m, clock):
        sid = ring_study(m, clock)
        eff = m.link_lost(clock.now(), "no message from the device for 3 s")
        assert names(eff.events) == ["connection_lost"] and eff.events[0]["sessionId"] == sid
        snap = m.snapshot(clock.now())
        assert snap.device.status == "reconnecting" and "lost" in snap.device.lastError
        assert snap.run.state == "ringing"  # sound keeps going
        assert snap.run.session.connectionLostDuringRun is True
        assert snap.points.study == 0

    def test_reconnect_syncs_current_session_without_reaward(self, m, clock):
        sid = ring_study(m, clock)
        m.link_lost(clock.now(), "drop")
        clock.advance_sync(1)
        eff = m.link_hello(clock.now(), hello(sessionId=sid, indicator=True, uptimeMs=99_000))
        assert eff.device == [{"type": "sync", "sessionId": sid}]
        assert names(eff.events)[0] == "connection_restored" and eff.events[0]["downMs"] == 1000
        m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        tap(m, clock, 1, sid, uptime=99_100)
        assert m.run_state() == "completed"
        # another drop + reconnect after completion: sync null, still exactly one award
        m.link_lost(clock.now(), "drop")
        eff = m.link_hello(clock.now(), hello(sessionId=None, uptimeMs=99_500))
        assert eff.device == [{"type": "sync", "sessionId": None}]
        tap(m, clock, 1, sid, uptime=99_600)  # replayed tap
        assert names(m.events).count("completed") == 1
        assert m.study_points() == (10, 1)

    def test_reboot_during_ringing_resyncs(self, m, clock):
        sid = ring_study(m, clock)
        tap(m, clock, 3, "x", uptime=60_000)
        m.link_lost(clock.now(), "power")
        eff = m.link_hello(clock.now(), hello(sessionId=None, tapSeq=0, uptimeMs=1200))
        assert "device_rebooted" in names(eff.events)
        assert eff.device == [{"type": "sync", "sessionId": sid}]
        m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        assert tap(m, clock, 1, sid, uptime=1500).events[0]["accepted"] is True

    def test_long_outage_reboot_detected_by_tapseq(self, m, clock):
        """Rebooted board offline longer than its old uptime: uptime looks larger,
        but hello.tapSeq < last tap seq still proves the reboot."""
        sid = ring_study(m, clock, ack=False)
        tap(m, clock, 3, sid, uptime=60_000)  # not_activated, seq 3 seen
        m.link_lost(clock.now(), "power")
        eff = m.link_hello(clock.now(), hello(sessionId=None, tapSeq=0, uptimeMs=200_000))
        reboot = [e for e in eff.events if e["event"] == "device_rebooted"]
        assert reboot and reboot[0]["detectedBy"] == "tapSeq"
        m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        assert tap(m, clock, 1, sid, uptime=200_500).events[0]["accepted"] is True

    def test_same_boot_reconnect_keeps_duplicate_protection(self, m, clock):
        sid = ring_study(m, clock, ack=False)
        assert tap(m, clock, 1, sid, uptime=50_100).events[0]["reason"] == "not_activated"
        m.link_lost(clock.now(), "drop")
        eff = m.link_hello(clock.now(), hello(sessionId=sid, indicator=True, tapSeq=1, uptimeMs=51_000))
        assert "device_rebooted" not in names(eff.events)
        m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        assert m.run_state() == "ringing"
        assert tap(m, clock, 1, sid, uptime=51_100).events[0]["reason"] == "duplicate"  # replay
        assert tap(m, clock, 2, sid, uptime=51_200).events[0]["accepted"] is True

    def test_post_timeout_resync_ack_off_is_not_a_failure(self, m, clock):
        sid = ring_study(m, clock)
        m.device_message(clock.now(), {"type": "status", "sensorStable": True, "indicator": False,
                                       "indicatorTimedOut": True, "sessionId": sid, "tapSeq": 0,
                                       "uptimeMs": 900_000, "rssi": -60})
        assert m.events[-1]["event"] == "indicator_timed_out"
        m.link_lost(clock.now(), "drop")
        m.link_hello(clock.now(), hello(sessionId=sid, indicator=False, uptimeMs=901_000))
        eff = m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                             "indicator": False, "ignored": False})
        clock.advance_sync(5)
        eff.merge(m.tick(clock.now()))
        assert "activation_missing" not in names(eff.events)
        assert m.ringing.activationStatus == "acked"
        assert tap(m, clock, 1, sid, uptime=902_000).events[0]["accepted"] is True

    def test_lost_before_ack_then_sync_ack_counts(self, m, clock):
        sid = ring_study(m, clock, ack=False)
        m.link_lost(clock.now(), "drop")
        m.link_hello(clock.now(), hello(sessionId=sid, indicator=True))
        m.device_message(clock.now(), {"type": "ack", "of": "sync", "sessionId": sid,
                                       "indicator": True, "ignored": False})
        assert m.ringing.activationStatus == "acked"

    def test_indicator_on_while_not_ringing_is_corrected(self, m, clock):
        make_ready(m, clock)
        aid = add_alarm(m, clock)
        m.arm_alarm(clock.now(), aid)
        clock.advance_sync(3)
        status = {"type": "status", "sensorStable": True, "indicator": True, "indicatorTimedOut": False,
                  "sessionId": "s-stale", "tapSeq": 0, "uptimeMs": 7000, "rssi": -50}
        eff = m.device_message(clock.now(), status)
        assert eff.device == [{"type": "sync", "sessionId": None}]
        assert m.device_message(clock.now(), status).device == []  # rate-limited
        clock.advance_sync(2)
        assert m.device_message(clock.now(), status).device == [{"type": "sync", "sessionId": None}]

    def test_device_error_logged(self, m, clock):
        m.link_hello(clock.now(), hello())
        eff = m.device_message(clock.now(), {"type": "error", "code": "bad_field", "detail": "x"})
        assert names(eff.events) == ["device_error"]

    def test_user_disconnect(self, m, clock):
        m.link_hello(clock.now(), hello())
        eff = m.disconnect_requested(clock.now())
        assert m.device.status == "disconnected" and names(eff.events) == ["device_disconnected"]


# ---------------------------------------------------------------- FR-07 logging
class TestLogging:
    def test_full_run_logs_every_fr07_event(self, m, clock):
        sid = ring_study(m, clock)
        clock.advance_sync(2)
        tap(m, clock, 1, sid)
        run = [e for e in m.events if e["sessionId"] == sid]
        assert names(run) == ["ring_started", "activation_sent", "activation_acked", "sound_started",
                              "tap_received", "completed", "deactivation_sent"]
        assert all(e["mode"] == "study" for e in run)
        assert run[0]["scheduledFor"] and run[-2]["scheduledFor"] == run[0]["scheduledFor"]
        for e in m.events:
            datetime.fromisoformat(e["ts"])  # ISO 8601 with offset
            assert e["ts"].endswith("+01:00")

    def test_snapshot_rev_increments(self, m, clock):
        a = m.snapshot(clock.now()).rev
        assert m.snapshot(clock.now()).rev == a + 1
        assert m.snapshot(clock.now(), bump=False).rev == a + 1

    def test_machine_is_pure(self):
        import wakequest.state as st
        src = open(st.__file__).read()
        for forbidden in ("import asyncio", "open(", "import os", "websockets"):
            assert forbidden not in src
        assert isinstance(Machine(), Machine)
