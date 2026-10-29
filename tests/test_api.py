"""API tests: REST + /ws through FastAPI's TestClient, fake device in-process."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from wakequest.api import create_app

from .helpers import fast_settings, wait_sync


def future(minutes: int = 30) -> str:
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")


@pytest.fixture
def settings(tmp_path):
    return fast_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def state(c: TestClient) -> dict:
    return c.get("/api/state").json()


def connect(c: TestClient) -> None:
    r = c.post("/api/device/connect")
    assert r.status_code == 202
    assert r.json()["device"]["status"] in ("connecting", "connected")
    wait_sync(lambda: state(c)["device"]["status"] == "connected")


def recv_until(ws, pred, limit: int = 200) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == "snapshot" and pred(msg):
            return msg
    raise AssertionError("snapshot not received")


def ready(c: TestClient, ws) -> None:
    connect(c)
    ws.send_json({"type": "audio_test_ok"})
    recv_until(ws, lambda s: s["readiness"]["canArm"])


# ---------------------------------------------------------------- basics
def test_state_snapshot_shape(client):
    s = state(client)
    assert s["type"] == "snapshot"
    assert s.keys() >= {"rev", "serverTime", "timezone", "testMode", "device", "audio", "readiness",
                        "alarms", "armedAlarm", "lastArmChange", "run", "points"}
    assert s["run"] == {"state": "idle", "session": None, "testAlarm": None}
    assert s["device"]["status"] == "disconnected"
    assert s["points"] == {"study": 0, "studySessionsCompleted": 0, "perCompletion": 10}
    assert s["testMode"] is True
    datetime.fromisoformat(s["serverTime"])


# ---------------------------------------------------------------- FR-01
def test_create_and_list_alarm(client):
    r = client.post("/api/alarms", json={"name": "Morning", "localTime": future()})
    assert r.status_code == 201
    a = r.json()["alarm"]
    assert a["status"] == "saved" and a["name"] == "Morning"
    assert client.get("/api/alarms").json()["alarms"][0]["id"] == a["id"]
    assert state(client)["alarms"][0]["id"] == a["id"]


@pytest.mark.parametrize("body, needle", [
    ({"localTime": "2030-01-01T07:00"}, "name"),
    ({"name": "x"}, "date and time"),
    ({"name": "x", "localTime": "tomorrow"}, "must look like"),
    ({"name": "x", "localTime": "2020-01-01T07:00"}, "in the past"),
])
def test_invalid_alarm_422_with_message(client, body, needle):
    r = client.post("/api/alarms", json=body)
    assert r.status_code == 422
    j = r.json()
    assert j["error"] == "invalid_alarm" and needle in j["message"]


def test_non_json_body_422(client):
    r = client.post("/api/alarms", content=b"{oops", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "JSON" in r.json()["message"]
    r = client.post("/api/alarms")
    assert r.status_code == 422


def test_alarms_persist_across_restart(settings):
    with TestClient(create_app(settings)) as c:
        aid = c.post("/api/alarms", json={"name": "Keep", "localTime": future()}).json()["alarm"]["id"]
    with TestClient(create_app(settings)) as c:
        assert [a["id"] for a in state(c)["alarms"]] == [aid]


def test_delete_alarm(client):
    aid = client.post("/api/alarms", json={"name": "Del", "localTime": future()}).json()["alarm"]["id"]
    assert client.delete(f"/api/alarms/{aid}").status_code == 200
    assert client.delete(f"/api/alarms/{aid}").status_code == 404


# ---------------------------------------------------------------- FR-02
def test_connect_shows_connected_with_device_id(client):
    connect(client)
    d = state(client)["device"]
    assert d["deviceId"] == "wq-fa4e01" and d["sensorStable"] is True and d["lastError"] is None


def test_disconnect(client):
    connect(client)
    r = client.post("/api/device/disconnect")
    assert r.json()["device"]["status"] == "disconnected"


# ---------------------------------------------------------------- FR-03 + /ws
def test_ws_snapshot_on_connect_and_audio_gate(client):
    aid = client.post("/api/alarms", json={"name": "A", "localTime": future()}).json()["alarm"]["id"]
    r = client.post(f"/api/alarms/{aid}/arm")
    assert r.status_code == 409
    assert {m["code"] for m in r.json()["missing"]} == {"device_not_connected", "audio_not_tested"}
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["audio"]["browsersConnected"] == 1
        connect(client)
        r = client.post(f"/api/alarms/{aid}/arm")
        assert r.status_code == 409 and [m["code"] for m in r.json()["missing"]] == ["audio_not_tested"]
        ws.send_json({"type": "audio_test_ok"})
        recv_until(ws, lambda s: s["audio"]["tested"])
        r = client.post(f"/api/alarms/{aid}/arm")
        assert r.status_code == 200 and r.json()["armedAlarm"]["status"] == "armed"
        snap = recv_until(ws, lambda s: s["run"]["state"] == "armed")
        assert snap["armedAlarm"]["id"] == aid
    # browser gone → audio test no longer valid
    wait_sync(lambda: state(client)["audio"]["tested"] is False)
    assert state(client)["readiness"]["canArm"] is False


def test_sensor_unstable_blocks_arming(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        client.post("/api/_test/fake-device/sensor-unstable")
        wait_sync(lambda: state(client)["device"]["sensorStable"] is False)
        aid = client.post("/api/alarms", json={"name": "A", "localTime": future()}).json()["alarm"]["id"]
        r = client.post(f"/api/alarms/{aid}/arm")
        assert r.status_code == 409 and r.json()["missing"][0]["code"] == "sensor_not_stable"
        client.post("/api/_test/fake-device/sensor-stable")
        wait_sync(lambda: state(client)["device"]["sensorStable"] is True)
        assert client.post(f"/api/alarms/{aid}/arm").status_code == 200


def test_arm_replacement_reported(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        a = client.post("/api/alarms", json={"name": "First", "localTime": future(30)}).json()["alarm"]["id"]
        b = client.post("/api/alarms", json={"name": "Second", "localTime": future(60)}).json()["alarm"]["id"]
        client.post(f"/api/alarms/{a}/arm")
        r = client.post(f"/api/alarms/{b}/arm").json()
        assert r["replacedAlarm"]["id"] == a and r["armedAlarm"]["id"] == b
        s = state(client)
        assert s["lastArmChange"]["replacedAlarmId"] == a
        assert [x["status"] for x in s["alarms"]] == ["saved", "armed"]
        assert client.post("/api/alarms/disarm").json()["disarmedAlarm"]["id"] == b


def test_ws_bad_messages_get_errors(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("nope")
        assert ws.receive_json()["error"] == "bad_json"
        ws.send_json({"type": "dance"})
        err = ws.receive_json()
        assert err["type"] == "error" and err["error"] == "unknown_type"
        ws.send_json({"type": "sound_started", "sessionId": "zzz"})
        assert ws.receive_json()["error"] == "stale_session"
        ws.send_json({"type": "get_snapshot"})
        assert ws.receive_json()["type"] == "snapshot"


# ---------------------------------------------------------------- FR-04..FR-08 end to end
def test_test_alarm_full_flow_and_reset(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        r = client.post("/api/test-alarm")
        assert r.status_code == 202 and r.json()["delayS"] == pytest.approx(0.2)
        assert client.post("/api/test-alarm").status_code == 409  # one run at a time
        snap = recv_until(ws, lambda s: s["run"]["state"] == "ringing")
        sess = snap["run"]["session"]
        sid = sess["sessionId"]
        assert sess["mode"] == "demo" and sess["sound"]["status"] == "pending"
        ws.send_json({"type": "sound_started", "sessionId": sid})
        recv_until(ws, lambda s: s["run"]["session"]["activation"]["status"] == "acked"
                   and s["run"]["session"]["sound"]["status"] == "started")
        assert client.get("/api/_test/fake-device").json()["indicator"] is True
        r = client.post("/api/_test/fake-device/tap", json={})
        assert r.json()["sent"] is True
        done = recv_until(ws, lambda s: s["run"]["state"] == "completed")
        res = done["run"]["session"]["result"]
        assert res["points"] == 10 and res["pointsArePreview"] is True
        assert isinstance(res["responseTimeMs"], int) and res["responseTimeMs"] >= 0
        assert done["points"]["study"] == 0
        wait_sync(lambda: client.get("/api/_test/fake-device").json()["indicator"] is False)
        # duplicate tap: no change
        client.post("/api/_test/fake-device/tap", json={"sessionId": sid})
        wait_sync(lambda: state(client)["device"]["lastTap"]["reason"] == "duplicate")
        assert state(client)["points"]["study"] == 0
        r = client.post("/api/demo/reset")
        assert r.status_code == 200 and r.json()["run"]["state"] == "idle"
        assert client.post("/api/test-alarm").status_code == 202  # next run prepared instantly


def test_abort_gives_no_reward(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        client.post("/api/test-alarm")
        recv_until(ws, lambda s: s["run"]["state"] == "ringing")
        r = client.post("/api/session/abort")
        assert r.status_code == 200
        run = r.json()["run"]
        assert run["state"] == "aborted" and run["session"]["result"]["points"] == 0
        assert run["session"]["abortReason"] == "manual"
        wait_sync(lambda: client.get("/api/_test/fake-device").json()["indicator"] is False)
        assert client.post("/api/session/abort").status_code == 409


def test_study_alarm_rings_and_completes(client, settings):
    """A real (minute-resolution) study alarm can't fire within a test, so arm it and
    move its due time forward in the machine; the scheduler fires it."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        aid = client.post("/api/alarms", json={"name": "Study", "localTime": future(5)}).json()["alarm"]["id"]
        client.post(f"/api/alarms/{aid}/arm")
        svc = client.app.state.service
        svc.machine.alarms[aid].scheduledAt = datetime.now().astimezone().isoformat(timespec="milliseconds")
        snap = recv_until(ws, lambda s: s["run"]["state"] == "ringing")
        sid = snap["run"]["session"]["sessionId"]
        assert snap["run"]["session"]["mode"] == "study" and snap["armedAlarm"] is None
        ws.send_json({"type": "sound_started", "sessionId": sid})
        recv_until(ws, lambda s: s["run"]["session"]["activation"]["status"] == "acked")
        client.post("/api/_test/fake-device/tap", json={})
        done = recv_until(ws, lambda s: s["run"]["state"] == "completed")
        assert done["points"]["study"] == 10
        assert done["run"]["session"]["result"]["pointsArePreview"] is False
        assert client.post("/api/demo/reset").status_code == 200  # dismiss result
        assert state(client)["points"]["study"] == 10


def test_device_drop_during_ring_reported_then_restored(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        client.post("/api/test-alarm")
        recv_until(ws, lambda s: s["run"]["state"] == "ringing")
        client.post("/api/_test/fake-device/power-off")
        lost = recv_until(ws, lambda s: s["device"]["status"] == "reconnecting")
        assert lost["run"]["state"] == "ringing" and lost["run"]["session"]["connectionLostDuringRun"]
        assert "lost" in lost["device"]["lastError"]
        client.post("/api/_test/fake-device/power-on", json={"reboot": True})
        recv_until(ws, lambda s: s["device"]["status"] == "connected")
        wait_sync(lambda: client.get("/api/_test/fake-device").json()["indicator"] is True)


def test_missing_ack_shown(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        client.post("/api/_test/fake-device/no-ack", json={"enabled": True})
        client.post("/api/test-alarm")
        snap = recv_until(ws, lambda s: s["run"]["session"] is not None
                          and s["run"]["session"]["activation"]["status"] == "missing")
        assert "activation_missing" in snap["run"]["session"]["technicalIssues"]


# ---------------------------------------------------------------- FR-07 export
def test_events_and_csv_export(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ready(client, ws)
        client.post("/api/test-alarm")
        snap = recv_until(ws, lambda s: s["run"]["state"] == "ringing")
        sid = snap["run"]["session"]["sessionId"]
        ws.send_json({"type": "sound_started", "sessionId": sid})
        recv_until(ws, lambda s: s["run"]["session"]["activation"]["status"] == "acked")
        client.post("/api/_test/fake-device/tap", json={})
        recv_until(ws, lambda s: s["run"]["state"] == "completed")
    evs = client.get("/api/events").json()["events"]
    run = [e["event"] for e in evs if e["sessionId"] == sid]
    for needed in ("test_alarm_scheduled", "ring_started", "activation_sent", "activation_acked",
                   "sound_started", "tap_received", "completed"):
        assert needed in run
    r = client.get("/api/events.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    done = [x for x in rows if x["event"] == "completed"][0]
    assert done["sessionId"] == sid and done["runType"] == "classroom_demo" and done["category"] == "successful_tap"
    study = list(csv.DictReader(io.StringIO(client.get("/api/events.csv?mode=study").text)))
    assert all(x["mode"] == "study" for x in study) and sid not in {x["sessionId"] for x in study}
    assert client.get("/api/events.csv?mode=bogus").status_code == 422
    sessions = client.get("/api/sessions").json()["sessions"]
    assert sessions[0]["sessionId"] == sid and sessions[0]["outcome"] == "successful_tap"
    assert client.get("/api/sessions.csv").text.startswith("sessionId,")


# ---------------------------------------------------------------- test hooks
def test_test_hooks_absent_without_test_mode(tmp_path):
    s = fast_settings(tmp_path, test_mode=False)
    with TestClient(create_app(s)) as c:
        assert c.post("/api/_test/fake-device/tap", json={}).status_code in (404, 405)
        assert state(c)["testMode"] is False


def test_test_alarm_delay_hook(client):
    r = client.post("/api/_test/test-alarm-delay", json={"seconds": 1.5})
    assert r.json() == {"testAlarmDelayS": 1.5}
    assert client.post("/api/_test/test-alarm-delay", json={"seconds": "x"}).status_code == 422


def test_fake_hooks_need_fake_device(tmp_path):
    s = fast_settings(tmp_path, device_mode="real", device_host="127.0.0.1", device_port=9)
    with TestClient(create_app(s)) as c:
        r = c.post("/api/_test/fake-device/drop")
        assert r.status_code == 404 and r.json()["error"] == "no_fake_device"


def test_web_folder_served_when_present(tmp_path, monkeypatch):
    import wakequest.api as api_mod

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>WakeQuest</h1>")
    monkeypatch.setattr(api_mod, "WEB_DIR", web)
    with TestClient(create_app(fast_settings(tmp_path / "d"))) as c:
        assert "WakeQuest" in c.get("/").text
        assert c.get("/api/state").json()["type"] == "snapshot"  # API still wins


def test_no_web_folder_no_root(tmp_path, monkeypatch):
    import wakequest.api as api_mod

    monkeypatch.setattr(api_mod, "WEB_DIR", tmp_path / "missing")
    with TestClient(create_app(fast_settings(tmp_path / "d"))) as c:
        assert c.get("/").status_code == 404
