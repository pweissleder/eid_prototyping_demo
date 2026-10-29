"""Local persistence: alarms.json (atomic), events.jsonl (append-only), CSV export."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .models import Alarm

log = logging.getLogger("wakequest.storage")

ALARMS_FILE = "alarms.json"
EVENTS_FILE = "events.jsonl"


class Storage:
    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.alarms_path = self.data_dir / ALARMS_FILE
        self.events_path = self.data_dir / EVENTS_FILE

    # ---- alarms -------------------------------------------------------
    def load_alarms(self) -> list[Alarm]:
        if not self.alarms_path.exists():
            return []
        try:
            data = json.loads(self.alarms_path.read_text(encoding="utf-8"))
            return [Alarm.model_validate(a) for a in data.get("alarms", [])]
        except (ValueError, TypeError, AttributeError) as exc:
            backup = self.alarms_path.with_suffix(".json.corrupt")
            os.replace(self.alarms_path, backup)
            log.error("alarms.json unreadable (%s); moved to %s and starting empty", exc, backup)
            return []

    def save_alarms(self, alarms: Iterable[Alarm]) -> None:
        payload = {"version": 1, "alarms": [a.model_dump(mode="json") for a in alarms]}
        atomic_write_text(self.alarms_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    # ---- events -------------------------------------------------------
    def append_event(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def load_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(self.events_path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    log.warning("skipping malformed line %d in %s", n, self.events_path)
                    continue
                if isinstance(obj, dict) and "event" in obj:
                    events.append(obj)
        return events


def atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file + fsync + os.replace so readers never see half a file."""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:  # persist the rename itself
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


# ---- analysis / export ---------------------------------------------------

TECHNICAL_FAILURE_EVENTS = {
    "activation_missing",
    "sound_failed",
    "connection_lost",
    "device_connect_failed",
    "device_error",
    "alarm_missed",
    "indicator_timed_out",
    "browser_lost_during_run",
}
MANUAL_ABORT_REASONS = {"manual", "reset"}


def event_category(ev: dict[str, Any]) -> str:
    """Outcome category of one log row (empty for plain bookkeeping rows)."""
    name = ev.get("event")
    if name == "completed":
        return "successful_tap"
    if name == "aborted":
        return "manual_abort" if ev.get("reason") in MANUAL_ABORT_REASONS else "technical_failure"
    if name in TECHNICAL_FAILURE_EVENTS:
        return "technical_failure"
    return ""


def run_type(mode: str | None) -> str:
    return {"study": "study", "demo": "classroom_demo"}.get(mode or "", "")


EVENT_CSV_COLUMNS = [
    "ts",
    "event",
    "sessionId",
    "mode",
    "runType",
    "category",
    "accepted",
    "reason",
    "responseTimeMs",
    "points",
    "scheduledFor",
    "details",
]


def filter_events(events: Iterable[dict[str, Any]], mode: str | None) -> list[dict[str, Any]]:
    return [e for e in events if mode is None or e.get("mode") == mode]


def events_to_csv(events: Iterable[dict[str, Any]], mode: str | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EVENT_CSV_COLUMNS)
    fixed = set(EVENT_CSV_COLUMNS)
    for ev in filter_events(events, mode):
        details = {k: v for k, v in ev.items() if k not in fixed}
        w.writerow(
            [
                ev.get("ts", ""),
                ev.get("event", ""),
                ev.get("sessionId") or "",
                ev.get("mode") or "",
                run_type(ev.get("mode")),
                event_category(ev),
                _cell(ev.get("accepted")),
                ev.get("reason") or "",
                _cell(ev.get("responseTimeMs")),
                _cell(ev.get("points")),
                ev.get("scheduledFor") or "",
                json.dumps(details, ensure_ascii=False, separators=(",", ":")) if details else "",
            ]
        )
    return buf.getvalue()


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def sessions_summary(events: Iterable[dict[str, Any]], mode: str | None = None) -> list[dict[str, Any]]:
    """One row per run (sessionId), derived from the event log."""
    runs: dict[str, dict[str, Any]] = {}
    for ev in events:
        sid = ev.get("sessionId")
        if not sid:
            continue
        name = ev.get("event")
        if name == "ring_started":
            runs[sid] = {
                "sessionId": sid,
                "mode": ev.get("mode"),
                "runType": run_type(ev.get("mode")),
                "alarmId": ev.get("alarmId"),
                "alarmName": ev.get("alarmName"),
                "scheduledFor": ev.get("scheduledFor"),
                "ringStartedAt": ev.get("ts"),
                "soundStartedAt": None,
                "activationAckedAt": None,
                "tapAcceptedAt": None,
                "endedAt": None,
                "outcome": "incomplete",
                "abortReason": None,
                "responseTimeMs": None,
                "points": 0,
                "pointsArePreview": ev.get("mode") == "demo",
                "ignoredTaps": 0,
                "technicalIssues": [],
            }
            continue
        run = runs.get(sid)
        if run is None:
            continue
        if name == "sound_started" and run["soundStartedAt"] is None:
            run["soundStartedAt"] = ev.get("ts")
        elif name == "activation_acked" and run["activationAckedAt"] is None:
            run["activationAckedAt"] = ev.get("ts")
        elif name == "tap_received":
            if ev.get("accepted"):
                run["tapAcceptedAt"] = ev.get("ts")
            else:
                run["ignoredTaps"] += 1
        elif name == "completed":
            run["outcome"] = "successful_tap"
            run["endedAt"] = ev.get("ts")
            run["responseTimeMs"] = ev.get("responseTimeMs")
            run["points"] = ev.get("points", 0)
        elif name == "aborted":
            run["outcome"] = event_category(ev)
            run["abortReason"] = ev.get("reason")
            run["endedAt"] = ev.get("ts")
        if name in TECHNICAL_FAILURE_EVENTS and name not in run["technicalIssues"]:
            run["technicalIssues"].append(name)
    rows = list(runs.values())
    return [r for r in rows if mode is None or r["mode"] == mode]


SESSION_CSV_COLUMNS = [
    "sessionId",
    "mode",
    "runType",
    "alarmName",
    "scheduledFor",
    "ringStartedAt",
    "soundStartedAt",
    "activationAckedAt",
    "tapAcceptedAt",
    "endedAt",
    "outcome",
    "abortReason",
    "responseTimeMs",
    "points",
    "pointsArePreview",
    "ignoredTaps",
    "technicalIssues",
]


def sessions_to_csv(rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SESSION_CSV_COLUMNS)
    for r in rows:
        w.writerow(
            [
                ";".join(r[c]) if c == "technicalIssues" else _cell(r.get(c))
                for c in SESSION_CSV_COLUMNS
            ]
        )
    return buf.getvalue()
