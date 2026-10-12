"""Portal API: meetings, rooms, devices, people, users, settings, audit."""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from bix.common import audio as au
from bix.common.settings import redacted
from bix.server import pipeline, speakers as spk
from bix.server.auth import (
    Principal,
    admin_only,
    any_user,
    can_read_meeting,
    client_ip,
    content_reader,
    hash_password,
    password_problems,
    require,
    token_hash,
)
from bix.server.core import key, prefix
from bix.server.db import (
    DEFAULT_ORG_SETTINGS,
    ROLES,
    AuditLog,
    Device,
    EnrollmentCode,
    Job,
    LiveSegment,
    Meeting,
    Org,
    Person,
    Room,
    User,
    audit,
)

FACE_STYLES = ("pixel", "capsule", "eve", "dots", "brows", "marker", "phosphor", "terminal", "orb")

router = APIRouter(prefix="/api")

ONLINE_S = 90
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


def clean_emails(values: list[str], field: str) -> list[str]:
    """Normalise a list of addresses; reject the request if any is invalid."""
    out = [v.strip().lower() for v in values if v and v.strip()]
    bad = [v for v in out if not EMAIL.fullmatch(v)]
    if bad:
        raise HTTPException(400, f"Невалиден имейл в „{field}“: {', '.join(bad)}")
    return list(dict.fromkeys(out))


def clean_email(value: str, field: str) -> str:
    return clean_emails([value], field)[0] if value and value.strip() else ""
admin_or_auditor = require("admin", "auditor")


def srv_of(request: Request):
    return request.app.state.srv


# --- overview ----------------------------------------------------------------


@router.get("/overview")
def overview(request: Request, p: Principal = Depends(any_user)) -> dict:
    srv = srv_of(request)
    now = time.time()
    with srv.db.session() as s:
        org = s.get(Org, p.org_id)
        devices = s.scalars(select(Device).where(Device.org_id == p.org_id, Device.revoked.is_(False))).all()
        month = now - 30 * 86400
        q = select(Meeting).where(Meeting.org_id == p.org_id, Meeting.started_at >= month)
        meetings = s.scalars(q).all()
        if p.user.role == "member":
            # the same meetings the member's list shows; org-wide numbers would leak activity
            meetings = [m for m in meetings if can_read_meeting(s, p, m)]
        jobs = s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"])))
        if p.user.role == "member":
            devices = []
        return {
            "org": org.name,
            "devices_total": len(devices),
            "devices_online": sum(1 for d in devices if d.last_seen and now - d.last_seen < ONLINE_S),
            "recording_now": sum(1 for d in devices if d.state == "recording" and d.last_seen and now - d.last_seen < ONLINE_S),
            "meetings_30d": len(meetings),
            "hours_30d": round(sum(m.duration_s for m in meetings) / 3600, 1),
            "seconds_30d": round(sum(m.duration_s for m in meetings)),
            "with_minutes_30d": sum(1 for m in meetings if m.minutes),
            "failed_30d": sum(1 for m in meetings if m.status == "failed"),
            "sent_30d": sum(1 for m in meetings if m.sent_at),
            "jobs_pending": jobs,
        }


# --- meetings ----------------------------------------------------------------


def meeting_summary(m: Meeting, rooms: dict[str, str]) -> dict[str, Any]:
    return {
        "id": m.id, "title": m.title, "room": rooms.get(m.room_id or "", ""), "room_id": m.room_id, "status": m.status,
        "started_at": m.started_at, "ended_at": m.ended_at, "duration_s": m.duration_s, "source": m.source,
        "speakers": [x.get("name") or x["label"] for x in m.speakers or []], "has_minutes": bool(m.minutes),
        "sent_at": m.sent_at, "error": m.error, "has_audio": m.has_audio,
    }


@router.get("/meetings")
def list_meetings(request: Request, q: str = "", room_id: str = "", status: str = "", limit: int = 50, offset: int = 0, p: Principal = Depends(any_user)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        rooms = {r.id: r.name for r in s.scalars(select(Room).where(Room.org_id == p.org_id)).all()}
        stmt = select(Meeting).where(Meeting.org_id == p.org_id)
        if room_id:
            stmt = stmt.where(Meeting.room_id == room_id)
        if status:
            stmt = stmt.where(Meeting.status == status)
        if q and p.user.role != "auditor":
            # Explicit lower(): SQLite renders ILIKE as LIKE, which folds only ASCII.
            like = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(or_(func.lower(Meeting.title).like(like, escape="\\"), func.lower(Meeting.transcript_text).like(like, escape="\\")))
        stmt = stmt.order_by(Meeting.started_at.desc())
        rows = s.scalars(stmt).all()
        if p.user.role == "member":
            rows = [m for m in rows if can_read_meeting(s, p, m)]
        total = len(rows)
        rows = rows[offset : offset + min(limit, 200)]
        items = [meeting_summary(m, rooms) for m in rows]
        if p.user.role == "auditor":
            for it in items:
                it["title"] = "—"
                it["speakers"] = []
        return {"total": total, "items": items}


def _meeting_for(s, p: Principal, mid: str) -> Meeting:
    m = s.get(Meeting, mid) if re.fullmatch(r"[0-9a-f]{32}", mid) else None
    if not m or not can_read_meeting(s, p, m):
        raise HTTPException(404, "Срещата не е намерена.")
    return m


@router.get("/meetings/{mid}")
def get_meeting(mid: str, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        rooms = {r.id: r.name for r in s.scalars(select(Room).where(Room.org_id == p.org_id)).all()}
        out = meeting_summary(m, rooms)
        out.update(
            speakers=m.speakers or [], steps=m.steps or {}, minutes=m.minutes, attendees=m.attendees or [],
            organizer_email=m.organizer_email, sent_to=m.sent_to or [], audio_deleted_at=m.audio_deleted_at,
            speaker_mode=s.get(Org, m.org_id).setting("speaker_mode"), notes=m.notes or [], calendar_uid=m.calendar_uid,
        )
        audit(s, p.actor, "meeting.view", target=mid, org_id=p.org_id, ip=client_ip(request))
        return out


@router.get("/meetings/{mid}/transcript")
def get_transcript(mid: str, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        org_id = m.org_id
        live = s.scalars(select(LiveSegment).where(LiveSegment.meeting_id == mid).order_by(LiveSegment.start_s)).all()
        live_rows = [{"start": r.start_s, "end": r.end_s, "text": r.text} for r in live]
    k = key(org_id, mid, "transcript.json")
    if not srv.storage.exists(k):
        return {"segments": [], "live": live_rows}
    data = json.loads(srv.storage.get(k))
    for x in data["segments"]:
        x.pop("words", None)
    data["live"] = live_rows
    return data


@router.get("/meetings/{mid}/audio")
def get_audio(mid: str, request: Request, p: Principal = Depends(content_reader)) -> Response:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        if not m.has_audio:
            raise HTTPException(404, "Звукът е изтрит по политиката за съхранение.")
        audit(s, p.actor, "meeting.audio_play", target=mid, org_id=p.org_id, ip=client_ip(request))
        k = key(m.org_id, mid, "audio.ogg")
    local = srv.storage.local_path(k)
    if local:
        from fastapi.responses import FileResponse

        return FileResponse(local, media_type="audio/ogg")
    return Response(srv.storage.get(k), media_type="audio/ogg")


@router.get("/meetings/{mid}/export.md")
def export_md(mid: str, request: Request, p: Principal = Depends(content_reader)) -> Response:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        audit(s, p.actor, "meeting.export", target=mid, org_id=p.org_id, ip=client_ip(request))
        org_id, title = m.org_id, m.title
    k = key(org_id, mid, "minutes.md")
    if not srv.storage.exists(k):
        raise HTTPException(404, "Няма минути.")
    from urllib.parse import quote

    fname = (re.sub(r"[^\w\-]+", "_", title).strip("_")[:60] or "minutes") + ".md"
    # HTTP headers are Latin-1: ASCII fallback + RFC 5987 UTF-8 name for Cyrillic titles.
    disposition = f"attachment; filename=\"minutes.md\"; filename*=UTF-8''{quote(fname)}"
    return Response(srv.storage.get(k), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": disposition})


class MeetingPatch(BaseModel):
    title: str | None = None


@router.patch("/meetings/{mid}")
def patch_meeting(mid: str, body: MeetingPatch, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(400, "Заглавието не може да е празно.")
            m.title = body.title.strip()[:300]
            m.title_auto = False
        audit(s, p.actor, "meeting.edit", target=mid, org_id=p.org_id, ip=client_ip(request))
    return {"ok": True}


class SpeakerIn(BaseModel):
    label: str
    name: str = ""
    person_id: str = ""
    # Запомни гласа: създава/обновява гласов профил. Изисква съгласие на човека.
    remember: bool = False
    consent: bool = False
    email: str = ""


@router.post("/meetings/{mid}/speakers")
def set_speaker(mid: str, body: SpeakerIn, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        org = s.get(Org, m.org_id)
        speakers = [dict(x) for x in m.speakers or []]
        entry = next((x for x in speakers if x["label"] == body.label), None)
        if not entry:
            raise HTTPException(404, "Няма такъв говорещ.")
        person = None
        if body.person_id:
            person = s.get(Person, body.person_id)
            if not person or person.org_id != p.org_id:
                raise HTTPException(404, "Няма такъв човек.")
        name = person.name if person else body.name.strip()[:200]
        if not person and name:
            # an existing person with this name (or e-mail) is linked, not duplicated
            email = clean_email(body.email, "имейл")
            person = s.scalars(
                select(Person).where(Person.org_id == p.org_id, or_(func.lower(Person.name) == name.lower(), *([Person.email == email] if email else [])))
            ).first()
            if not person and (body.remember or email):
                person = Person(org_id=p.org_id, name=name, email=email, embedding=[], samples=0, seconds=0.0)
                s.add(person)
                s.flush()
            elif email and not person.email:
                person.email = email
        if body.remember:
            if org.setting("speaker_mode") != "voice_profiles":
                raise HTTPException(400, "Гласовите профили са изключени в настройките.")
            if not body.consent and not (person and person.consent_at):
                raise HTTPException(400, "Нужно е съгласието на човека, за да се запомни гласът му.")
            embs = json.loads(srv.storage.get(key(m.org_id, mid, "embeddings.json"))) if srv.storage.exists(key(m.org_id, mid, "embeddings.json")) else {}
            if body.label not in embs:
                raise HTTPException(400, "Няма достатъчно запис на този говорещ, за да се запомни гласът.")
            import numpy as np

            vec = np.array(embs[body.label], dtype=np.float32)
            if not person:
                person = Person(org_id=p.org_id, name=name, email=clean_email(body.email, "имейл"), embedding=[], samples=0, seconds=0.0)
                s.add(person)
            if body.consent and not person.consent_at:
                person.consent_at = time.time()
                person.consent_by = p.user.email
                person.consent_note = f"Потвърдено в портала при среща {mid}"
            person.embedding = spk.update_profile(person.embedding, person.samples, vec)
            person.samples += 1
            person.seconds += float(entry.get("seconds") or 0)
            s.flush()
            audit(s, p.actor, "voiceprint.update", target=person.id, org_id=p.org_id, ip=client_ip(request), from_meeting=mid)
        entry.update(name=name, source="user", person_id=person.id if person else "")
        m.speakers = speakers
        audit(s, p.actor, "meeting.speaker_named", target=mid, org_id=p.org_id, ip=client_ip(request), label=body.label)
    return {"ok": True, "person_id": person.id if person else ""}


class ReprocessIn(BaseModel):
    step: str = "minutes"


@router.post("/meetings/{mid}/reprocess")
def reprocess(mid: str, body: ReprocessIn, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    if body.step not in pipeline.STEPS:
        raise HTTPException(400, "Непозната стъпка.")
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        if m.status in ("recording", "uploading", "processing"):
            raise HTTPException(409, "Срещата още се записва или обработва.")
        if body.step != "assemble" and not m.has_audio and body.step in ("stt", "speakers"):
            raise HTTPException(409, "Звукът вече е изтрит.")
        m.status = "queued"
        pipeline.enqueue(s, "process", mid, **{"from": body.step})
        audit(s, p.actor, "meeting.reprocess", target=mid, org_id=p.org_id, step=body.step)
    return {"ok": True}


class SendIn(BaseModel):
    to: list[str] = Field(default_factory=list)


@router.post("/meetings/{mid}/send")
def send(mid: str, body: SendIn, request: Request, p: Principal = Depends(content_reader)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        if not m.minutes:
            raise HTTPException(409, "Няма минути за изпращане.")
    to = clean_emails(body.to, "получатели")
    try:
        return pipeline.send_minutes(srv, mid, actor=p.actor, to=to or None)
    except Exception as e:
        raise HTTPException(502, str(e)) from e


@router.delete("/meetings/{mid}")
def delete_meeting(mid: str, request: Request, p: Principal = Depends(admin_only)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        m = _meeting_for(s, p, mid)
        srv.storage.delete_prefix(prefix(m.org_id, mid))
        audit(s, p.actor, "meeting.delete", target=mid, org_id=p.org_id, ip=client_ip(request), title=m.title)
        s.delete(m)
    return {"ok": True}


@router.post("/meetings/upload")
async def upload(request: Request, file: UploadFile = File(...), title: str = Form(""), room_id: str = Form(""), p: Principal = Depends(content_reader)) -> dict:
    """Process an existing recording (phone, Teams/Zoom export)."""
    srv = srv_of(request)
    import asyncio

    data = await file.read()
    if len(data) > 1024 * 1024 * 1024:
        raise HTTPException(413, "Файлът е над 1 GB.")
    try:
        # decoding a long recording takes seconds: keep it off the event loop
        audio = await asyncio.to_thread(au.decode, data)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    flac, ogg = await asyncio.gather(asyncio.to_thread(au.encode_flac, audio), asyncio.to_thread(au.encode_opus, audio))
    if len(audio) < au.SR * 2:
        raise HTTPException(400, "Записът е твърде кратък.")
    with srv.db.session() as s:
        if room_id and not (r := s.get(Room, room_id)) or (room_id and r.org_id != p.org_id):
            raise HTTPException(404, "Няма такава зала.")
        now = time.time()
        m = Meeting(
            org_id=p.org_id, room_id=room_id or None, source="upload", created_by=p.user.id,
            title=title.strip() or (file.filename or "Запис").rsplit(".", 1)[0], title_auto=not title.strip(), started_at=now, ended_at=now,
            duration_s=len(audio) / au.SR, status="queued", has_audio=True,
        )
        s.add(m)
        s.flush()
        srv.storage.put(key(p.org_id, m.id, "audio.flac"), flac, "audio/flac")
        srv.storage.put(key(p.org_id, m.id, "audio.ogg"), ogg, "audio/ogg")
        pipeline.enqueue(s, "process", m.id, **{"from": "calendar"})
        audit(s, p.actor, "meeting.upload", target=m.id, org_id=p.org_id, ip=client_ip(request), seconds=round(len(audio) / au.SR))
        return {"id": m.id}


# --- rooms -------------------------------------------------------------------


class RoomIn(BaseModel):
    name: str
    location: str = ""
    mailbox: str = ""
    recipients: list[str] = Field(default_factory=list)
    # None keeps the current link (the portal never shows it to non-admins); "" removes it
    calendar_ics_url: str | None = None


def _room_dict(r: Room, admin: bool = False) -> dict:
    d = {"id": r.id, "name": r.name, "location": r.location, "mailbox": r.mailbox, "recipients": r.recipients or [], "has_ics": bool(r.calendar_ics_url)}
    if admin:  # a secret iCal address gives read access to the calendar
        d["calendar_ics_url"] = r.calendar_ics_url
    return d


def clean_ics(url: str | None, current: str) -> str:
    if url is None:
        return current
    url = url.strip()
    if url and not url.lower().startswith(("https://", "webcal://", "http://")):
        raise HTTPException(400, "iCal адресът трябва да започва с https:// или webcal://")
    return url[:2000]


@router.get("/rooms")
def rooms(request: Request, p: Principal = Depends(any_user)) -> list[dict]:
    with srv_of(request).db.session() as s:
        return [_room_dict(r, p.is_admin()) for r in s.scalars(select(Room).where(Room.org_id == p.org_id).order_by(Room.name)).all()]


@router.post("/rooms")
def create_room(body: RoomIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        if not body.name.strip():
            raise HTTPException(400, "Залата трябва да има име.")
        r = Room(
            org_id=p.org_id, name=body.name.strip()[:200], location=body.location[:200], mailbox=clean_email(body.mailbox, "календар"),
            recipients=clean_emails(body.recipients, "получатели"), calendar_ics_url=clean_ics(body.calendar_ics_url, ""),
        )
        s.add(r)
        s.flush()
        audit(s, p.actor, "room.create", target=r.id, org_id=p.org_id, name=r.name)
        return _room_dict(r, True)


@router.patch("/rooms/{rid}")
def update_room(rid: str, body: RoomIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        r = s.get(Room, rid)
        if not r or r.org_id != p.org_id:
            raise HTTPException(404)
        if not body.name.strip():
            raise HTTPException(400, "Залата трябва да има име.")
        r.name, r.location = body.name.strip()[:200], body.location[:200]
        r.mailbox, r.recipients = clean_email(body.mailbox, "календар"), clean_emails(body.recipients, "получатели")
        ics = clean_ics(body.calendar_ics_url, r.calendar_ics_url)
        changed_ics = ics != r.calendar_ics_url
        r.calendar_ics_url = ics
        audit(s, p.actor, "room.update", target=rid, org_id=p.org_id, **({"calendar_ics": "changed"} if changed_ics else {}))
        return _room_dict(r, True)


@router.delete("/rooms/{rid}")
def delete_room(rid: str, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        r = s.get(Room, rid)
        if not r or r.org_id != p.org_id:
            raise HTTPException(404)
        if s.scalar(select(func.count()).select_from(Device).where(Device.room_id == rid, Device.revoked.is_(False))):
            raise HTTPException(409, "В залата има устройства. Преместете ги първо.")
        for m in s.scalars(select(Meeting).where(Meeting.room_id == rid)).all():
            m.room_id = None
        s.delete(r)
        audit(s, p.actor, "room.delete", target=rid, org_id=p.org_id)
    return {"ok": True}


# --- room calendar & bookings --------------------------------------------------


class BookingIn(BaseModel):
    title: str
    start: float
    end: float
    organizer_name: str = ""
    organizer_email: str = ""
    # "Име Фамилия <email>", "email" or just "Име"
    attendees: list[str] = Field(default_factory=list, max_length=200)
    repeat: str = ""


def _parse_person(text: str) -> dict[str, str]:
    import re

    text = text.strip()
    m = re.fullmatch(r"(.*?)\s*<([^>]+)>", text)
    name, email = (m.group(1).strip().strip('"'), m.group(2).strip()) if m else (("", text) if "@" in text else (text, ""))
    return {"name": name[:200], "email": clean_email(email, "участници")}


def _own_room(s, rid: str, p: Principal) -> Room:
    r = s.get(Room, rid)
    if not r or r.org_id != p.org_id:
        raise HTTPException(404)
    return r


@router.get("/rooms/{rid}/calendar")
def room_calendar(rid: str, request: Request, days: int = 7, p: Principal = Depends(any_user)) -> dict:
    """What is booked in the room (all calendar sources), today and the next days."""
    from datetime import datetime

    srv = srv_of(request)
    days = max(1, min(int(days), 31))
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with srv.db.session() as s:
        r = _own_room(s, rid, p)
        evs = srv.calendars.events(s, r, start, start + days * 86400)
        sources = {"bookings": True, "ics": bool(r.calendar_ics_url), "m365": bool(r.mailbox and srv.graph.enabled)}
        problem = srv.calendars.errors.get(r.id, "")
    for e in evs:
        e["booking_id"] = e["uid"].split(":")[1] if e["source"] == "bix" else None
    return {"events": evs, "sources": sources, "problem": problem}


@router.post("/rooms/{rid}/bookings")
def create_booking(rid: str, body: BookingIn, request: Request, p: Principal = Depends(content_reader)) -> dict:
    from bix.server.db import Booking

    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Резервацията трябва да има тема.")
    if body.end <= body.start or body.end - body.start > 12 * 3600:
        raise HTTPException(400, "Краят трябва да е след началото (до 12 часа).")
    if body.repeat not in ("", "weekly"):
        raise HTTPException(400, "Повторение: само всяка седмица.")
    with srv_of(request).db.session() as s:
        _own_room(s, rid, p)
        b = Booking(
            org_id=p.org_id, room_id=rid, title=title[:300], start=body.start, end=body.end,
            organizer_name=body.organizer_name.strip()[:200], organizer_email=clean_email(body.organizer_email, "организатор"),
            attendees=[_parse_person(a) for a in body.attendees if a.strip()], repeat=body.repeat, created_by=p.user.id,
        )
        s.add(b)
        s.flush()
        audit(s, p.actor, "booking.create", target=b.id, org_id=p.org_id, room=rid, title=b.title)
        return {"id": b.id}


@router.delete("/bookings/{bid}")
def delete_booking(bid: str, request: Request, p: Principal = Depends(content_reader)) -> dict:
    from bix.server.db import Booking

    with srv_of(request).db.session() as s:
        b = s.get(Booking, bid)
        if not b or b.org_id != p.org_id:
            raise HTTPException(404)
        if not p.is_admin() and b.created_by != p.user.id:
            raise HTTPException(403, "Само създателят или администратор може да изтрие резервацията.")
        s.delete(b)
        audit(s, p.actor, "booking.delete", target=bid, org_id=p.org_id)
    return {"ok": True}


# --- devices -----------------------------------------------------------------


def _device_dict(d: Device, rooms: dict[str, str]) -> dict:
    now = time.time()
    return {
        "id": d.id, "name": d.name, "serial": d.serial, "room_id": d.room_id, "room": rooms.get(d.room_id or "", ""),
        "online": bool(d.last_seen and now - d.last_seen < ONLINE_S), "last_seen": d.last_seen, "state": d.state,
        "fw_version": d.fw_version, "platform": d.platform, "ip": d.ip, "telemetry": d.telemetry or {},
        "enrolled_at": d.enrolled_at, "revoked": d.revoked, "cert_pinned": bool(d.cert_fingerprint),
    }


@router.get("/devices")
def devices(request: Request, p: Principal = Depends(admin_or_auditor)) -> list[dict]:
    with srv_of(request).db.session() as s:
        rooms = {r.id: r.name for r in s.scalars(select(Room).where(Room.org_id == p.org_id)).all()}
        return [_device_dict(d, rooms) for d in s.scalars(select(Device).where(Device.org_id == p.org_id).order_by(Device.name)).all()]


class CodeIn(BaseModel):
    room_id: str = ""
    hours: int = 24


@router.post("/devices/enrollment-codes")
def enrollment_code(body: CodeIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    with srv_of(request).db.session() as s:
        if body.room_id:
            r = s.get(Room, body.room_id)
            if not r or r.org_id != p.org_id:
                raise HTTPException(404, "Няма такава зала.")
        exp = time.time() + max(1, min(body.hours, 168)) * 3600
        s.add(EnrollmentCode(code_hash=token_hash(raw), org_id=p.org_id, room_id=body.room_id or None, created_by=p.user.id, expires_at=exp))
        audit(s, p.actor, "device.enrollment_code", org_id=p.org_id, room_id=body.room_id)
    code = "-".join(raw[i : i + 4] for i in range(0, 12, 4))
    url = srv_of(request).cfg.http.public_url.rstrip("/")
    return {"code": code, "expires_at": exp, "server": url, "qr_text": f"bix://enroll?server={url}&code={code}"}


class DevicePatch(BaseModel):
    name: str | None = None
    room_id: str | None = None


@router.patch("/devices/{did}")
def patch_device(did: str, body: DevicePatch, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        d = s.get(Device, did)
        if not d or d.org_id != p.org_id:
            raise HTTPException(404)
        if body.name is not None:
            d.name = body.name.strip()[:200]
        if body.room_id is not None:
            if body.room_id and ((r := s.get(Room, body.room_id)) is None or r.org_id != p.org_id):
                raise HTTPException(404, "Няма такава зала.")
            d.room_id = body.room_id or None
        audit(s, p.actor, "device.update", target=did, org_id=p.org_id)
    return {"ok": True}


class CommandIn(BaseModel):
    type: str


DEVICE_COMMANDS = ("start", "stop", "mute", "unmute", "identify", "restart", "sync")


@router.post("/devices/{did}/command")
def command(did: str, body: CommandIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    if body.type not in DEVICE_COMMANDS:
        raise HTTPException(400, "Непозната команда.")
    with srv_of(request).db.session() as s:
        d = s.get(Device, did)
        if not d or d.org_id != p.org_id or d.revoked:
            raise HTTPException(404)
        cmd = {"id": secrets.token_hex(8), "type": body.type, "by": p.user.email, "at": time.time(), "expires": time.time() + 300}
        d.commands = (d.commands or []) + [cmd]
        audit(s, p.actor, "device.command", target=did, org_id=p.org_id, command=body.type)
    return {"ok": True, "id": cmd["id"]}


@router.post("/devices/{did}/revoke")
def revoke(did: str, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        d = s.get(Device, did)
        if not d or d.org_id != p.org_id:
            raise HTTPException(404)
        d.revoked = True
        d.token_hash = token_hash(secrets.token_hex(32))
        audit(s, p.actor, "device.revoke", target=did, org_id=p.org_id)
    return {"ok": True}


# --- people / voice profiles ---------------------------------------------------


def _person_dict(x: Person) -> dict:
    return {
        "id": x.id, "name": x.name, "email": x.email, "has_voice": bool(x.embedding), "samples": x.samples,
        "seconds": round(x.seconds), "consent_at": x.consent_at, "consent_by": x.consent_by, "created_at": x.created_at,
    }


@router.get("/people")
def people(request: Request, p: Principal = Depends(content_reader)) -> list[dict]:
    with srv_of(request).db.session() as s:
        return [_person_dict(x) for x in s.scalars(select(Person).where(Person.org_id == p.org_id).order_by(Person.name)).all()]


class PersonIn(BaseModel):
    name: str
    email: str = ""


@router.post("/people")
def create_person(body: PersonIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        if not body.name.strip():
            raise HTTPException(400, "Въведете име.")
        x = Person(org_id=p.org_id, name=body.name.strip()[:200], email=clean_email(body.email, "имейл"), embedding=[], samples=0, seconds=0.0)
        s.add(x)
        s.flush()
        audit(s, p.actor, "person.create", target=x.id, org_id=p.org_id)
        return _person_dict(x)


@router.patch("/people/{pid}")
def update_person(pid: str, body: PersonIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        x = s.get(Person, pid)
        if not x or x.org_id != p.org_id:
            raise HTTPException(404)
        if not body.name.strip():
            raise HTTPException(400, "Въведете име.")
        x.name, x.email = body.name.strip()[:200], clean_email(body.email, "имейл")
        audit(s, p.actor, "person.update", target=pid, org_id=p.org_id)
        return _person_dict(x)


@router.post("/people/{pid}/voice")
async def enroll_voice(pid: str, request: Request, file: UploadFile = File(...), consent: bool = Form(False), p: Principal = Depends(admin_only)) -> dict:
    """Voice sample (10-60 s of the person speaking) → voice profile. Needs consent."""
    srv = srv_of(request)
    if not consent:
        raise HTTPException(400, "Нужно е съгласието на човека.")
    audio = au.decode(await file.read())
    if len(audio) < au.SR * 5:
        raise HTTPException(400, "Пробата трябва да е поне 5 секунди.")
    from bix.common import models
    from bix.common.vad import speech_regions

    models.ensure(srv.cfg.models_dir, srv.cfg.diarization.embedding_model)
    regions = speech_regions(audio, srv.cfg.models_dir / models.VAD)
    speech = [audio[int(a * au.SR) : int(b * au.SR)] for a, b in regions]
    if not speech or sum(len(x) for x in speech) < au.SR * 4:
        raise HTTPException(400, "В пробата няма достатъчно реч.")
    import numpy as np

    vec = spk.embed(np.concatenate(speech), srv.cfg.models_dir, srv.cfg.diarization)
    with srv.db.session() as s:
        x = s.get(Person, pid)
        if not x or x.org_id != p.org_id:
            raise HTTPException(404)
        x.embedding = spk.update_profile(x.embedding, x.samples, vec)
        x.samples += 1
        x.seconds += sum(len(y) for y in speech) / au.SR
        if not x.consent_at:
            x.consent_at, x.consent_by, x.consent_note = time.time(), p.user.email, "Проба, записана в портала"
        audit(s, p.actor, "voiceprint.enroll", target=pid, org_id=p.org_id, ip=client_ip(request))
        return _person_dict(x)


@router.delete("/people/{pid}/voice")
def forget_voice(pid: str, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        x = s.get(Person, pid)
        if not x or x.org_id != p.org_id:
            raise HTTPException(404)
        x.embedding, x.samples, x.seconds, x.consent_at = [], 0, 0.0, None
        audit(s, p.actor, "voiceprint.delete", target=pid, org_id=p.org_id, ip=client_ip(request))
    return {"ok": True}


@router.delete("/people/{pid}")
def erase_person(pid: str, request: Request, p: Principal = Depends(admin_only)) -> dict:
    """GDPR erasure: voice profile, the person record, and their name on meetings."""
    with srv_of(request).db.session() as s:
        x = s.get(Person, pid)
        if not x or x.org_id != p.org_id:
            raise HTTPException(404)
        touched = 0
        for m in s.scalars(select(Meeting).where(Meeting.org_id == p.org_id)).all():
            sp = [dict(y) for y in m.speakers or []]
            changed = False
            for y in sp:
                if y.get("person_id") == pid:
                    y.update(name="", person_id="", source="")
                    changed = True
            if changed:
                m.speakers = sp
                touched += 1
        s.delete(x)
        audit(s, p.actor, "person.erase", target=pid, org_id=p.org_id, ip=client_ip(request), meetings_touched=touched)
    return {"ok": True, "meetings_touched": touched}


# --- users ---------------------------------------------------------------------


def _user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active, "sso": bool(u.oidc_sub), "last_login": u.last_login, "created_at": u.created_at}


@router.get("/users")
def users(request: Request, p: Principal = Depends(admin_only)) -> list[dict]:
    with srv_of(request).db.session() as s:
        return [_user_dict(u) for u in s.scalars(select(User).where(User.org_id == p.org_id).order_by(User.email)).all()]


class UserIn(BaseModel):
    email: str
    name: str = ""
    role: str = "member"
    password: str = ""


@router.post("/users")
def create_user(body: UserIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    if body.role not in ROLES:
        raise HTTPException(400, "Непозната роля.")
    if body.password and (problem := password_problems(body.password)):
        raise HTTPException(400, problem)
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Невалиден имейл.")
    with srv_of(request).db.session() as s:
        if s.scalars(select(User).where(func.lower(User.email) == email)).first():
            raise HTTPException(409, "Вече има такъв потребител.")
        u = User(org_id=p.org_id, email=email, name=body.name.strip(), role=body.role, password_hash=hash_password(body.password) if body.password else None)
        s.add(u)
        s.flush()
        audit(s, p.actor, "user.create", target=u.id, org_id=p.org_id, email=email, role=body.role)
        return _user_dict(u)


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, request: Request, p: Principal = Depends(admin_only)) -> dict:
    with srv_of(request).db.session() as s:
        u = s.get(User, uid)
        if not u or u.org_id != p.org_id:
            raise HTTPException(404)
        if u.id == p.user.id and (body.role not in (None, "admin") or body.active is False):
            raise HTTPException(400, "Не можете да махнете собствените си администраторски права.")
        if body.role is not None:
            if body.role not in ROLES:
                raise HTTPException(400, "Непозната роля.")
            u.role = body.role
        if body.name is not None:
            u.name = body.name.strip()
        if body.active is not None:
            u.active = body.active
        if body.password:
            if problem := password_problems(body.password):
                raise HTTPException(400, problem)
            u.password_hash = hash_password(body.password)
        audit(s, p.actor, "user.update", target=uid, org_id=p.org_id, **{k: v for k, v in body.model_dump().items() if v is not None and k != "password"})
        return _user_dict(u)


# --- settings, status, audit ---------------------------------------------------


@router.get("/settings")
def get_settings(request: Request, p: Principal = Depends(any_user)) -> dict:
    srv = srv_of(request)
    with srv.db.session() as s:
        org = s.get(Org, p.org_id)
        out = {"org_name": org.name, "settings": org.all_settings()}
    if p.is_admin():
        cfg = redacted(srv.cfg)
        cfg.pop("auth", None)
        out["server"] = cfg
        out["config_path"] = str(srv.cfg_path)
    return out


class SettingsIn(BaseModel):
    org_name: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


@router.patch("/settings")
def patch_settings(body: SettingsIn, request: Request, p: Principal = Depends(admin_only)) -> dict:
    allowed = set(DEFAULT_ORG_SETTINGS)
    with srv_of(request).db.session() as s:
        org = s.get(Org, p.org_id)
        if body.org_name:
            org.name = body.org_name.strip()[:200]
        new = dict(org.settings or {})
        changes = {}
        for k, v in body.settings.items():
            if k not in allowed:
                continue
            default = DEFAULT_ORG_SETTINGS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"„{k}“ трябва да е цяло число.") from None
                if v < 0:
                    raise HTTPException(400, f"„{k}“ не може да е отрицателно.")
                if k == "max_meeting_hours" and not 1 <= v <= 12:
                    raise HTTPException(400, "Максималната продължителност е между 1 и 12 часа.")
            elif k == "glossary":
                from bix.server import glossary

                try:
                    v = glossary.clean(v if isinstance(v, (list, str)) else [])
                except ValueError as e:
                    raise HTTPException(400, str(e)) from None
            elif isinstance(default, list):
                v = [str(x).strip() for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()]
            else:
                v = str(v)
            if k == "fixed_recipients":
                v = clean_emails(v, "фиксирани адреси")
            if k == "speaker_mode" and v not in ("voice_profiles", "calendar_direction", "anonymous"):
                raise HTTPException(400, "Непознат режим.")
            if k == "face_style" and v not in FACE_STYLES:
                raise HTTPException(400, "Непознато лице.")
            if new.get(k, default) != v:
                changes[k] = v if k != "glossary" else f"{len(v)} реда"
            new[k] = v
        org.settings = new
        audit(s, p.actor, "settings.update", org_id=p.org_id, ip=client_ip(request), changes=changes)
        return {"ok": True, "settings": org.all_settings()}


def service_status(srv) -> dict[str, Any]:
    """One entry per external service: ok, provider, detail. Probes the network where it is cheap."""
    out: dict[str, Any] = {}
    try:
        from bix.server import stt as stt_mod

        out["stt"] = {"ok": True, "provider": srv.cfg.stt.provider, "detail": stt_mod.make(srv.cfg).check()}
    except Exception as e:
        out["stt"] = {"ok": False, "provider": srv.cfg.stt.provider, "detail": str(e)[:300]}
    llm = srv.cfg.llm
    try:
        if llm.provider == "anthropic":
            from bix.common.settings import secret

            ok = bool(secret(llm.api_key, llm.api_key_env))
            out["llm"] = {"ok": ok, "provider": llm.provider, "detail": llm.model if ok else f"Липсва {llm.api_key_env}"}
        elif llm.provider == "claude_cli":
            import shutil

            out["llm"] = {"ok": bool(shutil.which(llm.claude_cli_path)), "provider": llm.provider, "detail": llm.model}
        elif llm.provider == "vllm":
            from bix.server.minutes import vllm_model

            out["llm"] = {"ok": True, "provider": llm.provider, "detail": f"{vllm_model(llm, probe=True)} @ {llm.vllm_url}"}
        elif llm.provider == "azure_openai":
            out["llm"] = {"ok": True, "provider": llm.provider, "detail": _probe_azure_openai(llm)}
        else:
            out["llm"] = {"ok": llm.provider != "none", "provider": llm.provider, "detail": llm.model}
    except Exception as e:
        out["llm"] = {"ok": False, "provider": llm.provider, "detail": str(e)[:300]}
    from bix.common import models

    have = models.have(srv.cfg.models_dir, srv.cfg.diarization.embedding_model)
    out["diarization"] = {"ok": all(have.values()), "provider": srv.cfg.diarization.provider, "detail": have}
    out["email"] = {"ok": bool(srv.cfg.smtp.host) or (srv.graph.enabled and bool(srv.cfg.graph.sender_mailbox)), "provider": "graph" if srv.graph.enabled else "smtp", "detail": srv.cfg.graph.sender_mailbox or srv.cfg.smtp.host or "не е настроено"}
    try:
        detail = srv.storage.check() if hasattr(srv.storage, "check") else (srv.cfg.storage.s3_bucket or str(srv.cfg.storage_dir))
        out["storage"] = {"ok": True, "provider": srv.cfg.storage.backend, "detail": detail}
    except Exception as e:
        out["storage"] = {"ok": False, "provider": srv.cfg.storage.backend, "detail": str(e)[:300]}
    if srv.graph.enabled:
        try:
            srv.graph.token()
            out["m365"] = {"ok": True, "provider": "graph", "detail": "вход в Microsoft Entra ID ✓"}
        except Exception as e:
            out["m365"] = {"ok": False, "provider": "graph", "detail": str(e)[:300]}
    return out


def _probe_azure_openai(llm) -> str:
    """A one-token call: proves the endpoint, the deployment and the login all work."""
    import httpx

    from bix.server.minutes import MinutesError, azure_openai_request

    url, headers = azure_openai_request(llm, "chat/completions")
    body = {"messages": [{"role": "user", "content": "ok"}], "max_completion_tokens": 16}
    if llm.azure_api_version == "v1":
        body["model"] = llm.azure_deployment
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise MinutesError(f"Azure OpenAI не отговаря ({llm.azure_endpoint}): {type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise MinutesError(f"Azure OpenAI отказа достъпа ({r.status_code}).")
    if r.status_code == 404:
        raise MinutesError(f"Няма deployment „{llm.azure_deployment}“ ({llm.azure_endpoint}).")
    if r.status_code >= 500:
        raise MinutesError(f"Azure OpenAI {r.status_code}.")
    return f"{llm.azure_deployment} @ {llm.azure_endpoint}"


@router.get("/status")
def status(request: Request, p: Principal = Depends(admin_only)) -> dict:
    """Health of the moving parts, for the Settings page."""
    srv = srv_of(request)
    out = service_status(srv)
    with srv.db.session() as s:
        out["jobs"] = {
            "queued": s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")),
            "running": s.scalar(select(func.count()).select_from(Job).where(Job.status == "running")),
            "failed_24h": s.scalar(select(func.count()).select_from(Job).where(Job.status == "failed", Job.updated_at > time.time() - 86400)),
        }
    return out


@router.get("/audit")
def audit_log(request: Request, limit: int = 100, offset: int = 0, action: str = "", p: Principal = Depends(admin_or_auditor)) -> dict:
    with srv_of(request).db.session() as s:
        stmt = select(AuditLog).where(AuditLog.org_id == p.org_id)
        if action:
            stmt = stmt.where(AuditLog.action.like(f"{action}%"))
        total = s.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = s.scalars(stmt.order_by(AuditLog.id.desc()).offset(offset).limit(min(limit, 500))).all()
        return {"total": total, "items": [{"at": r.at, "actor": r.actor, "action": r.action, "target": r.target, "details": r.details, "ip": r.ip} for r in rows]}


@router.get("/jobs")
def jobs(request: Request, p: Principal = Depends(admin_only)) -> list[dict]:
    with srv_of(request).db.session() as s:
        rows = s.scalars(select(Job).order_by(Job.id.desc()).limit(100)).all()
        return [{"id": j.id, "kind": j.kind, "meeting_id": j.meeting_id, "status": j.status, "attempts": j.attempts, "error": j.error, "updated_at": j.updated_at} for j in rows]


@router.get("/events")
async def events(request: Request, p: Principal = Depends(any_user)) -> StreamingResponse:
    """Server-sent events for live portal updates."""
    import asyncio
    import queue as queue_mod

    srv = srv_of(request)
    q = srv.bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.to_thread(q.get, True, 15)
                except queue_mod.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev.type}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n"
        finally:
            srv.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
