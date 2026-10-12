"""Meeting processing: audio → transcript → speakers → minutes → delivery.

Each step stores its output, so a step can be re-run alone (for example new
minutes after renaming speakers) without paying for speech recognition again.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import delete, select

from bix.common import audio as au
from bix.common.audio import fmt_ts
from bix.server import minutes as minutes_mod
from bix.server import speakers as spk
from bix.server import stt as stt_mod
from bix.server.core import Server, key, prefix
from bix.server.db import Chunk, Job, Meeting, Org, Person, Room, audit
from bix.server.integrations import DeliveryError, smtp_send

log = logging.getLogger(__name__)

STEPS = ["assemble", "calendar", "stt", "speakers", "minutes", "deliver"]


class StepError(RuntimeError):
    pass


def enqueue(s, kind: str, meeting_id: str | None = None, **args: Any) -> None:
    """Add a job unless the same one is already waiting."""
    existing = s.scalars(select(Job).where(Job.kind == kind, Job.meeting_id == meeting_id, Job.status == "queued")).first()
    if existing:
        existing.args = {**existing.args, **args}
        return
    s.add(Job(kind=kind, meeting_id=meeting_id, args=args))


def process(srv: Server, meeting_id: str, from_step: str = "assemble") -> None:
    start = STEPS.index(from_step) if from_step in STEPS else 0
    with srv.db.session() as s:
        m = s.get(Meeting, meeting_id)
        if m is None:
            return
        m.status = "processing"
        m.error = ""
    srv.bus.publish("meeting", id=meeting_id, status="processing")
    for step in STEPS[start:]:
        _set_step(srv, meeting_id, step, state="running")
        t0 = time.time()
        try:
            info = STEP_FUNCS[step](srv, meeting_id) or {}
        except Exception as e:
            log.exception("step %s failed for %s", step, meeting_id)
            _set_step(srv, meeting_id, step, state="failed", error=str(e)[:500], took_s=round(time.time() - t0, 1))
            with srv.db.session() as s:
                m = s.get(Meeting, meeting_id)
                m.status = "failed"
                m.error = f"{step}: {e}"[:1000]
            srv.bus.publish("meeting", id=meeting_id, status="failed")
            e.bix_step = step  # type: ignore[attr-defined]  # a retry resumes here
            raise
        _set_step(srv, meeting_id, step, state="done", took_s=round(time.time() - t0, 1), **info)
    with srv.db.session() as s:
        m = s.get(Meeting, meeting_id)
        m.status = "done"
    srv.bus.publish("meeting", id=meeting_id, status="done")


def _set_step(srv: Server, mid: str, step: str, **info: Any) -> None:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        steps = dict(m.steps or {})
        steps[step] = {**steps.get(step, {}), **info, "at": time.time()}
        if info.get("state") == "running":
            steps[step].pop("error", None)
        m.steps = steps
    srv.bus.publish("meeting", id=mid, step=step, state=info.get("state"))


# --- steps -----------------------------------------------------------------


def step_assemble(srv: Server, mid: str) -> dict[str, Any]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        chunks = s.scalars(select(Chunk).where(Chunk.meeting_id == mid).order_by(Chunk.seq)).all()
        org_id = m.org_id
        has_audio = m.has_audio
    audio_key = key(org_id, mid, "audio.flac")
    if not chunks:
        if has_audio and srv.storage.exists(audio_key):
            return {"note": "already assembled"}
        raise StepError("Няма записан звук.")
    pieces, missing, expected = [], [], 0
    for c in chunks:
        if c.seq != expected:
            missing += list(range(expected, c.seq))
        expected = c.seq + 1
        pieces.append(au.decode(srv.storage.get(c.key)))
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    srv.storage.put(audio_key, au.encode_flac(audio), "audio/flac")
    srv.storage.put(key(org_id, mid, "audio.ogg"), au.encode_opus(audio), "audio/ogg")
    for c in chunks:
        srv.storage.delete(c.key)
    with srv.db.session() as s:
        s.execute(delete(Chunk).where(Chunk.meeting_id == mid))
        m = s.get(Meeting, mid)
        m.has_audio = True
        m.duration_s = len(audio) / au.SR
    return {"seconds": round(len(audio) / au.SR, 1), "chunks": len(chunks), "missing_chunks": missing}


def step_calendar(srv: Server, mid: str) -> dict[str, Any]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        room = s.get(Room, m.room_id) if m.room_id else None
        if not room:
            return {"skipped": "няма зала"}
        start, end = m.started_at, m.ended_at or (m.started_at + m.duration_s)
        ev = None
        if m.calendar_uid:  # picked on the device when the meeting started
            ev = srv.calendars.find(s, room, m.calendar_uid, start)
        if ev is None:
            ev = srv.calendars.at(s, room, start, end)
        problem = srv.calendars.errors.get(room.id, "")
        if not ev:
            return {"found": False, **({"problem": problem} if problem else {})}
        apply_event(m, ev)
    return {"found": True, "subject": ev["title"], "attendees": len(ev["attendees"]), "source": ev["source"]}


def apply_event(m: Meeting, ev: dict[str, Any]) -> None:
    """Title, organizer and attendees of a meeting from its calendar event."""
    if ev["title"] and m.title_auto:
        m.title = ev["title"][:300]
        m.title_auto = False  # a calendar subject is a real title
    m.calendar_uid = ev["uid"][:300]
    m.scheduled_end = ev["end"]
    m.organizer_email = ev["organizer"].get("email", "")
    seen, att = set(), []
    for p in [ev["organizer"]] + ev["attendees"]:
        k = (p.get("email") or p.get("name") or "").lower()
        if k and k not in seen:
            seen.add(k)
            att.append({"name": p.get("name", ""), "email": p.get("email", "")})
    m.attendees = att


def step_stt(srv: Server, mid: str) -> dict[str, Any]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        room = s.get(Room, m.room_id) if m.room_id else None
        language = org.setting("language")
        hints = [a.get("name", "") for a in m.attendees or []]
        hints += [p.name for p in s.scalars(select(Person).where(Person.org_id == m.org_id)).all()]
        if room:
            hints.append(room.name)
        org_id = m.org_id
    audio = au.decode(srv.storage.get(key(org_id, mid, "audio.flac")))
    transcriber = stt_mod.make(srv.cfg)
    t0 = time.time()
    hints = [h for h in hints if h]
    fallback_note = ""
    try:
        segments = transcriber.transcribe(audio, language, hints=hints)
    except Exception as e:
        if not srv.cfg.stt.fallback_provider:
            raise
        log.warning("STT %s failed (%s); using fallback %s", transcriber.name, e, srv.cfg.stt.fallback_provider)
        fallback_note = f"{transcriber.name}: {e}"[:200]
        transcriber = stt_mod.make(srv.cfg, srv.cfg.stt.fallback_provider)
        segments = transcriber.transcribe(audio, language, hints=hints)
    srv.storage.put(
        key(org_id, mid, "stt.json"),
        json.dumps({"provider": transcriber.name, "diarized": transcriber.diarizes, "segments": [x.to_dict() for x in segments]}, ensure_ascii=False).encode(),
        "application/json",
    )
    info = {"provider": transcriber.name, "segments": len(segments), "rtf": round((time.time() - t0) / max(1.0, len(audio) / au.SR), 3)}
    if fallback_note:
        info["fallback_from"] = fallback_note
    return info


def step_speakers(srv: Server, mid: str) -> dict[str, Any]:
    cfg = srv.cfg.diarization
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        mode = org.setting("speaker_mode")
        org_id = m.org_id
        people = [(p.id, np.array(p.embedding, dtype=np.float32), p.name) for p in s.scalars(select(Person).where(Person.org_id == org_id)).all() if p.embedding and p.consent_at]
        previous = {x["label"]: x for x in (m.speakers or []) if x.get("source") == "user"}
    raw = json.loads(srv.storage.get(key(org_id, mid, "stt.json")))
    segments = [_seg(d) for d in raw["segments"]]
    audio = au.decode(srv.storage.get(key(org_id, mid, "audio.flac")))

    if cfg.provider == "none":
        turns: list[spk.Turn] = []
        for x in segments:
            x.speaker = "S1"
    elif cfg.provider == "provider" and raw.get("diarized"):
        turns = [spk.Turn(x.start, x.end, x.speaker or "A0") for x in segments]
    else:
        turns = spk.diarize(audio, srv.cfg.models_dir, cfg)
    embs: dict[str, np.ndarray] = {}
    if turns:
        embs = spk.speaker_embeddings(audio, turns, srv.cfg.models_dir, cfg)
        turns = spk.merge_small(turns, embs, cfg.min_speaker_seconds)
        turns = spk.relabel_by_first_appearance(turns)
        embs = spk.speaker_embeddings(audio, turns, srv.cfg.models_dir, cfg)
        segments = spk.assign(segments, turns)

    totals: dict[str, float] = {}
    for x in segments:
        totals[x.speaker or "S1"] = totals.get(x.speaker or "S1", 0.0) + (x.end - x.start)
    matches = spk.match_profiles(embs, [(p[0], p[1]) for p in people], cfg.profile_match) if mode == "voice_profiles" else {}
    names = {p[0]: p[2] for p in people}
    speakers = []
    for label in sorted(totals, key=lambda l: int(l[1:]) if l[1:].isdigit() else 999):
        entry = {"label": label, "name": "", "source": "", "person_id": "", "seconds": round(totals[label], 1), "score": None}
        if label in matches:
            pid, score = matches[label]
            entry.update(name=names[pid], source="profile", person_id=pid, score=score)
        if label in previous and previous[label].get("name"):
            entry.update(name=previous[label]["name"], source="user", person_id=previous[label].get("person_id", ""))
        if mode == "anonymous":
            entry.update(name="", source="", person_id="")
        speakers.append(entry)

    transcript = {"segments": [x.to_dict() for x in segments]}
    srv.storage.put(key(org_id, mid, "transcript.json"), json.dumps(transcript, ensure_ascii=False).encode(), "application/json")
    srv.storage.put(
        key(org_id, mid, "embeddings.json"),
        json.dumps({k: [round(float(v), 6) for v in e] for k, e in embs.items()}).encode(),
        "application/json",
    )
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        m.speakers = speakers
        m.transcript_text = "\n".join(x.text for x in segments)
    return {"speakers": len(speakers), "recognized": len(matches)}


def step_minutes(srv: Server, mid: str) -> dict[str, Any]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        room = s.get(Room, m.room_id) if m.room_id else None
        language = org.setting("language")
        mode = org.setting("speaker_mode")
        org_id = m.org_id
        names = {x["label"]: x["name"] for x in m.speakers or [] if x.get("name")}
        context = {
            "room": room.name if room else "",
            "started": datetime.fromtimestamp(m.started_at).strftime("%d.%m.%Y %H:%M"),
            "calendar_subject": "" if m.title_auto else m.title,
            "attendees": [a.get("name") or a.get("email") for a in m.attendees or []],
            "notes": [note_line(n) for n in m.notes or []],
        }
    if srv.cfg.llm.provider == "none":
        return {"skipped": "без езиков модел"}
    transcript = json.loads(srv.storage.get(key(org_id, mid, "transcript.json")))
    provider = srv.cfg.llm.provider
    try:
        result = minutes_mod.generate(srv.cfg.llm, transcript["segments"], names, context, language)
    except Exception as e:
        if not srv.cfg.llm.fallback_provider:
            raise
        import dataclasses

        log.warning("LLM %s failed (%s); using fallback %s", provider, e, srv.cfg.llm.fallback_provider)
        provider = srv.cfg.llm.fallback_provider
        result = minutes_mod.generate(dataclasses.replace(srv.cfg.llm, provider=provider), transcript["segments"], names, context, language)
    inferred = 0
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        speakers = [dict(x) for x in m.speakers or []]
        if mode != "anonymous":
            for sn in result.get("speaker_names", []):
                for x in speakers:
                    if x["label"] == sn["label"] and not x.get("name") and sn.get("name"):
                        x.update(name=sn["name"], source="llm", evidence=sn.get("evidence", ""))
                        inferred += 1
        m.speakers = speakers
        if result.get("title") and m.title_auto:
            m.title = result["title"]
        m.minutes = result
    _render(srv, mid)
    return {"provider": provider, "decisions": len(result["decisions"]), "actions": len(result["action_items"]), "names_inferred": inferred}


def step_deliver(srv: Server, mid: str) -> dict[str, Any]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        auto = org.setting("auto_send")
        to = recipients(s, m, org)
    if not auto:
        return {"skipped": "ръчно изпращане"}
    # Nobody to send to, or no mail set up: the minutes are still done; the room screen
    # and the portal say why nothing went out, instead of the meeting failing.
    if not to:
        return {"skipped": "няма получатели", "problem": "no_recipients"}
    if not mail_configured(srv):
        return {"skipped": "имейлът не е настроен", "problem": "no_mail"}
    return send_minutes(srv, mid, actor="system")


def mail_configured(srv: Server) -> bool:
    return bool(srv.cfg.smtp.host or (srv.graph.enabled and srv.cfg.graph.sender_mailbox))


STEP_FUNCS = {
    "assemble": step_assemble,
    "calendar": step_calendar,
    "stt": step_stt,
    "speakers": step_speakers,
    "minutes": step_minutes,
    "deliver": step_deliver,
}


def _seg(d: dict[str, Any]) -> stt_mod.Segment:
    words = [stt_mod.Word(**w) for w in d.get("words", [])]
    return stt_mod.Segment(d["start"], d["end"], d["text"], speaker=d.get("speaker"), confidence=d.get("confidence"), words=words)


# --- rendering & delivery ---------------------------------------------------


def meeting_meta(srv: Server, m: Meeting, room: Room | None) -> dict[str, Any]:
    participants = [x["name"] for x in m.speakers or [] if x.get("name")]
    return {
        "title": m.title,
        "date": datetime.fromtimestamp(m.started_at).strftime("%d.%m.%Y %H:%M"),
        "duration": au.fmt_ts(m.duration_s),
        "room": room.name if room else "",
        "participants": participants,
        "url": f"{srv.cfg.http.public_url.rstrip('/')}/#/meetings/{m.id}",
    }


def _render(srv: Server, mid: str) -> tuple[str, str]:
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        room = s.get(Room, m.room_id) if m.room_id else None
        meta = meeting_meta(srv, m, room)
        language = org.setting("language")
        minutes = m.minutes or minutes_mod.empty_minutes("")
        org_id = m.org_id
    md = minutes_mod.render_markdown(minutes, meta, language)
    html = minutes_mod.render_html(minutes, meta, language)
    srv.storage.put(key(org_id, mid, "minutes.md"), md.encode(), "text/markdown")
    srv.storage.put(key(org_id, mid, "minutes.html"), html.encode(), "text/html")
    return md, html


def recipients(s, m: Meeting, org: Org) -> list[str]:
    room = s.get(Room, m.room_id) if m.room_id else None
    out: list[str] = []
    rules = org.setting("send_to") or []
    if "organizer" in rules and m.organizer_email:
        out.append(m.organizer_email)
    if "attendees" in rules:
        out += [a["email"] for a in m.attendees or [] if a.get("email")]
        pids = [x.get("person_id") for x in m.speakers or [] if x.get("person_id")]
        if pids:
            out += [p.email for p in s.scalars(select(Person).where(Person.id.in_(pids))).all() if p.email]
    if "room" in rules and room:
        out += room.recipients or []
    if "fixed" in rules:
        out += org.setting("fixed_recipients") or []
    if m.source == "upload" and m.created_by:
        from bix.server.db import User

        u = s.get(User, m.created_by)
        if u:
            out.append(u.email)
    seen, uniq = set(), []
    for a in out:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


def send_minutes(srv: Server, mid: str, actor: str, to: list[str] | None = None) -> dict[str, Any]:
    md, html = _render(srv, mid)
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        org = s.get(Org, m.org_id)
        to = to or recipients(s, m, org)
        subject = f"Минути: {m.title}" if org.setting("language") == "bg" else f"Minutes: {m.title}"
        org_id = m.org_id
    if not to:
        raise DeliveryError("Няма получатели. Задайте получатели на залата или в настройките.")
    attachments = [("minutes.md", md.encode(), "text/markdown")]
    if srv.graph.enabled and srv.cfg.graph.sender_mailbox:
        srv.graph.send_mail(to, subject, html, attachments)
        via = "graph"
    else:
        smtp_send(srv.cfg.smtp, to, subject, html, md, attachments)
        via = "smtp"
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        m.sent_to = sorted(set((m.sent_to or []) + to))
        m.sent_at = time.time()
        audit(s, actor, "minutes.send", target=mid, org_id=org_id, to=to, via=via)
    return {"sent_to": to, "via": via}


# --- live transcription during the meeting ---------------------------------


def live_chunk(srv: Server, meeting_id: str, seq: int) -> None:
    """Transcribe one uploaded chunk while the meeting still runs (live text, questions)."""
    from sqlalchemy import update

    with srv.db.session() as s:
        # claim it, so the live worker and a question don't transcribe the same chunk twice
        claimed = s.execute(update(Chunk).where(Chunk.meeting_id == meeting_id, Chunk.seq == seq, Chunk.live_done.is_(False)).values(live_done=True)).rowcount
        c = s.get(Chunk, (meeting_id, seq))
        m = s.get(Meeting, meeting_id)
        if not claimed or c is None or m is None:
            return
        language = s.get(Org, m.org_id).setting("language")
        chunk_key, offset = c.key, c.start_s
    try:
        audio = au.decode(srv.storage.get(chunk_key))
        segs = stt_mod.make(srv.cfg).transcribe(audio, language)
    except Exception:
        with srv.db.session() as s:
            s.execute(update(Chunk).where(Chunk.meeting_id == meeting_id, Chunk.seq == seq).values(live_done=False))
        raise
    with srv.db.session() as s:
        from bix.server.db import LiveSegment

        for x in segs:
            s.add(LiveSegment(meeting_id=meeting_id, start_s=offset + x.start, end_s=offset + x.end, text=x.text))
    for x in segs:
        srv.bus.publish("live", meeting_id=meeting_id, start=offset + x.start, text=x.text)


def ask(srv: Server, meeting_id: str, question: str) -> dict[str, Any]:
    """Answer a question about the meeting so far ("Ask Bix"), from the chunks uploaded until now."""
    from bix.server.db import LiveSegment

    with srv.db.session() as s:
        m = s.get(Meeting, meeting_id)
        language = s.get(Org, m.org_id).setting("language")
        started = m.started_at
        todo = [c.seq for c in s.scalars(select(Chunk).where(Chunk.meeting_id == meeting_id, Chunk.live_done.is_(False)).order_by(Chunk.seq))]
    for seq in todo:
        live_chunk(srv, meeting_id, seq)
    with srv.db.session() as s:
        lines = [{"start": x.start_s, "text": x.text} for x in s.scalars(select(LiveSegment).where(LiveSegment.meeting_id == meeting_id).order_by(LiveSegment.start_s))]
    try:
        result = minutes_mod.answer(srv.cfg.llm, question, lines, time.time() - started, language)
    except Exception as e:
        if not srv.cfg.llm.fallback_provider or srv.cfg.llm.provider == "none":
            raise
        import dataclasses

        log.warning("LLM %s failed for a question (%s); using fallback %s", srv.cfg.llm.provider, e, srv.cfg.llm.fallback_provider)
        result = minutes_mod.answer(dataclasses.replace(srv.cfg.llm, provider=srv.cfg.llm.fallback_provider), question, lines, time.time() - started, language)
    if result["kind"] != "answer" and result["note"]:
        add_note(srv, meeting_id, {"t": round(time.time() - started, 1), "kind": result["kind"], "text": result["note"], "owner": result["owner"], "due": result["due"], "asked": question})
    return result


def add_note(srv: Server, meeting_id: str, note: dict[str, Any]) -> None:
    with srv.db.session() as s:
        m = s.get(Meeting, meeting_id)
        m.notes = [*(m.notes or []), note][-200:]


def note_line(n: dict[str, Any]) -> str:
    kind = {"star": "marked as important", "task": "task", "decision": "decision", "note": "note"}.get(n.get("kind"), "note")
    extra = "".join(f", {k}: {n[k]}" for k in ("owner", "due") if n.get(k))
    return f"[{fmt_ts(float(n.get('t') or 0))}] {kind}{': ' + n['text'] if n.get('text') else ''}{extra}"


# --- retention ---------------------------------------------------------------


def retention(srv: Server) -> list[str]:
    now = time.time()
    done = []
    with srv.db.session() as s:
        for org in s.scalars(select(Org)).all():
            a_days = int(org.setting("retention_audio_days") or 0)
            t_days = int(org.setting("retention_text_days") or 0)
            for m in s.scalars(select(Meeting).where(Meeting.org_id == org.id, Meeting.status.in_(["done", "failed"]))).all():
                age = now - (m.ended_at or m.started_at)
                if t_days and age > t_days * 86400:
                    srv.storage.delete_prefix(prefix(org.id, m.id))
                    s.delete(m)
                    audit(s, "system", "retention.delete_meeting", target=m.id, org_id=org.id, days=t_days)
                    done.append(m.id)
                elif a_days and age > a_days * 86400 and m.has_audio:
                    for name in ("audio.flac", "audio.ogg", "embeddings.json"):
                        srv.storage.delete(key(org.id, m.id, name))
                    m.has_audio = False
                    m.audio_deleted_at = now
                    audit(s, "system", "retention.delete_audio", target=m.id, org_id=org.id, days=a_days)
                    done.append(m.id + ":audio")
    return done


def watchdog(srv: Server) -> None:
    """Meetings whose device vanished mid-recording get processed with what arrived."""
    now = time.time()
    with srv.db.session() as s:
        for m in s.scalars(select(Meeting).where(Meeting.status.in_(["recording", "uploading"]))).all():
            org = s.get(Org, m.org_id)
            limit = float(org.setting("max_meeting_hours") or 4) * 3600 + 3600
            last = s.scalars(select(Chunk.received_at).where(Chunk.meeting_id == m.id).order_by(Chunk.received_at.desc())).first()
            idle = now - (last or m.started_at)
            if now - m.started_at > limit or (m.status == "uploading" and idle > 6 * 3600):
                m.status = "queued"
                m.ended_at = m.ended_at or (last or now)
                m.error = "Устройството не довърши качването; обработено е полученото."
                enqueue(s, "process", m.id)
