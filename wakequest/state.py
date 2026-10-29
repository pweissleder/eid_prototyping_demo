"""Pure WakeQuest state machine.

No IO and no asyncio. Every input (REST command, device message, browser
message, link event, clock tick) is a method that receives the current time
and returns ``Effects``: events to log, device messages to send, whether the
alarm list must be persisted, and whether a fresh snapshot must be pushed.
The coordinator in ``service.py`` executes those effects.

Rules implemented here (see context.md §4/§6 and docs/protocol.md):
- FR-01 alarm validation; one armed alarm; arming replaces the previous one.
- FR-02/FR-09 device status (connected only after a valid hello).
- FR-03 arming preconditions (device connected, sensor stable, audio tested).
- FR-04 ring → activate → ack within 2 s or activation_missing.
- FR-05 tap acceptance rule and ignore reasons.
- FR-06 reward; points are derived from the event log, never incremented.
- FR-07 event logging; FR-08 test alarm, abort, demo reset.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .clock import iso, ms_between
from .models import (
    ActivationView,
    Alarm,
    ArmChange,
    AudioView,
    DeviceView,
    Missing,
    PointsView,
    Readiness,
    ResultView,
    RunView,
    SessionView,
    Snapshot,
    SoundView,
    TapView,
    TestAlarmView,
    TimezoneView,
)

POINTS_PER_COMPLETION = 10
NAME_MAX = 60
LOCAL_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
CORRECTION_INTERVAL_S = 2.0


class CommandError(Exception):
    """A refused command. Maps to an HTTP error with an understandable message."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


@dataclass
class Effects:
    events: list[dict[str, Any]] = field(default_factory=list)
    device: list[dict[str, Any]] = field(default_factory=list)
    persist_alarms: bool = False
    push: bool = False
    result: Any = None

    def merge(self, other: "Effects") -> "Effects":
        self.events += other.events
        self.device += other.device
        self.persist_alarms |= other.persist_alarms
        self.push |= other.push
        return self


@dataclass
class DeviceInfo:
    status: str = "disconnected"
    deviceId: str | None = None
    firmware: str | None = None
    sensorStable: bool | None = None
    indicator: bool | None = None
    indicatorTimedOut: bool = False
    sessionId: str | None = None
    tapSeq: int | None = None
    uptimeMs: int | None = None
    rssi: int | None = None
    lastError: str | None = None
    connectedAt: datetime | None = None
    lostAt: datetime | None = None
    lastMessageAt: datetime | None = None


@dataclass
class Session:
    sessionId: str
    mode: str  # "study" | "demo"
    alarmId: str | None
    alarmName: str
    scheduledFor: datetime
    ringStartedAt: datetime
    phase: str = "ringing"  # ringing | completed | aborted
    soundStatus: str = "pending"  # pending | started | failed
    soundStartedAt: datetime | None = None
    soundError: str | None = None
    activationStatus: str = "pending"  # pending | sent | acked | missing
    activationSentAt: datetime | None = None
    activationDeadline: datetime | None = None
    activationAckedAt: datetime | None = None
    tapAcceptedAt: datetime | None = None
    endedAt: datetime | None = None
    abortReason: str | None = None
    responseTimeMs: int | None = None
    responseTimeFlag: str | None = None
    connectionLostDuringRun: bool = False
    technicalIssues: list[str] = field(default_factory=list)
    indicatorTimeoutLogged: bool = False

    def issue(self, name: str) -> None:
        if name not in self.technicalIssues:
            self.technicalIssues.append(name)


@dataclass
class PendingTest:
    sessionId: str
    fireAt: datetime


def default_session_id(now: datetime) -> str:
    return f"s-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def default_alarm_id(now: datetime) -> str:
    return f"a-{secrets.token_hex(4)}"


def fmt_local(dt: datetime) -> str:
    return f"{dt:%H:%M} on {dt.day} {dt:%b %Y}"


def derived_study_points(events: list[dict[str, Any]]) -> tuple[int, int]:
    """Points = 10 × distinct completed *study* sessions in the log."""
    completed = {
        e.get("sessionId")
        for e in events
        if e.get("event") == "completed" and e.get("mode") == "study" and e.get("sessionId")
    }
    return POINTS_PER_COMPLETION * len(completed), len(completed)


class Machine:
    def __init__(
        self,
        *,
        alarms: list[Alarm] | None = None,
        events: list[dict[str, Any]] | None = None,
        localize: Callable[[datetime], datetime] | None = None,
        session_id_factory: Callable[[datetime], str] = default_session_id,
        alarm_id_factory: Callable[[datetime], str] = default_alarm_id,
        test_alarm_delay_s: float = 10.0,
        ack_timeout_s: float = 2.0,
        device_host: str = "wakequest.local",
        device_port: int = 81,
        test_mode: bool = False,
    ) -> None:
        self.alarms: dict[str, Alarm] = {a.id: a for a in (alarms or [])}
        self.events: list[dict[str, Any]] = list(events or [])
        self._localize = localize
        self._new_session_id = session_id_factory
        self._new_alarm_id = alarm_id_factory
        self.test_alarm_delay_s = test_alarm_delay_s
        self.ack_timeout_s = ack_timeout_s
        self.test_mode = test_mode
        self.device = DeviceInfo()
        self.device_host = device_host
        self.device_port = device_port
        self.session: Session | None = None
        self.pending_test: PendingTest | None = None
        self.last_arm_change: ArmChange | None = None
        self.last_tap: TapView | None = None
        self.browsers: set[str] = set()
        self.audio_ok: set[str] = set()
        self.rev = 0
        self._last_tap_seq: int | None = None
        self._last_uptime: int | None = None
        self._last_correction: datetime | None = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _log(self, eff: Effects, now: datetime, event: str, session: Session | None = None,
             *, session_id: str | None = None, mode: str | None = None, **fields: Any) -> dict[str, Any]:
        ev: dict[str, Any] = {
            "ts": iso(now),
            "event": event,
            "sessionId": session.sessionId if session else session_id,
            "mode": session.mode if session else mode,
        }
        ev.update(fields)
        self.events.append(ev)
        eff.events.append(ev)
        return ev

    def localize(self, naive: datetime, now: datetime) -> datetime:
        if self._localize is not None:
            return self._localize(naive)
        return naive.replace(tzinfo=now.tzinfo)

    @property
    def ringing(self) -> Session | None:
        s = self.session
        return s if s is not None and s.phase == "ringing" else None

    @property
    def armed_alarm(self) -> Alarm | None:
        for a in self.alarms.values():
            if a.status == "armed":
                return a
        return None

    def run_active(self) -> bool:
        return self.ringing is not None or self.pending_test is not None

    def readiness(self) -> Readiness:
        connected = self.device.status == "connected"
        stable = connected and bool(self.device.sensorStable)
        audio = bool(self.audio_ok)
        missing: list[Missing] = []
        if not connected:
            missing.append(Missing(code="device_not_connected",
                                   message="Connect the device first: it is not connected."))
        elif not stable:
            missing.append(Missing(code="sensor_not_stable",
                                   message="Keep the device still: the tap sensor has not been idle "
                                           "for 2 seconds yet."))
        if not audio:
            missing.append(Missing(code="audio_not_tested",
                                   message="Enable and test sound in the browser first."))
        return Readiness(deviceConnected=connected, sensorStable=stable, audioTested=audio,
                         canArm=not missing, missing=missing)

    def _require_ready(self, what: str) -> None:
        r = self.readiness()
        if not r.canArm:
            msgs = " ".join(m.message for m in r.missing)
            raise CommandError(409, "not_ready", f"Cannot {what} yet. {msgs}",
                               missing=[m.model_dump() for m in r.missing])

    def study_points(self) -> tuple[int, int]:
        return derived_study_points(self.events)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def startup(self, now: datetime) -> Effects:
        eff = Effects(push=True)
        self._log(eff, now, "service_started")
        for a in self.alarms.values():
            if a.status == "armed" and datetime.fromisoformat(a.scheduledAt) <= now:
                a.status = "missed"
                eff.persist_alarms = True
                self._log(eff, now, "alarm_missed", alarmId=a.id, alarmName=a.name,
                          scheduledFor=a.scheduledAt, reason="service_not_running_at_alarm_time")
        return eff

    def shutdown(self, now: datetime) -> Effects:
        eff = Effects()
        s = self.ringing
        if s is not None:
            self._end_session(eff, now, s, "aborted", reason="service_shutdown")
        self._log(eff, now, "service_stopped")
        return eff

    # ------------------------------------------------------------------
    # alarms (FR-01, FR-03)
    # ------------------------------------------------------------------
    def create_alarm(self, now: datetime, payload: Any) -> Effects:
        if not isinstance(payload, dict):
            raise CommandError(422, "invalid_alarm", "Send the alarm as a JSON object with "
                                                     "\"name\" and \"localTime\".")
        raw_name = payload.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            raise CommandError(422, "invalid_alarm", "Please give the alarm a name.", field="name")
        if len(name) > NAME_MAX:
            raise CommandError(422, "invalid_alarm",
                               f"The name is too long ({len(name)} characters); use at most {NAME_MAX}.",
                               field="name")
        raw_time = payload.get("localTime")
        if raw_time is None or (isinstance(raw_time, str) and not raw_time.strip()):
            raise CommandError(422, "invalid_alarm", "Please choose a date and time for the alarm.",
                               field="localTime")
        if not isinstance(raw_time, str) or not LOCAL_TIME_RE.match(raw_time.strip()):
            raise CommandError(422, "invalid_alarm",
                               "The time must look like 2026-10-30T07:00 (date, then 24-hour time).",
                               field="localTime")
        local_time = raw_time.strip()
        try:
            naive = datetime.strptime(local_time, "%Y-%m-%dT%H:%M")
        except ValueError:
            raise CommandError(422, "invalid_alarm", f"{local_time} is not a real date and time.",
                               field="localTime") from None
        scheduled = self.localize(naive, now)
        if scheduled <= now:
            raise CommandError(422, "invalid_alarm",
                               f"That time is in the past (it is now {fmt_local(now)}). "
                               "Choose a future date and time.", field="localTime")
        alarm = Alarm(id=self._new_alarm_id(now), name=name, localTime=local_time,
                      scheduledAt=iso(scheduled), createdAt=iso(now), status="saved")
        self.alarms[alarm.id] = alarm
        eff = Effects(persist_alarms=True, push=True, result=alarm)
        self._log(eff, now, "alarm_created", alarmId=alarm.id, alarmName=name,
                  scheduledFor=alarm.scheduledAt)
        return eff

    def _get_alarm(self, alarm_id: str) -> Alarm:
        alarm = self.alarms.get(alarm_id)
        if alarm is None:
            raise CommandError(404, "alarm_not_found", "That alarm does not exist (any more).")
        return alarm

    def delete_alarm(self, now: datetime, alarm_id: str) -> Effects:
        alarm = self._get_alarm(alarm_id)
        eff = Effects(persist_alarms=True, push=True)
        if alarm.status == "armed":
            self._log(eff, now, "alarm_disarmed", alarmId=alarm.id, reason="deleted")
        del self.alarms[alarm_id]
        self._log(eff, now, "alarm_deleted", alarmId=alarm.id, alarmName=alarm.name)
        return eff

    def arm_alarm(self, now: datetime, alarm_id: str) -> Effects:
        alarm = self._get_alarm(alarm_id)
        if alarm.status in ("rang", "missed"):
            raise CommandError(409, "alarm_used",
                               f"“{alarm.name}” has already {'rung' if alarm.status == 'rang' else 'been missed'}; "
                               "create a new alarm.")
        if datetime.fromisoformat(alarm.scheduledAt) <= now:
            raise CommandError(409, "alarm_in_past",
                               f"“{alarm.name}” is in the past (it is now {fmt_local(now)}); "
                               "create a new alarm.")
        if self.ringing is not None:
            raise CommandError(409, "run_active", "An alarm is ringing right now; finish or stop it first.")
        previous = self.armed_alarm
        if previous is not None and previous.id == alarm.id:
            return Effects(result={"armedAlarm": alarm, "replacedAlarm": None})
        self._require_ready("arm the alarm")
        eff = Effects(persist_alarms=True, push=True)
        if previous is not None:
            previous.status = "saved"
        alarm.status = "armed"
        if self.session is not None:  # clear a finished run's result screen
            self.session = None
        msg = (f"“{alarm.name}” is now armed for {fmt_local(datetime.fromisoformat(alarm.scheduledAt))}"
               + (f"; it replaces “{previous.name}”." if previous else "."))
        self.last_arm_change = ArmChange(at=iso(now), armedAlarmId=alarm.id,
                                         replacedAlarmId=previous.id if previous else None, message=msg)
        self._log(eff, now, "alarm_armed", alarmId=alarm.id, alarmName=alarm.name,
                  scheduledFor=alarm.scheduledAt, replacedAlarmId=previous.id if previous else None)
        eff.result = {"armedAlarm": alarm, "replacedAlarm": previous}
        return eff

    def disarm(self, now: datetime) -> Effects:
        alarm = self.armed_alarm
        if alarm is None:
            return Effects(result={"disarmedAlarm": None})
        alarm.status = "saved"
        self.last_arm_change = ArmChange(at=iso(now), armedAlarmId=None, replacedAlarmId=alarm.id,
                                         message=f"“{alarm.name}” is no longer armed.")
        eff = Effects(persist_alarms=True, push=True, result={"disarmedAlarm": alarm})
        self._log(eff, now, "alarm_disarmed", alarmId=alarm.id, reason="user")
        return eff

    # ------------------------------------------------------------------
    # presenter (FR-08)
    # ------------------------------------------------------------------
    def schedule_test_alarm(self, now: datetime) -> Effects:
        if self.ringing is not None or self.pending_test is not None:
            raise CommandError(409, "run_active",
                               "A run is already active. Stop it or reset the demo first.")
        self._require_ready("start a test alarm")
        sid = self._new_session_id(now)
        fire_at = now + timedelta(seconds=self.test_alarm_delay_s)
        self.pending_test = PendingTest(sessionId=sid, fireAt=fire_at)
        if self.session is not None:
            self.session = None
        eff = Effects(push=True, result=self.pending_test)
        self._log(eff, now, "test_alarm_scheduled", session_id=sid, mode="demo",
                  scheduledFor=iso(fire_at), delayS=self.test_alarm_delay_s)
        return eff

    def abort(self, now: datetime, reason: str = "manual") -> Effects:
        s = self.ringing
        eff = Effects(push=True)
        if s is not None:
            self._end_session(eff, now, s, "aborted", reason=reason)
            return eff
        if self.pending_test is not None:
            pt = self.pending_test
            self.pending_test = None
            self._log(eff, now, "test_alarm_cancelled", session_id=pt.sessionId, mode="demo", reason=reason)
            return eff
        raise CommandError(409, "nothing_to_stop", "Nothing to stop: no alarm is ringing.")

    def demo_reset(self, now: datetime) -> Effects:
        s = self.ringing
        if s is not None and s.mode == "study":
            raise CommandError(409, "study_run_active",
                               "A study alarm is ringing. Use “Stop run (aborted)” to stop it.")
        eff = Effects(push=True)
        if s is not None:
            self._end_session(eff, now, s, "aborted", reason="reset")
        if self.pending_test is not None:
            pt = self.pending_test
            self.pending_test = None
            self._log(eff, now, "test_alarm_cancelled", session_id=pt.sessionId, mode="demo", reason="reset")
        cleared = self.session.sessionId if self.session else None
        self.session = None
        self._log(eff, now, "demo_reset", clearedSessionId=cleared)
        return eff

    def set_test_alarm_delay(self, seconds: float) -> None:
        if not (0 <= seconds <= 3600):
            raise CommandError(422, "invalid_delay", "Delay must be between 0 and 3600 seconds.")
        self.test_alarm_delay_s = float(seconds)

    # ------------------------------------------------------------------
    # clock (FR-04)
    # ------------------------------------------------------------------
    def tick(self, now: datetime) -> Effects:
        eff = Effects()
        s = self.ringing
        if (s is not None and s.activationStatus == "sent" and s.activationDeadline is not None
                and now >= s.activationDeadline):
            s.activationStatus = "missing"
            s.issue("activation_missing")
            self._log(eff, now, "activation_missing", s, reason="no_ack",
                      timeoutMs=int(self.ack_timeout_s * 1000))
            eff.push = True
        armed = self.armed_alarm
        if armed is not None and datetime.fromisoformat(armed.scheduledAt) <= now:
            cur = self.ringing
            if cur is not None and cur.mode == "demo":
                self._end_session(eff, now, cur, "aborted", reason="preempted_by_study_alarm")
            if self.pending_test is not None:
                pt = self.pending_test
                self.pending_test = None
                self._log(eff, now, "test_alarm_cancelled", session_id=pt.sessionId, mode="demo",
                          reason="preempted_by_study_alarm")
            if self.ringing is None:
                armed.status = "rang"
                sid = self._new_session_id(now)
                armed.sessionId = sid
                eff.persist_alarms = True
                self._ring(eff, now, sid, "study", armed.id, armed.name,
                           datetime.fromisoformat(armed.scheduledAt))
        if self.pending_test is not None and now >= self.pending_test.fireAt and self.ringing is None:
            pt = self.pending_test
            self.pending_test = None
            self._ring(eff, now, pt.sessionId, "demo", None, "Test alarm", pt.fireAt)
        return eff

    def _ring(self, eff: Effects, now: datetime, sid: str, mode: str, alarm_id: str | None,
              name: str, scheduled_for: datetime) -> None:
        s = Session(sessionId=sid, mode=mode, alarmId=alarm_id, alarmName=name,
                    scheduledFor=scheduled_for, ringStartedAt=now)
        self.session = s
        eff.push = True
        self._log(eff, now, "ring_started", s, alarmId=alarm_id, alarmName=name,
                  scheduledFor=iso(scheduled_for), lateMs=max(0, ms_between(scheduled_for, now)))
        if self.device.status == "connected":
            self._send_activation(eff, now, s, {"type": "activate", "sessionId": sid}, via="activate")
        else:
            s.activationStatus = "missing"
            s.issue("activation_missing")
            self._log(eff, now, "activation_missing", s, reason="device_not_connected")

    def _send_activation(self, eff: Effects, now: datetime, s: Session, msg: dict[str, Any], via: str) -> None:
        eff.device.append(msg)
        if s.activationStatus == "pending":
            s.activationStatus = "sent"
            s.activationSentAt = now
            s.activationDeadline = now + timedelta(seconds=self.ack_timeout_s)
        self._log(eff, now, "activation_sent", s, via=via)

    def _end_session(self, eff: Effects, now: datetime, s: Session, phase: str, *, reason: str | None = None) -> None:
        s.phase = phase
        s.endedAt = now
        eff.push = True
        if phase == "completed":
            s.tapAcceptedAt = now
            if s.soundStartedAt is not None:
                s.responseTimeMs = ms_between(s.soundStartedAt, now)
            else:
                s.responseTimeFlag = "sound_not_confirmed"
            self._log(eff, now, "completed", s, scheduledFor=iso(s.scheduledFor),
                      responseTimeMs=s.responseTimeMs, responseTimeFlag=s.responseTimeFlag,
                      points=POINTS_PER_COMPLETION, pointsArePreview=s.mode == "demo")
            dreason = "completed"
        else:
            s.abortReason = reason
            self._log(eff, now, "aborted", s, scheduledFor=iso(s.scheduledFor), reason=reason, points=0)
            dreason = "reset" if reason == "reset" else "aborted"
        if self.device.status == "connected":
            eff.device.append({"type": "deactivate", "sessionId": s.sessionId, "reason": dreason})
            self._log(eff, now, "deactivation_sent", s, reason=dreason)

    # ------------------------------------------------------------------
    # device connection (FR-02, FR-09)
    # ------------------------------------------------------------------
    def connect_requested(self, now: datetime) -> Effects:
        eff = Effects(push=True)
        if self.device.status == "connected":
            return eff
        self.device.status = "connecting"
        self.device.lastError = None
        self._log(eff, now, "device_connect_requested", host=self.device_host, port=self.device_port)
        return eff

    def disconnect_requested(self, now: datetime) -> Effects:
        eff = Effects(push=True)
        was = self.device.status
        self.device.status = "disconnected"
        self.device.lastError = None
        self.device.lostAt = None
        if was != "disconnected":
            self._log(eff, now, "device_disconnected", reason="user")
        return eff

    def link_attempt(self, now: datetime, reconnecting: bool) -> Effects:
        new = "reconnecting" if reconnecting else "connecting"
        if self.device.status == new:
            return Effects()
        self.device.status = new
        return Effects(push=True)

    def link_failed(self, now: datetime, error: str) -> Effects:
        """The user-requested connection attempt failed (never got a valid hello)."""
        eff = Effects(push=True)
        self.device.status = "disconnected"
        self.device.lastError = error
        self._log(eff, now, "device_connect_failed", error=error, host=self.device_host)
        return eff

    def link_retry_failed(self, now: datetime, error: str, attempt: int) -> Effects:
        self.device.status = "reconnecting"
        self.device.lastError = f"Connection to the device was lost. Reconnecting… (attempt {attempt}: {error})"
        return Effects(push=True)

    def link_lost(self, now: datetime, reason: str) -> Effects:
        eff = Effects(push=True)
        self.device.status = "reconnecting"
        self.device.lastError = f"Connection to the device was lost ({reason}). Reconnecting automatically…"
        if self.device.lostAt is None:
            self.device.lostAt = now
        s = self.ringing
        if s is not None:
            s.connectionLostDuringRun = True
            s.issue("connection_lost")
        self._log(eff, now, "connection_lost", s, reason=reason, deviceId=self.device.deviceId)
        return eff

    def link_hello(self, now: datetime, hello: dict[str, Any]) -> Effects:
        eff = Effects(push=True)
        d = self.device
        device_id = hello.get("deviceId")
        uptime = hello.get("uptimeMs") if isinstance(hello.get("uptimeMs"), int) else None
        if device_id != d.deviceId:
            self._last_tap_seq = None
            self._last_uptime = None
        else:
            hello_seq = hello.get("tapSeq") if isinstance(hello.get("tapSeq"), int) else None
            uptime_back = uptime is not None and self._last_uptime is not None and uptime < self._last_uptime
            # tapSeq only grows within one boot, so a lower tapSeq also proves a reboot
            # (covers an outage longer than the previous uptime, where uptime looks larger).
            seq_back = (hello_seq is not None and self._last_tap_seq is not None
                        and hello_seq < self._last_tap_seq)
            if uptime_back or seq_back:
                self._last_tap_seq = None
                self._log(eff, now, "device_rebooted", deviceId=device_id,
                          previousUptimeMs=self._last_uptime, uptimeMs=uptime,
                          detectedBy="uptimeMs" if uptime_back else "tapSeq")
        if uptime is not None:
            self._last_uptime = uptime
        if d.lostAt is not None:
            self._log(eff, now, "connection_restored", self.ringing, deviceId=device_id,
                      downMs=ms_between(d.lostAt, now))
        else:
            self._log(eff, now, "device_connected", deviceId=device_id, firmware=hello.get("firmware"),
                      sensorStable=hello.get("sensorStable"))
        d.status = "connected"
        d.lastError = None
        d.lostAt = None
        d.connectedAt = now
        d.lastMessageAt = now
        d.deviceId = device_id
        d.firmware = hello.get("firmware") if isinstance(hello.get("firmware"), str) else None
        d.sensorStable = bool(hello.get("sensorStable"))
        d.indicator = bool(hello.get("indicator"))
        d.indicatorTimedOut = False
        d.sessionId = hello.get("sessionId") if isinstance(hello.get("sessionId"), str) else None
        d.tapSeq = hello.get("tapSeq") if isinstance(hello.get("tapSeq"), int) else None
        d.uptimeMs = uptime
        # Always sync the device to the service's view of the current session.
        s = self.ringing
        sid = s.sessionId if s is not None else None
        sync = {"type": "sync", "sessionId": sid}
        if s is not None:
            self._send_activation(eff, now, s, sync, via="sync")
        else:
            eff.device.append(sync)
            self._log(eff, now, "sync_sent", syncSessionId=None)
        self._last_correction = now
        return eff

    # ------------------------------------------------------------------
    # device messages
    # ------------------------------------------------------------------
    def device_message(self, now: datetime, msg: Any) -> Effects:
        if not isinstance(msg, dict):
            return Effects()
        self.device.lastMessageAt = now
        t = msg.get("type")
        if t == "status":
            return self._on_status(now, msg)
        if t == "ack":
            return self._on_ack(now, msg)
        if t == "tap":
            return self._on_tap(now, msg)
        if t == "hello":
            return self.link_hello(now, msg)
        if t == "error":
            eff = Effects()
            self._log(eff, now, "device_error", self.ringing, code=msg.get("code"), detail=msg.get("detail"))
            return eff
        return Effects()

    def _observe_uptime(self, eff: Effects, now: datetime, uptime: Any) -> None:
        if not isinstance(uptime, int):
            return
        if self._last_uptime is not None and uptime < self._last_uptime:
            self._last_tap_seq = None
            self._log(eff, now, "device_rebooted", deviceId=self.device.deviceId,
                      previousUptimeMs=self._last_uptime, uptimeMs=uptime)
        self._last_uptime = uptime
        self.device.uptimeMs = uptime

    def _on_status(self, now: datetime, msg: dict[str, Any]) -> Effects:
        eff = Effects()
        d = self.device
        self._observe_uptime(eff, now, msg.get("uptimeMs"))
        before = (d.sensorStable, d.indicator, d.indicatorTimedOut, d.sessionId)
        if "sensorStable" in msg:
            d.sensorStable = bool(msg["sensorStable"])
        if "indicator" in msg:
            d.indicator = bool(msg["indicator"])
        d.indicatorTimedOut = bool(msg.get("indicatorTimedOut", False))
        if "sessionId" in msg:
            d.sessionId = msg["sessionId"] if isinstance(msg["sessionId"], str) else None
        if isinstance(msg.get("tapSeq"), int):
            d.tapSeq = msg["tapSeq"]
        if isinstance(msg.get("rssi"), int):
            d.rssi = msg["rssi"]
        if before != (d.sensorStable, d.indicator, d.indicatorTimedOut, d.sessionId):
            eff.push = True
        s = self.ringing
        if s is not None and d.indicatorTimedOut and d.sessionId == s.sessionId and not s.indicatorTimeoutLogged:
            s.indicatorTimeoutLogged = True
            s.issue("indicator_timed_out")
            self._log(eff, now, "indicator_timed_out", s)
            eff.push = True
        # QR-07: the light must be off whenever no alarm is ringing.
        if (s is None and d.indicator and d.status == "connected"
                and (self._last_correction is None
                     or (now - self._last_correction).total_seconds() >= CORRECTION_INTERVAL_S)):
            self._last_correction = now
            eff.device.append({"type": "sync", "sessionId": None})
            self._log(eff, now, "sync_sent", syncSessionId=None, reason="indicator_on_while_not_ringing")
        return eff

    def _on_ack(self, now: datetime, msg: dict[str, Any]) -> Effects:
        eff = Effects()
        d = self.device
        of = msg.get("of")
        ack_sid = msg.get("sessionId") if isinstance(msg.get("sessionId"), str) else None
        if "indicator" in msg:
            if d.indicator != bool(msg["indicator"]) or d.sessionId != ack_sid:
                eff.push = True
            d.indicator = bool(msg["indicator"])
            d.sessionId = ack_sid
        s = self.ringing
        if (of in ("activate", "sync") and s is not None and ack_sid == s.sessionId
                and msg.get("indicator") is True):
            if s.activationStatus != "acked":
                late = s.activationStatus == "missing"
                s.activationStatus = "acked"
                s.activationAckedAt = now
                self._log(eff, now, "activation_acked", s, of=of, late=late)
                eff.push = True
        elif of == "deactivate":
            self._log(eff, now, "deactivation_acked", deviceSessionId=ack_sid, ignored=bool(msg.get("ignored")))
        return eff

    def _on_tap(self, now: datetime, msg: dict[str, Any]) -> Effects:
        eff = Effects(push=True)
        self._observe_uptime(eff, now, msg.get("uptimeMs"))
        seq = msg.get("seq") if isinstance(msg.get("seq"), int) and not isinstance(msg.get("seq"), bool) else None
        tap_sid = msg.get("sessionId") if isinstance(msg.get("sessionId"), str) else None
        if seq is not None:
            self.device.tapSeq = seq
        duplicate_seq = seq is not None and self._last_tap_seq is not None and seq <= self._last_tap_seq
        if seq is not None and not duplicate_seq:
            self._last_tap_seq = seq
        s = self.session
        reason: str | None
        if duplicate_seq:
            reason = "duplicate"
        elif s is None or s.phase != "ringing":
            if s is not None and tap_sid == s.sessionId and s.phase == "completed":
                reason = "duplicate"
            elif s is not None and tap_sid == s.sessionId and s.phase == "aborted":
                reason = "stale_session"
            elif self.armed_alarm is not None or self.pending_test is not None:
                reason = "armed"
            else:
                reason = "idle"
        elif tap_sid != s.sessionId:
            reason = "stale_session"
        elif s.activationStatus != "acked":
            reason = "not_activated"
        else:
            reason = None
        accepted = reason is None
        ringing = self.ringing
        self._log(eff, now, "tap_received", ringing, accepted=accepted, reason=reason, seq=seq,
                  tapSessionId=tap_sid, deviceId=self.device.deviceId)
        self.last_tap = TapView(at=iso(now), seq=seq, tapSessionId=tap_sid, accepted=accepted, reason=reason)
        if accepted and ringing is not None:
            self._end_session(eff, now, ringing, "completed")
        return eff

    # ------------------------------------------------------------------
    # browser messages (FR-03 audio test, FR-04 sound confirmation)
    # ------------------------------------------------------------------
    def browser_connected(self, now: datetime, bid: str) -> Effects:
        self.browsers.add(bid)
        return Effects(push=True)

    def browser_disconnected(self, now: datetime, bid: str) -> Effects:
        eff = Effects(push=True)
        self.browsers.discard(bid)
        self.audio_ok.discard(bid)
        s = self.ringing
        if s is not None and not self.browsers:
            s.issue("browser_lost_during_run")
            self._log(eff, now, "browser_lost_during_run", s)
        return eff

    def browser_message(self, now: datetime, bid: str, msg: Any) -> Effects:
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            raise CommandError(400, "bad_message", "Messages must be JSON objects with a \"type\".")
        t = msg["type"]
        eff = Effects()
        if t == "audio_test_ok":
            if bid not in self.audio_ok:
                self.audio_ok.add(bid)
                eff.push = True
                self._log(eff, now, "audio_test_ok")
            return eff
        if t == "audio_test_failed":
            self.audio_ok.discard(bid)
            eff.push = True
            self._log(eff, now, "audio_test_failed", error=str(msg.get("error") or "unknown"))
            return eff
        if t in ("sound_started", "sound_failed"):
            s = self.ringing
            sid = msg.get("sessionId")
            if s is None or sid != s.sessionId:
                raise CommandError(409, "stale_session",
                                   f"{t} ignored: session {sid!r} is not the ringing session.")
            if t == "sound_started":
                if s.soundStatus != "started":
                    s.soundStatus = "started"
                    s.soundStartedAt = now
                    s.soundError = None
                    self._log(eff, now, "sound_started", s)
                    eff.push = True
            else:
                if s.soundStatus != "started":
                    s.soundStatus = "failed"
                    s.soundError = str(msg.get("error") or "unknown")
                    s.issue("sound_failed")
                    self._log(eff, now, "sound_failed", s, error=s.soundError)
                    eff.push = True
            return eff
        raise CommandError(400, "unknown_type", f"Unknown message type {t!r}.")

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    def run_state(self) -> str:
        s = self.session
        if s is not None and s.phase == "ringing":
            return "ringing"
        if self.pending_test is not None:
            return "test_pending"
        if s is not None:
            return s.phase
        if self.armed_alarm is not None:
            return "armed"
        return "idle"

    def _session_view(self, s: Session) -> SessionView:
        result = None
        if s.phase == "completed":
            result = ResultView(points=POINTS_PER_COMPLETION, pointsArePreview=s.mode == "demo",
                                responseTimeMs=s.responseTimeMs, responseTimeFlag=s.responseTimeFlag)
        elif s.phase == "aborted":
            result = ResultView(points=0, pointsArePreview=s.mode == "demo", responseTimeMs=None,
                                responseTimeFlag=None)
        return SessionView(
            sessionId=s.sessionId, mode=s.mode, alarmId=s.alarmId, alarmName=s.alarmName,
            scheduledFor=iso(s.scheduledFor), ringStartedAt=iso(s.ringStartedAt), phase=s.phase,
            sound=SoundView(status=s.soundStatus, startedAt=iso(s.soundStartedAt), error=s.soundError),
            activation=ActivationView(status=s.activationStatus, sentAt=iso(s.activationSentAt),
                                      ackedAt=iso(s.activationAckedAt)),
            tapAcceptedAt=iso(s.tapAcceptedAt), endedAt=iso(s.endedAt), abortReason=s.abortReason,
            result=result, connectionLostDuringRun=s.connectionLostDuringRun,
            technicalIssues=list(s.technicalIssues),
        )

    def alarms_list(self) -> list[Alarm]:
        return sorted(self.alarms.values(), key=lambda a: (a.scheduledAt, a.createdAt))

    def snapshot(self, now: datetime, *, bump: bool = True) -> Snapshot:
        if bump:
            self.rev += 1
        d = self.device
        offset = now.strftime("%z")
        points, completed = self.study_points()
        pt = self.pending_test
        return Snapshot(
            rev=self.rev,
            serverTime=iso(now),
            timezone=TimezoneView(name=now.tzname(), utcOffset=f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"),
            testMode=self.test_mode,
            device=DeviceView(
                status=d.status, host=self.device_host, port=self.device_port, deviceId=d.deviceId,
                firmware=d.firmware, sensorStable=d.sensorStable, indicator=d.indicator,
                indicatorTimedOut=d.indicatorTimedOut, deviceSessionId=d.sessionId, tapSeq=d.tapSeq,
                rssi=d.rssi, lastError=d.lastError, connectedAt=iso(d.connectedAt), lostAt=iso(d.lostAt),
                lastMessageAt=iso(d.lastMessageAt), lastTap=self.last_tap,
            ),
            audio=AudioView(tested=bool(self.audio_ok), browsersConnected=len(self.browsers)),
            readiness=self.readiness(),
            alarms=self.alarms_list(),
            armedAlarm=self.armed_alarm,
            lastArmChange=self.last_arm_change,
            run=RunView(
                state=self.run_state(),
                session=self._session_view(self.session) if self.session else None,
                testAlarm=TestAlarmView(sessionId=pt.sessionId, fireAt=iso(pt.fireAt)) if pt else None,
            ),
            points=PointsView(study=points, studySessionsCompleted=completed,
                              perCompletion=POINTS_PER_COMPLETION),
        )
