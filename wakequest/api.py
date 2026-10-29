"""HTTP + WebSocket API (contract: docs/api.md)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .clock import Clock
from .fake_device import CURRENT
from .models import snapshot_json
from .service import Service
from .settings import Settings
from .state import CommandError
from .storage import events_to_csv, filter_events, sessions_summary, sessions_to_csv

log = logging.getLogger("wakequest.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MODES = {"study", "demo"}


async def _json_body(request: Request, *, required: bool = True) -> Any:
    raw = await request.body()
    if not raw.strip():
        if required:
            raise CommandError(422, "invalid_request", "The request needs a JSON body.")
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise CommandError(422, "invalid_request", "The request body is not valid JSON.") from None


def _mode_param(mode: str | None) -> str | None:
    if mode is None or mode == "":
        return None
    if mode not in MODES:
        raise CommandError(422, "invalid_mode", "mode must be \"study\" or \"demo\".")
    return mode


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = Service(settings, clock)
        app.state.service = service
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="WakeQuest service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(CommandError)
    async def _command_error(_: Request, exc: CommandError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "error": "invalid_request", "message": "The request is not valid.",
            "details": json.loads(json.dumps(exc.errors(), default=str)),
        })

    def svc(request: Request) -> Service:
        return request.app.state.service

    api = APIRouter(prefix="/api")

    # ---- state ---------------------------------------------------------
    @api.get("/state")
    async def get_state(request: Request) -> dict[str, Any]:
        return svc(request).snapshot()

    # ---- alarms --------------------------------------------------------
    @api.get("/alarms")
    async def list_alarms(request: Request) -> dict[str, Any]:
        s = svc(request)
        return {"alarms": [a.model_dump() for a in s.machine.alarms_list()],
                "armedAlarmId": s.machine.armed_alarm.id if s.machine.armed_alarm else None}

    @api.post("/alarms", status_code=201)
    async def create_alarm(request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        alarm = await svc(request).run(svc(request).machine.create_alarm, body)
        return {"alarm": alarm.model_dump()}

    @api.delete("/alarms/{alarm_id}")
    async def delete_alarm(alarm_id: str, request: Request) -> dict[str, Any]:
        await svc(request).run(svc(request).machine.delete_alarm, alarm_id)
        return {"deleted": alarm_id}

    @api.post("/alarms/disarm")
    async def disarm(request: Request) -> dict[str, Any]:
        res = await svc(request).run(svc(request).machine.disarm)
        a = res["disarmedAlarm"]
        return {"disarmedAlarm": a.model_dump() if a else None}

    @api.post("/alarms/{alarm_id}/arm")
    async def arm(alarm_id: str, request: Request) -> dict[str, Any]:
        res = await svc(request).run(svc(request).machine.arm_alarm, alarm_id)
        rep = res["replacedAlarm"]
        return {"armedAlarm": res["armedAlarm"].model_dump(),
                "replacedAlarm": rep.model_dump() if rep else None}

    # ---- device --------------------------------------------------------
    @api.post("/device/connect", status_code=202)
    async def connect(request: Request) -> dict[str, Any]:
        s = svc(request)
        await s.connect_device()
        return {"device": s.snapshot()["device"]}

    @api.post("/device/disconnect")
    async def disconnect(request: Request) -> dict[str, Any]:
        s = svc(request)
        await s.disconnect_device()
        return {"device": s.snapshot()["device"]}

    # ---- presenter -----------------------------------------------------
    @api.post("/test-alarm", status_code=202)
    async def test_alarm(request: Request) -> dict[str, Any]:
        s = svc(request)
        pt = await s.run(s.machine.schedule_test_alarm)
        return {"sessionId": pt.sessionId, "fireAt": pt.fireAt.isoformat(timespec="milliseconds"),
                "delayS": s.machine.test_alarm_delay_s}

    @api.post("/session/abort")
    async def abort(request: Request) -> dict[str, Any]:
        s = svc(request)
        await s.run(s.machine.abort, "manual")
        return {"run": s.snapshot()["run"]}

    @api.post("/demo/reset")
    async def demo_reset(request: Request) -> dict[str, Any]:
        s = svc(request)
        await s.run(s.machine.demo_reset)
        return {"run": s.snapshot()["run"]}

    # ---- log / export --------------------------------------------------
    @api.get("/events")
    async def events(request: Request, mode: str | None = None) -> dict[str, Any]:
        return {"events": filter_events(svc(request).machine.events, _mode_param(mode))}

    @api.get("/events.csv")
    async def events_csv(request: Request, mode: str | None = None) -> PlainTextResponse:
        m = _mode_param(mode)
        name = f"wakequest-events{'-' + m if m else ''}.csv"
        return PlainTextResponse(events_to_csv(svc(request).machine.events, m), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @api.get("/sessions")
    async def sessions(request: Request, mode: str | None = None) -> dict[str, Any]:
        return {"sessions": sessions_summary(svc(request).machine.events, _mode_param(mode))}

    @api.get("/sessions.csv")
    async def sessions_csv(request: Request, mode: str | None = None) -> PlainTextResponse:
        m = _mode_param(mode)
        name = f"wakequest-sessions{'-' + m if m else ''}.csv"
        return PlainTextResponse(sessions_to_csv(sessions_summary(svc(request).machine.events, m)),
                                 media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    app.include_router(api)

    # ---- test hooks (WAKEQUEST_TEST_MODE=1 only) -------------------------
    if settings.test_mode:
        app.include_router(_test_router())

    # ---- browser websocket --------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        service: Service = ws.app.state.service
        bid, queue = await service.add_browser()

        async def sender() -> None:
            while True:
                item = await queue.get()
                await ws.send_json(item)

        send_task = asyncio.create_task(sender())
        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                except ValueError:
                    queue.put_nowait({"type": "error", "error": "bad_json",
                                      "message": "Messages must be JSON."})
                    continue
                if isinstance(msg, dict) and msg.get("type") == "get_snapshot":
                    queue.put_nowait(service.snapshot())
                    continue
                try:
                    await service.browser_message(bid, msg)
                except CommandError as exc:
                    queue.put_nowait({"type": "error", **exc.body(),
                                      "inReplyTo": msg.get("type") if isinstance(msg, dict) else None})
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await send_task
            await service.remove_browser(bid)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


def _test_router() -> APIRouter:
    r = APIRouter(prefix="/api/_test")

    def fake(request: Request):
        s: Service = request.app.state.service
        if s.fake is None:
            raise CommandError(404, "no_fake_device", "Start the service with WAKEQUEST_DEVICE=fake.")
        return s.fake

    @r.get("/fake-device")
    async def fake_state(request: Request) -> dict[str, Any]:
        return fake(request).state()

    @r.post("/fake-device/tap")
    async def tap(request: Request) -> dict[str, Any]:
        body = await _json_body(request, required=False)
        if not isinstance(body, dict):
            raise CommandError(422, "invalid_request", "Body must be a JSON object.")
        sid = body["sessionId"] if "sessionId" in body else CURRENT
        seq = body.get("seq")
        msg = await fake(request).tap(sid, seq if isinstance(seq, int) else None)
        return {"sent": fake(request).connected, "tap": msg}

    @r.post("/fake-device/drop")
    async def drop(request: Request) -> dict[str, Any]:
        await fake(request).drop_connection()
        return {"ok": True}

    @r.post("/fake-device/power-off")
    async def power_off(request: Request) -> dict[str, Any]:
        await fake(request).power_off()
        return {"ok": True}

    @r.post("/fake-device/power-on")
    async def power_on(request: Request) -> dict[str, Any]:
        body = await _json_body(request, required=False)
        reboot = body.get("reboot", True) if isinstance(body, dict) else True
        await fake(request).power_on(reboot=bool(reboot))
        return {"ok": True}

    @r.post("/fake-device/no-ack")
    async def no_ack(request: Request) -> dict[str, Any]:
        body = await _json_body(request, required=False)
        withhold = body.get("enabled", True) if isinstance(body, dict) else True
        fake(request).set_acks(not withhold)
        return {"withholdAcks": bool(withhold)}

    @r.post("/fake-device/sensor-unstable")
    async def sensor_unstable(request: Request) -> dict[str, Any]:
        fake(request).set_sensor_stable(False)
        return {"sensorStable": False}

    @r.post("/fake-device/sensor-stable")
    async def sensor_stable(request: Request) -> dict[str, Any]:
        fake(request).set_sensor_stable(True)
        return {"sensorStable": True}

    @r.post("/test-alarm-delay")
    async def test_alarm_delay(request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        secs = body.get("seconds") if isinstance(body, dict) else None
        if not isinstance(secs, (int, float)) or isinstance(secs, bool):
            raise CommandError(422, "invalid_delay", "Send {\"seconds\": <number>}.")
        s: Service = request.app.state.service
        s.machine.set_test_alarm_delay(float(secs))
        return {"testAlarmDelayS": s.machine.test_alarm_delay_s}

    return r


__all__ = ["create_app", "snapshot_json"]
