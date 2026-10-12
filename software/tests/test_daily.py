"""A normal day in the room: the calendar on the device, starting the booked meeting,
questions during the meeting, stars, automatic sending, and the device-side countdowns."""

from __future__ import annotations

import time
import uuid

from conftest import tone
from test_flow import drain, enroll_device, setup_admin

from bix.common.audio import encode_flac
from bix.server import calendars

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//bix//EN
BEGIN:VEVENT
UID:weekly-1@test
DTSTART:20261005T070000Z
DTEND:20261005T080000Z
RRULE:FREQ=WEEKLY;BYDAY=MO
SUMMARY:Седмичен преглед
ORGANIZER;CN=Мария Петрова:mailto:maria@test.bg
ATTENDEE;CN=Иван:mailto:ivan@test.bg
ATTENDEE;CUTYPE=ROOM;CN=Зала Витоша:mailto:vitosha@test.bg
END:VEVENT
BEGIN:VEVENT
UID:allday@test
DTSTART;VALUE=DATE:20261012
SUMMARY:Празник
END:VEVENT
BEGIN:VEVENT
UID:cancelled@test
DTSTART:20261012T120000Z
DTEND:20261012T130000Z
STATUS:CANCELLED
SUMMARY:Отменена
END:VEVENT
END:VCALENDAR
""".encode()


def test_ics_recurring_events():
    import icalendar

    cal = icalendar.Calendar.from_ical(ICS)
    start = 1791763200  # 2026-10-12 00:00 UTC, a Monday
    evs = calendars.ics_events(cal, start, start + 86400)
    assert [e["title"] for e in evs] == ["Седмичен преглед"]  # the weekly one repeats; all-day and cancelled are not meetings
    ev = evs[0]
    assert ev["start"] == start + 7 * 3600 and ev["end"] == start + 8 * 3600
    assert ev["organizer"] == {"name": "Мария Петрова", "email": "maria@test.bg"}
    assert ev["attendees"] == [{"name": "Иван", "email": "ivan@test.bg"}]  # the room itself is not a person
    assert ev["uid"].startswith("ics:weekly-1@test:")


def _book(client, room, title, start, dur=3600, **kw):
    r = client.post(f"/api/rooms/{room['id']}/bookings", json={"title": title, "start": start, "end": start + dur, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_booking_reaches_device_and_starts_the_meeting(srv, client):
    setup_admin(client)
    room, token = enroll_device(client)
    h = {"Authorization": f"Bearer {token}"}
    now = time.time()
    _book(client, room, "Седмичен преглед", now - 300, organizer_name="Мария", organizer_email="maria@test.bg",
          attendees=["Иван Георгиев <ivan@test.bg>", "Петър"], repeat="weekly")
    _book(client, room, "Ретро", now + 3 * 3600, 1800)
    assert client.post(f"/api/rooms/{room['id']}/bookings", json={"title": "x", "start": now, "end": now - 1}).status_code == 400
    assert client.post(f"/api/rooms/{room['id']}/bookings", json={"title": "x", "start": now, "end": now + 60, "attendees": ["не е имейл@"]}).status_code == 400

    hb = client.post("/api/device/heartbeat", json={"state": "idle"}, headers=h).json()
    agenda = hb["agenda"]
    assert [e["title"] for e in agenda] == ["Седмичен преглед", "Ретро"]
    assert agenda[0]["organizer"] == "Мария" and agenda[0]["attendees"] == ["Иван Георгиев", "Петър"]
    assert "ivan@test.bg" not in str(agenda)  # the room screen gets names, not addresses
    assert hb["config"]["face_style"] == "pixel"

    # one tap on the room screen: the meeting is that booking, with its people
    mid = uuid.uuid4().hex
    r = client.post("/api/device/meetings", json={"id": mid, "started_at": time.time(), "calendar_uid": agenda[0]["uid"]}, headers=h)
    assert r.status_code == 200
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["title"] == "Седмичен преглед"
    assert {a["email"] for a in m["attendees"]} == {"maria@test.bg", "ivan@test.bg", ""}
    assert m["organizer_email"] == "maria@test.bg"

    # stars made on the device arrive with the end of the meeting and go to the minutes
    client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": 10}, content=encode_flac(tone(10)), headers=h)
    r = client.post(f"/api/device/meetings/{mid}/end", json={"ended_at": time.time(), "chunks": 1, "duration_s": 10,
                                                              "notes": [{"t": 4.0, "kind": "star"}, {"t": 6.0, "kind": "hack", "text": "x" * 900}]}, headers=h)
    assert r.json()["status"] == "queued"
    notes = client.get(f"/api/meetings/{mid}").json()["notes"]
    assert notes[0] == {"t": 4.0, "kind": "star", "text": ""} and notes[1]["kind"] == "note" and len(notes[1]["text"]) == 500

    # the next heartbeat says what happened to it
    drain(srv)
    last = client.post("/api/device/heartbeat", json={"state": "idle"}, headers=h).json()["last_meeting"]
    assert last["id"] == mid and last["status"] == "done"

    # portal: the week of the room, and bookings can be removed
    cal = client.get(f"/api/rooms/{room['id']}/calendar").json()
    weekly = [e for e in cal["events"] if e["title"] == "Седмичен преглед"]
    assert weekly and weekly[0]["booking_id"]
    assert client.delete(f"/api/bookings/{weekly[0]['booking_id']}").status_code == 200


def test_no_recipients_does_not_fail_the_meeting(srv, client):
    """Automatic sending is on by default; with nobody to send to the minutes are still done."""
    setup_admin(client)
    room = client.post("/api/rooms", json={"name": "Без получатели"}).json()
    code = client.post("/api/devices/enrollment-codes", json={"room_id": room["id"]}).json()["code"]
    token = client.post("/api/device/enroll", json={"code": code}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    mid = uuid.uuid4().hex
    client.post("/api/device/meetings", json={"id": mid, "started_at": time.time()}, headers=h)
    client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": 8}, content=encode_flac(tone(8)), headers=h)
    client.post(f"/api/device/meetings/{mid}/end", json={"ended_at": time.time(), "chunks": 1, "duration_s": 8}, headers=h)
    drain(srv)
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["status"] == "done"
    assert m["steps"]["deliver"]["problem"] == "no_recipients"


def test_ask_during_the_meeting(srv, client, monkeypatch):
    from bix.server import minutes

    setup_admin(client)
    room, token = enroll_device(client)
    h = {"Authorization": f"Bearer {token}"}
    mid = uuid.uuid4().hex
    client.post("/api/device/meetings", json={"id": mid, "started_at": time.time() - 30}, headers=h)
    client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": 12}, content=encode_flac(tone(12)), headers=h)
    seen = {}

    def fake_structured(cfg, system, prompt, schema=None, max_tokens=0, effort=""):
        seen["prompt"], seen["effort"] = prompt, effort
        if "Запиши" in prompt:
            return {"kind": "task", "answer": "Записах.", "points": [], "note": "Иван праща офертата", "owner": "Иван", "due": "петък"}
        return {"kind": "answer", "answer": "Решихме две неща.", "points": ["едно", "две", "", "три", "4", "5", "6"], "note": "игнорира се", "owner": None, "due": None}

    srv.cfg.llm.provider = "vllm"
    monkeypatch.setattr(minutes, "structured", fake_structured)
    r = client.post(f"/api/device/meetings/{mid}/ask", json={"preset": "decisions"}, headers=h)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["question"] == minutes.PRESETS["decisions"] and a["kind"] == "answer" and a["note"] is None
    assert a["points"] == ["едно", "две", "три", "4", "5"]  # at most five, no empty ones
    assert "изречение 0" in seen["prompt"] and seen["effort"] == "low"  # the uploaded audio was transcribed on demand
    # asking twice does not transcribe the same chunk twice
    client.post(f"/api/device/meetings/{mid}/ask", json={"question": "А сега?"}, headers=h)
    assert seen["prompt"].count("изречение 0") == 1

    r = client.post(f"/api/device/meetings/{mid}/ask", json={"question": "Запиши задача: Иван праща офертата до петък"}, headers=h).json()
    assert r["kind"] == "task" and r["owner"] == "Иван"
    notes = client.get(f"/api/meetings/{mid}").json()["notes"]
    assert notes[-1]["kind"] == "task" and notes[-1]["text"] == "Иван праща офертата"

    assert client.post(f"/api/device/meetings/{mid}/ask", json={}, headers=h).status_code == 400
    other_room, other_token = enroll_device(client, "Друга")
    assert client.post(f"/api/device/meetings/{mid}/ask", json={"preset": "recap"}, headers={"Authorization": f"Bearer {other_token}"}).status_code == 404

    # what was written down in the room goes into the minutes prompt
    from bix.server.pipeline import note_line

    assert note_line({"t": 75, "kind": "task", "text": "Иван праща офертата", "owner": "Иван", "due": "петък"}) == "[01:15] task: Иван праща офертата, owner: Иван, due: петък"


def test_device_countdown_and_suggestion(tmp_path):
    """Automatic stops count down first and "keep going" cancels them; a booking that is
    about to start ends the running meeting; talking with nothing recording only asks."""
    from bix.device.agent import COUNTDOWN_S, Agent
    from bix.device.config import DeviceConfig

    cfg = DeviceConfig(data_dir=str(tmp_path / "dev"))
    cfg.audio.input_device = "none-such-device"
    a = Agent(cfg)
    now = time.time()
    a.state, a.meeting_id, a.meeting_started = "recording", "m1", now - 3600
    a.spool.create("m1", now - 3600)
    a.last_speech = now - 11 * 60
    a._check_auto_stop()
    assert a.ending and a.ending["reason"] == "silence" and a.state == "recording"
    a.keep_going()
    assert a.ending is None
    a._check_auto_stop()
    assert a.ending is None  # "keep going" counts as speech

    # the next booking starts in 3 minutes
    a.agenda = [{"uid": "b2", "title": "Ретро", "start": now + 180, "end": now + 1980}]
    a._check_auto_stop()
    assert a.ending["reason"] == "next_meeting" and a.ending["next"] == "Ретро"
    a.ending["at"] = time.time() - 1
    a._check_auto_stop()
    assert a.state == "idle"  # stopped after the countdown
    assert COUNTDOWN_S >= 30

    # idle, people talk for a while: a question on the screen, never a recording
    a.client.base, a.client.token = "http://bix.test", "x"
    a.present = True
    t = time.time()
    for i in range(2600):  # 52 s of speech in 20 ms frames
        a._watch_for_meeting(t + i * 0.02, True)
    assert a.suggest is not None and a.state == "idle"
    a.dismiss_suggest()
    assert a.suggest is None and a._suggest_snooze > time.time()


def test_smtp_message_is_ready_for_a_bank_relay(monkeypatch):
    """Relays and mail gateways reject mail without Date/Message-ID; out-of-office replies must not loop."""
    import smtplib

    from bix.server.config import SmtpCfg
    from bix.server.integrations import DeliveryError, smtp_send

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, local_hostname=None, timeout=30):
            sent["conn"] = (host, port, local_hostname)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = u

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = SmtpCfg(host="relay.bank.local", port=25, starttls=False, sender="bix@bank.bg", reply_to="it@bank.bg", local_hostname="bix01.bank.local")
    smtp_send(cfg, ["a@bank.bg", "b@bank.bg"], "Минути: Преглед", "<p>x</p>", "x", [("minutes.md", b"# x", "text/markdown")])
    m = sent["msg"]
    assert m["From"] == "Bix <bix@bank.bg>" and m["Reply-To"] == "it@bank.bg" and m["To"] == "a@bank.bg, b@bank.bg"
    assert m["Date"] and m["Message-ID"].endswith("@bank.bg>")
    assert m["Auto-Submitted"] == "auto-generated" and m["X-Auto-Response-Suppress"] == "All"
    assert "login" not in sent and "tls" not in sent  # an internal relay: no password, no STARTTLS
    assert sent["conn"] == ("relay.bank.local", 25, "bix01.bank.local")

    class Refuse(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPSenderRefused(550, b"5.7.1 Client host rejected: relay not permitted", "bix@bank.bg")

    monkeypatch.setattr(smtplib, "SMTP", Refuse)
    try:
        smtp_send(cfg, ["a@bank.bg"], "s", "<p>x</p>", "x")
    except DeliveryError as e:
        assert "relay" in str(e) and "bix@bank.bg" in str(e)
    else:
        raise AssertionError("expected DeliveryError")


def test_browser_device_room_page_and_wav_chunks(srv, client):
    """Any laptop: /room serves the room screen with the in-browser agent; browsers upload WAV."""
    import io

    import soundfile as sf

    r = client.get("/room")
    assert r.status_code == 200 and "/room/webagent.js" in r.text and "/room/panel.js" in r.text
    assert "microphone=(self)" in r.headers["permissions-policy"]
    assert "script-src 'self'" in r.headers["content-security-policy"]
    for name in ("face.js", "panel.js", "webagent.js", "worklet.js"):
        assert client.get(f"/room/{name}").status_code == 200
    assert client.get("/room/../server.toml").status_code == 404
    assert client.get("/room/app.py").status_code == 404
    assert client.get("/room/manifest.json").json()["start_url"] == "/room"

    setup_admin(client)
    room, token = enroll_device(client)
    h = {"Authorization": f"Bearer {token}"}
    mid = uuid.uuid4().hex
    client.post("/api/device/meetings", json={"id": mid, "started_at": time.time()}, headers=h)
    buf = io.BytesIO()
    sf.write(buf, tone(9), 16000, format="WAV", subtype="PCM_16")
    r = client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": 9}, content=buf.getvalue(), headers={**h, "Content-Type": "audio/wav"})
    assert r.status_code == 200, r.text
    assert client.put(f"/api/device/meetings/{mid}/chunks/1", params={"start_s": 9, "duration_s": 1}, content=b"not audio at all", headers=h).status_code == 415
    assert client.put(f"/api/device/meetings/{mid}/chunks/1", params={"start_s": 9, "duration_s": 1}, content=b"RIFF\x00\x00\x00\x00WAVEjunk", headers=h).status_code == 415
    client.post(f"/api/device/meetings/{mid}/end", json={"ended_at": time.time(), "chunks": 1, "duration_s": 9}, headers=h)
    drain(srv)
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["status"] == "done" and round(m["duration_s"]) == 9  # the WAV was stored as FLAC and processed


def test_glossary_parse_and_fix():
    from bix.server import glossary
    from bix.server.stt.base import Segment

    g = glossary.parse(["Пощенска банка", "ДСК: ди ес ке, DSK", "Мария Петрова = мария петрова", "", "# бележка", "дск: дубликат"])
    assert [e.term for e in g] == ["Пощенска банка", "ДСК", "Мария Петрова"]
    assert glossary.fix("Говорихме с Ди Ес Ке и с dsk за DSKто.", g) == "Говорихме с ДСК и с ДСК за DSKто."  # whole words only
    segs = [Segment(0, 1, "мария петрова каза"), Segment(1, 2, "нищо")]
    assert glossary.fix_segments(segs, g) == 1 and segs[0].text == "Мария Петрова каза"
    block = glossary.prompt_block(g)
    assert "ДСК (may appear as: ди ес ке, DSK)" in block and "Пощенска банка" in block
    assert glossary.prompt_block([]) == ""
    import pytest

    with pytest.raises(ValueError):
        glossary.clean(["x" * 400])


def test_glossary_reaches_settings_transcript_and_minutes(srv, client, monkeypatch):
    from test_flow import record

    from bix.server import minutes, pipeline

    setup_admin(client)
    r = client.patch("/api/settings", json={"settings": {"glossary": "ДСК: изречение\nПощенска банка, клон Витоша"}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["glossary"] == ["ДСК: изречение", "Пощенска банка, клон Витоша"]  # commas stay inside a line
    assert client.patch("/api/settings", json={"settings": {"glossary": ["x" * 400]}}).status_code == 400
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    seg = client.get(f"/api/meetings/{mid}/transcript").json()["segments"][0]
    assert seg["text"].startswith("ДСК")  # "изречение" (the fake STT's word) was corrected by the glossary
    seen = {}
    srv.cfg.llm.provider = "vllm"
    monkeypatch.setattr(minutes, "structured", lambda cfg, system, prompt, *a, **k: seen.update(prompt=prompt) or minutes.empty_minutes("x"))
    pipeline.process(srv, mid, "minutes")
    assert "Glossary of the organisation" in seen["prompt"] and "Пощенска банка, клон Витоша" in seen["prompt"]


def test_one_person_one_label():
    """Far microphone: each person is split into several similar clusters; merge them back."""
    import numpy as np

    from bix.server.speakers import Turn, merge_similar

    rng = np.random.default_rng(1)
    people = [rng.normal(size=192) for _ in range(3)]
    embs, turns, t = {}, [], 0.0
    n = 0
    for p, base in enumerate(people):
        for k in range(4):  # 4 clusters per person, like the 25-for-4 meeting
            n += 1
            lab = f"S{n}"
            v = base + rng.normal(scale=0.35, size=192)
            embs[lab] = (v / np.linalg.norm(v)).astype(np.float32)
            secs = 120 if k == 0 else 30
            turns.append(Turn(t, t + secs, lab))
            t += secs
    tiny = rng.normal(size=192)
    embs["S99"] = (tiny / np.linalg.norm(tiny)).astype(np.float32)
    turns.append(Turn(t, t + 5, "S99"))  # 5 s of something: not a person
    out = merge_similar(turns, embs, 0.75, 0.025, 6.0)
    assert len({x.label for x in out}) == 3
    # different people are never merged by similarity alone...
    distinct = {f"S{i}": (v / np.linalg.norm(v)).astype(np.float32) for i, v in enumerate(people)}
    dturns = [Turn(i * 100, i * 100 + 100, f"S{i}") for i in range(3)]
    assert len({x.label for x in merge_similar(dturns, distinct, 0.75, 0.025, 6.0)}) == 3
    # ...but the calendar caps the count (invited + 1)
    assert len({x.label for x in merge_similar(dturns, distinct, 0.75, 0.025, 6.0, max_speakers=2)}) == 2
