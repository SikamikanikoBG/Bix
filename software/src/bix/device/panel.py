"""Room panel: the device's own screen as a local web page.

Used on the laptop and on kiosk screens. On the Bix CRT the same controls are
drawn by face.py with SDL2 instead of a browser (saves ~300 MB RAM).
Bound to localhost; state-changing calls need the X-Bix header and a local
Host header (CSRF and DNS-rebinding protection).
"""

from __future__ import annotations

import asyncio
import json
import queue as queue_mod
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from bix.device.agent import Agent

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class EnrollIn(BaseModel):
    server: str
    code: str


class StartIn(BaseModel):
    event_uid: str = ""


class AskIn(BaseModel):
    preset: str = ""
    question: str = ""


class SimPresenceIn(BaseModel):
    present: bool


class SimDoaIn(BaseModel):
    deg: int | None = None


def create_panel(agent: Agent) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS and host != agent.cfg.panel.host:
            return _json(403, {"detail": "forbidden host"})
        if request.method == "POST" and request.headers.get("x-bix") != "1":
            return _json(403, {"detail": "missing X-Bix header"})
        resp = await call_next(request)
        sim = agent.cfg.panel.simulator
        # the simulator shows the real screen inside the 3D model (an iframe of this same panel)
        resp.headers["X-Frame-Options"] = "SAMEORIGIN" if sim else "DENY"
        if sim and request.url.path == "/sim":
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; frame-src 'self'; img-src 'self' data: blob:; frame-ancestors 'self'"
            )
        else:
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors " + ("'self'" if sim else "'none'")
            )
        return resp

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "panel.html", headers={"Cache-Control": "no-cache"})

    @app.get("/panel.js")
    def js() -> FileResponse:
        return FileResponse(STATIC / "panel.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/face.js")
    def face_js() -> FileResponse:
        return FileResponse(STATIC / "face.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    # --- virtual device (no hardware needed) ------------------------------------------
    SIM_FILES = {"sim.js", "asm_bix_crt_shell.json", "asm_bix_crt_bezel.json", "asm_bix_crt_top_cap.json", "asm_bix_crt_base.json"}

    @app.get("/sim")
    def sim_page() -> FileResponse:
        if not agent.cfg.panel.simulator:
            raise HTTPException(404)
        return FileResponse(STATIC / "sim" / "sim.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sim/{name}")
    def sim_file(name: str) -> FileResponse:
        if not agent.cfg.panel.simulator or name not in SIM_FILES:
            raise HTTPException(404)
        kind = "text/javascript" if name.endswith(".js") else "application/json"
        return FileResponse(STATIC / "sim" / name, media_type=kind, headers={"Cache-Control": "no-cache" if name.endswith(".js") else "max-age=3600"})

    @app.post("/api/sim/presence")
    def sim_presence(body: SimPresenceIn) -> dict:
        if not agent.cfg.panel.simulator:
            raise HTTPException(404)
        agent._on_presence(body.present)
        agent._publish_state()
        return {"ok": True}

    @app.post("/api/sim/doa")
    def sim_doa(body: SimDoaIn) -> dict:
        if not agent.cfg.panel.simulator:
            raise HTTPException(404)
        agent.sim_doa = body.deg
        return {"ok": True}

    @app.post("/api/sim/mute")
    def sim_mute() -> dict:
        """The Mute button on the microphone array: pauses a running meeting."""
        if not agent.cfg.panel.simulator:
            raise HTTPException(404)
        agent.set_muted(not agent.muted)
        return {"ok": True, "muted": agent.muted}

    @app.get("/api/status")
    def status() -> dict:
        return {**agent.status(), "live": agent.live_lines[-50:]}

    @app.post("/api/start")
    def start(body: StartIn | None = None) -> dict:
        try:
            return {"meeting_id": agent.start_meeting("panel", event_uid=(body.event_uid if body else ""))}
        except Exception as e:
            raise HTTPException(500, str(e)) from e

    @app.post("/api/mark")
    def mark() -> dict:
        try:
            return agent.mark("star")
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/keep")
    def keep() -> dict:
        agent.keep_going()
        return {"ok": True}

    @app.post("/api/suggest/dismiss")
    def dismiss() -> dict:
        agent.dismiss_suggest()
        return {"ok": True}

    @app.post("/api/ask")
    def ask(body: AskIn) -> dict:
        try:
            return agent.ask(preset=body.preset, question=body.question)
        except Exception as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/ask/listen")
    def listen() -> dict:
        try:
            agent.listen_start()
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @app.post("/api/ask/voice")
    def ask_voice() -> dict:
        try:
            return agent.listen_stop()
        except Exception as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/stop")
    def stop() -> dict:
        agent.stop_meeting("panel")
        return {"ok": True}

    @app.post("/api/mute")
    def mute() -> dict:
        agent.set_muted(True)
        return {"ok": True}

    @app.post("/api/unmute")
    def unmute() -> dict:
        agent.set_muted(False)
        return {"ok": True}

    @app.post("/api/discard")
    def discard() -> dict:
        agent.discard_meeting()
        return {"ok": True}

    @app.post("/api/enroll")
    def enroll(body: EnrollIn) -> dict:
        if agent.client.enrolled:
            raise HTTPException(409, "Устройството вече е регистрирано.")
        try:
            data = agent.client.enroll(body.server, body.code)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
        agent.sync_now()
        return {"ok": True, "org": data.get("org"), "room": data.get("room")}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        q = agent.bus.subscribe(maxsize=200)

        async def gen():
            try:
                yield "retry: 2000\n\n"
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.to_thread(q.get, True, 15)
                    except queue_mod.Empty:
                        yield ": ping\n\n"
                        continue
                    yield f"event: {ev.type}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n"
            finally:
                agent.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app


def _json(code: int, data: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(data, status_code=code)
