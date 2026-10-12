"""FastAPI application: portal, device API, health and metrics."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from bix import __version__
from bix.server import api_auth, api_device, api_portal
from bix.server.core import Server
from bix.server.db import Device, Job, Meeting
from bix.server.worker import Worker

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
DEVICE_STATIC = Path(__file__).resolve().parents[1] / "device" / "static"


def create_app(srv: Server, run_worker: bool = True) -> FastAPI:
    worker = Worker(srv) if run_worker else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if worker:
            worker.start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(title="Bix", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.srv = srv
    app.state.worker = worker
    app.include_router(api_auth.router)
    app.include_router(api_device.router)
    app.include_router(api_portal.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
        if srv.cfg.http.cookie_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Вътрешна грешка на сървъра."}, status_code=500)

    @app.get("/healthz")
    def healthz() -> dict:
        with srv.db.session() as s:
            s.execute(text("SELECT 1"))
        return {"ok": True, "version": __version__}

    @app.get("/metrics")
    def metrics(request: Request) -> PlainTextResponse:
        from bix.server.auth import is_trusted

        if request.client and not is_trusted(request.client.host, srv.cfg.http.trusted_proxies + ["127.0.0.1", "::1"]):
            return PlainTextResponse("forbidden", status_code=403)
        now = time.time()
        with srv.db.session() as s:
            online = s.scalar(select(func.count()).select_from(Device).where(Device.revoked.is_(False), Device.last_seen > now - 90))
            devices = s.scalar(select(func.count()).select_from(Device).where(Device.revoked.is_(False)))
            by_status = dict(s.execute(select(Meeting.status, func.count()).group_by(Meeting.status)).all())
            jobs = dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        lines = [
            "# TYPE bix_devices gauge", f"bix_devices {devices}",
            "# TYPE bix_devices_online gauge", f"bix_devices_online {online}",
            "# TYPE bix_meetings gauge",
        ]
        lines += [f'bix_meetings{{status="{k}"}} {v}' for k, v in by_status.items()]
        lines.append("# TYPE bix_jobs gauge")
        lines += [f'bix_jobs{{status="{k}"}} {v}' for k, v in jobs.items()]
        return PlainTextResponse("\n".join(lines) + "\n")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # --- Bix in the browser: any laptop in a room is a device at /room (no install) ----------
    ROOM_FILES = {"face.js": DEVICE_STATIC / "face.js", "panel.js": DEVICE_STATIC / "panel.js",
                  "webagent.js": DEVICE_STATIC / "web" / "webagent.js", "worklet.js": DEVICE_STATIC / "web" / "worklet.js"}
    room_headers = {"Cache-Control": "no-cache", "Permissions-Policy": "microphone=(self), screen-wake-lock=(self)"}

    @app.get("/room")
    def room() -> HTMLResponse:
        html = (DEVICE_STATIC / "panel.html").read_text(encoding="utf-8")
        html = html.replace('<script src="/face.js"></script>', '<script src="/room/face.js"></script>\n<script src="/room/webagent.js"></script>')
        html = html.replace('<script src="/panel.js"></script>', '<script src="/room/panel.js"></script>')
        html = html.replace("<title>Bix · зала</title>", '<title>Bix · зала</title>\n<link rel="manifest" href="/room/manifest.json">\n<meta name="theme-color" content="#0b0d10">')
        return HTMLResponse(html, headers=room_headers)

    @app.get("/room/manifest.json")
    def room_manifest() -> JSONResponse:
        # "install as an app": full screen, no browser bars, on the laptop in the room
        return JSONResponse({"name": "Bix · зала", "short_name": "Bix", "start_url": "/room", "display": "fullscreen", "background_color": "#0b0d10", "theme_color": "#0b0d10"})

    @app.get("/room/{name}")
    def room_file(name: str) -> FileResponse:
        path = ROOM_FILES.get(name)
        if path is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(path, media_type="text/javascript", headers=room_headers)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app
