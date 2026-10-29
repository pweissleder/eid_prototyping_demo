"""Storage: atomic alarms.json, append-only events.jsonl, CSV/summary export."""

from __future__ import annotations

import csv
import io
import json
import os

import pytest

from wakequest.models import Alarm
from wakequest.storage import (
    Storage,
    atomic_write_text,
    event_category,
    events_to_csv,
    sessions_summary,
    sessions_to_csv,
)

from .helpers import make_ready, new_machine, ring_study, tap


def _alarm(i: int) -> Alarm:
    return Alarm(id=f"a-{i}", name=f"A{i}", localTime="2026-10-30T07:00",
                 scheduledAt="2026-10-30T07:00:00.000+01:00", createdAt="2026-10-29T22:00:00.000+01:00")


def test_alarms_roundtrip(tmp_path):
    st = Storage(tmp_path / "data")
    assert st.load_alarms() == []
    st.save_alarms([_alarm(1), _alarm(2)])
    loaded = Storage(tmp_path / "data").load_alarms()
    assert [a.id for a in loaded] == ["a-1", "a-2"]
    assert json.loads(st.alarms_path.read_text())["version"] == 1
    assert not [p for p in st.data_dir.iterdir() if p.name.endswith(".tmp")]


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "alarms.json"
    p.write_text("OLD")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "NEW")
    assert p.read_text() == "OLD"
    assert [x.name for x in tmp_path.iterdir()] == ["alarms.json"]  # temp file cleaned up


def test_corrupt_alarms_file_is_backed_up(tmp_path):
    st = Storage(tmp_path)
    st.alarms_path.write_text("{not json")
    assert st.load_alarms() == []
    assert (tmp_path / "alarms.json.corrupt").exists()


def test_events_append_only_and_tolerate_torn_line(tmp_path):
    st = Storage(tmp_path)
    st.append_event({"ts": "t1", "event": "a"})
    st.append_event({"ts": "t2", "event": "b", "x": "ü"})
    with open(st.events_path, "a") as fh:
        fh.write('{"ts": "t3", "ev')  # crash mid-write
    evs = st.load_events()
    assert [e["event"] for e in evs] == ["a", "b"]
    st2 = Storage(tmp_path)
    st2.append_event({"ts": "t4", "event": "c"})
    assert st2.events_path.read_text().splitlines()[0] == '{"ts":"t1","event":"a"}'


def _run_log(clock):
    m = new_machine(clock)
    sid1 = ring_study(m, clock)
    clock.advance_sync(5)
    tap(m, clock, 1, sid1)
    sid2 = ring_study(m, clock, ack=False)
    clock.advance_sync(3)
    m.tick(clock.now())
    m.abort(clock.now())
    m.demo_reset(clock.now())
    m.schedule_test_alarm(clock.now())
    clock.advance_sync(10)
    m.tick(clock.now())
    sid3 = m.ringing.sessionId
    m.device_message(clock.now(), {"type": "ack", "of": "activate", "sessionId": sid3,
                                   "indicator": True, "ignored": False})
    tap(m, clock, 2, sid3)
    return m, sid1, sid2, sid3


def test_csv_contents(clock):
    m, sid1, sid2, sid3 = _run_log(clock)
    rows = list(csv.DictReader(io.StringIO(events_to_csv(m.events))))
    assert rows[0].keys() >= {"ts", "event", "sessionId", "mode", "runType", "category", "accepted",
                              "reason", "responseTimeMs", "points", "scheduledFor", "details"}
    done = [r for r in rows if r["event"] == "completed"]
    assert {r["sessionId"] for r in done} == {sid1, sid3}
    study_done = [r for r in done if r["sessionId"] == sid1][0]
    assert study_done["category"] == "successful_tap" and study_done["mode"] == "study"
    assert study_done["runType"] == "study" and study_done["points"] == "10"
    assert study_done["responseTimeMs"] == "5000"
    demo_done = [r for r in done if r["sessionId"] == sid3][0]
    assert demo_done["runType"] == "classroom_demo"
    aborted = [r for r in rows if r["event"] == "aborted"][0]
    assert aborted["sessionId"] == sid2 and aborted["category"] == "manual_abort" and aborted["points"] == "0"
    missing = [r for r in rows if r["event"] == "activation_missing"][0]
    assert missing["category"] == "technical_failure"
    taps = [r for r in rows if r["event"] == "tap_received"]
    assert taps[0]["accepted"] == "true"
    # study-only export excludes demo rows
    study_rows = list(csv.DictReader(io.StringIO(events_to_csv(m.events, "study"))))
    assert study_rows and all(r["mode"] == "study" for r in study_rows)
    assert sid3 not in {r["sessionId"] for r in study_rows}


def test_sessions_summary(clock):
    m, sid1, sid2, sid3 = _run_log(clock)
    rows = {r["sessionId"]: r for r in sessions_summary(m.events)}
    assert rows[sid1]["outcome"] == "successful_tap" and rows[sid1]["points"] == 10
    assert rows[sid1]["responseTimeMs"] == 5000
    assert rows[sid2]["outcome"] == "manual_abort" and rows[sid2]["points"] == 0
    assert "activation_missing" in rows[sid2]["technicalIssues"]
    assert rows[sid3]["runType"] == "classroom_demo" and rows[sid3]["pointsArePreview"] is True
    assert [r["sessionId"] for r in sessions_summary(m.events, "study")] == [sid1, sid2]
    text = sessions_to_csv(sessions_summary(m.events))
    assert text.splitlines()[0].startswith("sessionId,mode,runType")
    assert "activation_missing" in text


def test_event_category():
    assert event_category({"event": "aborted", "reason": "service_shutdown"}) == "technical_failure"
    assert event_category({"event": "aborted", "reason": "manual"}) == "manual_abort"
    assert event_category({"event": "connection_lost"}) == "technical_failure"
    assert event_category({"event": "alarm_created"}) == ""


def test_service_restart_preserves_alarms_and_points(tmp_path, clock):
    st = Storage(tmp_path)
    m = new_machine(clock)
    make_ready(m, clock)
    sid = ring_study(m, clock)
    tap(m, clock, 1, sid)
    for e in m.events:
        st.append_event(e)
    st.save_alarms(m.alarms_list())
    m2 = new_machine(clock, alarms=st.load_alarms(), events=st.load_events())
    assert m2.study_points() == (10, 1)
    assert [a.id for a in m2.alarms_list()] == [a.id for a in m.alarms_list()]
