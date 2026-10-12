"""Background job worker. Jobs live in the database, so they survive restarts
and several server replicas can share one queue."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from sqlalchemy import select, update

from bix.server import pipeline
from bix.server.core import Server
from bix.server.db import Job

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Live transcription first: people are looking at the screen.
PRIORITY = {"live": 0, "send": 1, "process": 2}


class Worker:
    def __init__(self, srv: Server) -> None:
        self.srv = srv
        self.name = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_maint = 0.0

    def start(self) -> None:
        self._recover()
        for i in range(max(1, self.srv.cfg.worker.threads)):
            t = threading.Thread(target=self._loop, name=f"bix-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def _recover(self) -> None:
        with self.srv.db.session() as s:
            s.execute(update(Job).where(Job.status == "running").values(status="queued", locked_by=""))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._maintenance()
                    self._stop.wait(self.srv.cfg.worker.poll_s)
            except Exception:
                log.exception("worker loop")
                self._stop.wait(5)

    def _maintenance(self) -> None:
        if time.time() - self._last_maint < 900:
            return
        self._last_maint = time.time()
        try:
            pipeline.watchdog(self.srv)
            removed = pipeline.retention(self.srv)
            if removed:
                log.info("retention removed %s", removed)
        except Exception:
            log.exception("maintenance")

    def run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        try:
            if job.kind == "process":
                if self.srv.cfg.worker.isolate:
                    run_isolated(self.srv, job.meeting_id, job.args.get("from", "assemble"))
                else:
                    pipeline.process(self.srv, job.meeting_id, job.args.get("from", "assemble"))
            elif job.kind == "live":
                pipeline.live_chunk(self.srv, job.meeting_id, int(job.args["seq"]))
            elif job.kind == "send":
                pipeline.send_minutes(self.srv, job.meeting_id, actor=job.args.get("actor", "system"))
            else:
                raise ValueError(f"unknown job {job.kind}")
            self._finish(job.id, "done")
        except Exception as e:
            retry = job.kind != "live" and job.attempts < MAX_ATTEMPTS and _transient(e)
            args = {**job.args, "from": e.bix_step} if retry and hasattr(e, "bix_step") else None
            delay = 60 * job.attempts if retry else 0
            self._finish(job.id, "queued" if retry else "failed", str(e)[:1000], delay=delay, args=args)
            if retry and job.kind == "process" and job.meeting_id:
                # Don't show "failed" while a retry is scheduled.
                from bix.server.db import Meeting

                with self.srv.db.session() as s:
                    m = s.get(Meeting, job.meeting_id)
                    if m:
                        m.status = "queued"
                        m.error = f"{e} Нов опит след {delay // 60} мин (опит {job.attempts} от {MAX_ATTEMPTS})."[:1000]
        return True

    def _claim(self) -> Job | None:
        now = time.time()
        with self.srv.db.session() as s:
            candidates = s.scalars(select(Job).where(Job.status == "queued", Job.run_after <= now).order_by(Job.id).limit(20)).all()
            for job in sorted(candidates, key=lambda j: (PRIORITY.get(j.kind, 9), j.id)):
                res = s.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.status == "queued")
                    .values(status="running", locked_by=self.name, attempts=Job.attempts + 1, updated_at=now)
                )
                if res.rowcount == 1:
                    s.commit()
                    s.refresh(job)
                    return job
        return None

    def _finish(self, job_id: int, status: str, error: str = "", delay: float = 0, args: dict | None = None) -> None:
        values = dict(status=status, error=error, updated_at=time.time(), run_after=time.time() + delay, locked_by="")
        if args is not None:
            values["args"] = args
        with self.srv.db.session() as s:
            s.execute(update(Job).where(Job.id == job_id).values(**values))


class ChildError(RuntimeError):
    """An exception raised in the processing child, carried back to the worker."""

    def __init__(self, kind: str, message: str, step: str | None, transient: bool) -> None:
        super().__init__(message)
        self.kind = kind
        self.transient = transient
        if step:
            self.bix_step = step


def _child_main(cfg, cfg_path, meeting_id: str, from_step: str, q) -> None:
    """Runs in a fresh process: its own DB connections, models and GIL."""
    from bix.common import logs
    from bix.server.core import Server

    logs.setup(cfg.dir / "logs", "worker")
    srv = Server.create(cfg, cfg_path)
    srv.bus.on(lambda ev: q.put(("event", ev.type, ev.data)))  # relay progress to the portal
    try:
        pipeline.process(srv, meeting_id, from_step)
        q.put(("ok",))
    except Exception as e:
        q.put(("error", type(e).__name__, str(e), getattr(e, "bix_step", None), _transient(e)))


def run_isolated(srv: Server, meeting_id: str, from_step: str) -> None:
    import multiprocessing as mp
    import queue as queue_mod

    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    child = ctx.Process(target=_child_main, args=(srv.cfg, srv.cfg_path, meeting_id, from_step, q), name=f"bix-process-{meeting_id[:8]}", daemon=True)
    child.start()
    result = None
    while True:
        try:
            msg = q.get(timeout=1.0)
        except queue_mod.Empty:
            if not child.is_alive():
                break
            continue
        if msg[0] == "event":
            srv.bus.publish(msg[1], **msg[2])
        else:
            result = msg
            break
    child.join(timeout=30)
    if result is None:
        raise ChildError("Crash", f"Обработката спря неочаквано (код {child.exitcode}).", None, True)
    if result[0] == "error":
        _, kind, message, step, transient = result
        raise ChildError(kind, message, step, transient)


def _transient(e: Exception) -> bool:
    import httpx

    from bix.server.minutes import MinutesError
    from bix.server.stt.base import SttError

    if isinstance(e, ChildError):
        return e.transient
    if isinstance(e, (httpx.TransportError, TimeoutError, ConnectionError)):
        return True
    msg = str(e)
    if isinstance(e, (SttError, MinutesError)):
        return any(x in msg for x in ("429", " 5", "не отговаря", "Няма връзка", "твърде много"))
    return False
