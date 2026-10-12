"""API for Bix devices: enrollment, heartbeat/commands, meeting upload."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from bix.server.auth import DevicePrincipal, client_ip, current_device, token_hash
from bix.server.core import key
from bix.server.db import Chunk, Device, EnrollmentCode, LiveSegment, Meeting, Org, Room, audit
from bix.server.pipeline import enqueue

router = APIRouter(prefix="/api/device")
log = logging.getLogger(__name__)

_ID = re.compile(r"^[0-9a-f]{32}$")
_enroll_failures: dict[str, list[float]] = defaultdict(list)
MAX_CHUNK_BYTES = 64 * 1024 * 1024


class EnrollIn(BaseModel):
    code: str
    serial: str = ""
    name: str = ""
    platform: str = ""
    fw_version: str = ""


@router.post("/enroll")
def enroll(body: EnrollIn, request: Request) -> dict:
    import secrets

    srv = request.app.state.srv
    ip = client_ip(request)
    now = time.time()
    _enroll_failures[ip] = [t for t in _enroll_failures[ip] if now - t < 900]
    # Codes carry ~60 bits, so 30 tries per 15 min is still far from guessable, and an
    # office enrolling many rooms behind one NAT isn't locked out by a few typos.
    if len(_enroll_failures[ip]) >= 30:
        raise HTTPException(429, "too many attempts")
    code = body.code.strip().upper().replace("-", "").replace(" ", "")
    with srv.db.session() as s:
        ec = s.get(EnrollmentCode, token_hash(code))
        if not ec or ec.used_at or ec.expires_at < now:
            _enroll_failures[ip].append(now)
            audit(s, f"ip:{ip}", "device.enroll_failed", ip=ip)
            raise HTTPException(403, "Невалиден или изтекъл код за регистрация.")
        token = secrets.token_urlsafe(32)
        room = s.get(Room, ec.room_id) if ec.room_id else None
        d = Device(
            org_id=ec.org_id, room_id=ec.room_id, name=body.name or (room.name if room else "Bix"), serial=body.serial[:100],
            token_hash=token_hash(token), platform=body.platform[:100], fw_version=body.fw_version[:50], ip=ip, last_seen=now,
        )
        s.add(d)
        s.flush()
        ec.used_at = now
        ec.device_id = d.id
        audit(s, f"device:{d.id}", "device.enrolled", target=d.id, org_id=ec.org_id, ip=ip, serial=body.serial, room=room.name if room else "")
        org = s.get(Org, ec.org_id)
        return {"device_id": d.id, "token": token, "org": org.name, "room": room.name if room else "", "server_time": now}


class HeartbeatIn(BaseModel):
    state: str = "idle"
    fw_version: str = ""
    platform: str = ""
    meeting_id: str | None = None
    telemetry: dict = Field(default_factory=dict)
    acked_commands: list[str] = Field(default_factory=list)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    srv = request.app.state.srv
    with srv.db.session() as s:
        d = s.get(Device, dev.device_id)
        d.last_seen = time.time()
        d.state = body.state[:32]
        d.fw_version = body.fw_version[:50] or d.fw_version
        d.platform = body.platform[:100] or d.platform
        d.ip = client_ip(request)
        d.telemetry = {k: body.telemetry[k] for k in list(body.telemetry)[:30]}
        pending = [c for c in d.commands or [] if c.get("id") not in body.acked_commands and c.get("expires", 0) > time.time()]
        d.commands = pending
        org = s.get(Org, d.org_id)
        room = s.get(Room, d.room_id) if d.room_id else None
        cfg = org.all_settings()
        srv.bus.publish("device", id=d.id, state=d.state)
        agenda: list[dict] = []
        if room:
            try:
                agenda = srv.calendars.agenda(s, room)
            except Exception as e:  # a broken calendar must not take the device offline
                log.warning("agenda for room %s: %s", room.id, e)
        return {
            "server_time": time.time(),
            "config": {
                "org": org.name,
                "room": room.name if room else "",
                "language": cfg["language"],
                "live_transcription": cfg["live_transcription"],
                "notice_text": cfg["notice_text"],
                "auto_stop_silence_min": cfg["auto_stop_silence_min"],
                "max_meeting_hours": cfg["max_meeting_hours"],
                "speaker_mode": cfg["speaker_mode"],
                "face_style": cfg["face_style"],
            },
            "agenda": agenda,
            "last_meeting": _last_meeting(s, d.id),
            "commands": pending,
        }


def _last_meeting(s, device_id: str) -> dict | None:
    """What happened to the last meeting in this room: the room screen says it."""
    m = s.scalars(
        select(Meeting).where(Meeting.device_id == device_id, Meeting.status != "recording").order_by(Meeting.started_at.desc()).limit(1)
    ).first()
    if not m:
        return None
    return {
        "id": m.id, "title": m.title, "status": m.status, "started_at": m.started_at, "ended_at": m.ended_at,
        "sent": len(m.sent_to or []), "sent_at": m.sent_at, "error": (m.error or "")[:200],
        "unnamed": sum(1 for x in m.speakers or [] if not x.get("name")),
    }


class MeetingIn(BaseModel):
    id: str
    started_at: float
    title: str = ""
    calendar_uid: str = ""


@router.post("/meetings")
def start_meeting(body: MeetingIn, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    srv = request.app.state.srv
    if not _ID.match(body.id):
        raise HTTPException(400, "bad meeting id")
    with srv.db.session() as s:
        m = s.get(Meeting, body.id)
        if m:
            if m.device_id != dev.device_id:
                raise HTTPException(409, "id taken")
            return {"id": m.id, "status": m.status}
        from datetime import datetime

        m = Meeting(
            id=body.id, org_id=dev.org_id, room_id=dev.room_id, device_id=dev.device_id, source="device",
            title=body.title or f"Среща {datetime.fromtimestamp(body.started_at):%d.%m.%Y %H:%M}",
            title_auto=not body.title, started_at=body.started_at, status="recording",
        )
        room = s.get(Room, dev.room_id) if dev.room_id else None
        if body.calendar_uid and room:  # started from the room screen for a booked meeting
            try:
                ev = srv.calendars.find(s, room, body.calendar_uid[:300], body.started_at)
            except Exception as e:
                ev = None
                log.warning("calendar lookup: %s", e)
            if ev:
                from bix.server.pipeline import apply_event

                apply_event(m, ev)
            else:
                m.calendar_uid = body.calendar_uid[:300]
        s.add(m)
        audit(s, dev.actor, "meeting.started", target=m.id, org_id=dev.org_id)
    srv.bus.publish("meeting", id=body.id, status="recording")
    return {"id": body.id, "status": "recording"}


async def _as_flac(body: bytes) -> bytes:
    """Devices send FLAC; browsers send 16 kHz WAV, kept as FLAC like everything else."""
    if body[:4] == b"fLaC":
        return body
    if body[:4] == b"RIFF" and body[8:12] == b"WAVE":
        import asyncio

        from bix.common import audio as au

        def convert() -> bytes:
            return au.encode_flac(au.decode(body))

        try:
            return await asyncio.to_thread(convert)
        except Exception as e:
            raise HTTPException(415, "unreadable WAV") from e
    raise HTTPException(415, "chunks must be FLAC or WAV")


def _own_meeting(s, mid: str, dev: DevicePrincipal) -> Meeting:
    m = s.get(Meeting, mid) if _ID.match(mid) else None
    if not m or m.device_id != dev.device_id:
        raise HTTPException(404, "meeting not found")
    return m


@router.put("/meetings/{mid}/chunks/{seq}")
async def upload_chunk(mid: str, seq: int, start_s: float, duration_s: float, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    srv = request.app.state.srv
    if seq < 0 or seq > 100000:
        raise HTTPException(400, "bad seq")
    body = await request.body()
    if not body or len(body) > MAX_CHUNK_BYTES:
        raise HTTPException(413, "bad chunk size")
    body = await _as_flac(body)
    with srv.db.session() as s:
        m = _own_meeting(s, mid, dev)
        if m.status not in ("recording", "uploading"):
            return {"ok": True, "ignored": m.status}
        if s.get(Chunk, (mid, seq)):
            return {"ok": True, "duplicate": True}
        k = key(m.org_id, mid, f"chunks/{seq:06d}.flac")
        srv.storage.put(k, body, "audio/flac")
        s.add(Chunk(meeting_id=mid, seq=seq, start_s=start_s, duration_s=duration_s, key=k, size=len(body)))
        live = s.get(Org, m.org_id).setting("live_transcription") and m.status == "recording"
        if live:
            from bix.server.db import Job

            s.add(Job(kind="live", meeting_id=mid, args={"seq": seq}))
        s.flush()
        _maybe_queue(s, m)
    return {"ok": True}


class NoteIn(BaseModel):
    t: float
    kind: str = "star"
    text: str = ""


class EndIn(BaseModel):
    ended_at: float
    chunks: int
    duration_s: float = 0.0
    notes: list[NoteIn] = Field(default_factory=list, max_length=200)


@router.post("/meetings/{mid}/end")
def end_meeting(mid: str, body: EndIn, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    srv = request.app.state.srv
    with srv.db.session() as s:
        m = _own_meeting(s, mid, dev)
        if m.status in ("recording", "uploading"):
            m.ended_at = body.ended_at
            m.expected_chunks = body.chunks
            m.duration_s = body.duration_s
            if body.notes:  # stars and notes made on the device (kept there while offline)
                m.notes = [*(m.notes or []), *[{"t": n.t, "kind": n.kind if n.kind in ("star", "note") else "note", "text": n.text[:500]} for n in body.notes]][-200:]
            m.status = "uploading"
            audit(s, dev.actor, "meeting.ended", target=mid, org_id=m.org_id, duration_s=round(body.duration_s))
            s.flush()
            _maybe_queue(s, m)
        status = m.status
    srv.bus.publish("meeting", id=mid, status=status)
    return {"status": status}


def _maybe_queue(s, m: Meeting) -> None:
    if m.status != "uploading" or m.expected_chunks is None:
        return
    have = s.scalar(select(func.count()).select_from(Chunk).where(Chunk.meeting_id == m.id))
    if have >= m.expected_chunks:
        m.status = "queued"
        enqueue(s, "process", m.id)


@router.post("/meetings/{mid}/discard")
def discard_meeting(mid: str, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    """Somebody in the room asked not to record: delete everything at once."""
    srv = request.app.state.srv
    from bix.server.core import prefix

    with srv.db.session() as s:
        m = _own_meeting(s, mid, dev)
        srv.storage.delete_prefix(prefix(m.org_id, mid))
        audit(s, dev.actor, "meeting.discarded_on_device", target=mid, org_id=m.org_id)
        s.delete(m)
    return {"ok": True}


class AskIn(BaseModel):
    question: str = ""
    preset: str = ""


@router.post("/meetings/{mid}/ask")
def ask(mid: str, body: AskIn, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    """A question about the meeting so far, from the room ("Ask Bix")."""
    from bix.server import minutes as minutes_mod
    from bix.server import pipeline

    srv = request.app.state.srv
    question = (minutes_mod.PRESETS.get(body.preset) or body.question).strip()[:500]
    if not question:
        raise HTTPException(400, "Няма въпрос.")
    with srv.db.session() as s:
        m = _own_meeting(s, mid, dev)
        if m.status not in ("recording", "uploading"):
            raise HTTPException(409, "Срещата вече е приключила.")
        audit(s, dev.actor, "meeting.asked", target=mid, org_id=m.org_id, preset=body.preset)
    try:
        return {"question": question, **pipeline.ask(srv, mid, question)}
    except Exception as e:
        log.warning("ask %s: %s", mid, e)
        raise HTTPException(502, f"Не успях да отговоря: {str(e)[:200]}") from e


@router.post("/meetings/{mid}/ask-voice")
async def ask_voice(mid: str, request: Request, dev: DevicePrincipal = Depends(current_device)) -> dict:
    """The same, asked out loud: the body is the question as FLAC."""
    import asyncio

    from bix.common import audio as au
    from bix.server import stt as stt_mod

    srv = request.app.state.srv
    body = await request.body()
    if not body or len(body) > 8 * 1024 * 1024:
        raise HTTPException(413, "the question must be up to 8 MB")
    body = await _as_flac(body)
    with srv.db.session() as s:
        m = _own_meeting(s, mid, dev)
        language = s.get(Org, m.org_id).setting("language")

    def transcribe() -> str:
        segs = stt_mod.make(srv.cfg).transcribe(au.decode(body), language)
        return " ".join(x.text for x in segs).strip()

    try:
        question = await asyncio.to_thread(transcribe)
    except Exception as e:
        raise HTTPException(502, f"Не разбрах въпроса: {str(e)[:200]}") from e
    if not question:
        raise HTTPException(422, "Не чух въпрос. Опитайте пак, по-близо до устройството.")
    return await asyncio.to_thread(ask, mid, AskIn(question=question), request, dev)


@router.get("/meetings/{mid}/live")
def live(mid: str, request: Request, after: int = 0, dev: DevicePrincipal = Depends(current_device)) -> dict:
    srv = request.app.state.srv
    with srv.db.session() as s:
        _own_meeting(s, mid, dev)
        rows = s.scalars(select(LiveSegment).where(LiveSegment.meeting_id == mid, LiveSegment.id > after).order_by(LiveSegment.id).limit(200)).all()
        return {"segments": [{"id": r.id, "start": r.start_s, "end": r.end_s, "text": r.text} for r in rows]}
