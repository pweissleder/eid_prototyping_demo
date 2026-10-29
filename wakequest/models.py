"""Pydantic models: persisted alarms, logged events and the browser snapshot.

The snapshot schema is the contract for the website (see docs/api.md).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Mode = Literal["study", "demo"]
AlarmStatus = Literal["saved", "armed", "rang", "missed"]
DeviceStatus = Literal["disconnected", "connecting", "connected", "reconnecting"]
RunState = Literal["idle", "armed", "test_pending", "ringing", "completed", "aborted"]
SoundStatus = Literal["pending", "started", "failed"]
ActivationStatus = Literal["pending", "sent", "acked", "missing"]
TapReason = Literal["idle", "armed", "stale_session", "not_activated", "duplicate"]


class Alarm(BaseModel):
    """A saved one-off alarm. Persisted in data/alarms.json."""

    id: str
    name: str
    localTime: str  # "YYYY-MM-DDTHH:MM", host-local wall time as entered
    scheduledAt: str  # ISO 8601 with UTC offset
    createdAt: str
    status: AlarmStatus = "saved"
    sessionId: str | None = None  # set when the alarm rang


class Event(BaseModel):
    """One line of data/events.jsonl. Extra, event-specific fields are kept."""

    model_config = ConfigDict(extra="allow")

    ts: str  # service local time, ISO 8601 with offset
    event: str
    sessionId: str | None = None
    mode: Mode | None = None


class TapView(BaseModel):
    at: str
    seq: int | None
    tapSessionId: str | None
    accepted: bool
    reason: TapReason | None


class DeviceView(BaseModel):
    status: DeviceStatus
    host: str
    port: int
    deviceId: str | None
    firmware: str | None
    sensorStable: bool | None
    indicator: bool | None
    indicatorTimedOut: bool
    deviceSessionId: str | None
    tapSeq: int | None
    rssi: int | None
    lastError: str | None
    connectedAt: str | None
    lostAt: str | None
    lastMessageAt: str | None
    lastTap: TapView | None


class AudioView(BaseModel):
    tested: bool
    browsersConnected: int


class Missing(BaseModel):
    code: Literal["device_not_connected", "sensor_not_stable", "audio_not_tested"]
    message: str


class Readiness(BaseModel):
    deviceConnected: bool
    sensorStable: bool
    audioTested: bool
    canArm: bool
    missing: list[Missing]


class ArmChange(BaseModel):
    at: str
    armedAlarmId: str | None
    replacedAlarmId: str | None
    message: str


class SoundView(BaseModel):
    status: SoundStatus
    startedAt: str | None
    error: str | None


class ActivationView(BaseModel):
    status: ActivationStatus
    sentAt: str | None
    ackedAt: str | None


class ResultView(BaseModel):
    points: int
    pointsArePreview: bool
    responseTimeMs: int | None
    responseTimeFlag: Literal["sound_not_confirmed"] | None


class SessionView(BaseModel):
    sessionId: str
    mode: Mode
    alarmId: str | None
    alarmName: str
    scheduledFor: str
    ringStartedAt: str
    phase: Literal["ringing", "completed", "aborted"]
    sound: SoundView
    activation: ActivationView
    tapAcceptedAt: str | None
    endedAt: str | None
    abortReason: str | None
    result: ResultView | None
    connectionLostDuringRun: bool
    technicalIssues: list[str]


class TestAlarmView(BaseModel):
    __test__ = False  # not a pytest class
    sessionId: str
    fireAt: str


class RunView(BaseModel):
    state: RunState
    session: SessionView | None
    testAlarm: TestAlarmView | None


class PointsView(BaseModel):
    study: int
    studySessionsCompleted: int
    perCompletion: int


class TimezoneView(BaseModel):
    name: str | None
    utcOffset: str


class Snapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    rev: int
    serverTime: str
    timezone: TimezoneView
    testMode: bool
    device: DeviceView
    audio: AudioView
    readiness: Readiness
    alarms: list[Alarm]
    armedAlarm: Alarm | None
    lastArmChange: ArmChange | None
    run: RunView
    points: PointsView


def snapshot_json(s: Snapshot) -> dict[str, Any]:
    return s.model_dump(mode="json")
