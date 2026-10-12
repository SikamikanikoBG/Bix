"""End-to-end through the HTTP API: setup → enrollment → recording upload →
worker → portal → permissions → retention → erasure."""

from __future__ import annotations

import time
import uuid

from conftest import tone

from bix.common.audio import encode_flac
from bix.server.db import AuditLog, Meeting
from bix.server.worker import Worker


def setup_admin(client):
    r = client.post("/auth/setup", json={"org_name": "Тест ООД", "email": "admin@test.bg", "name": "Админ", "password": "correct horse battery"})
    assert r.status_code == 200, r.text


def enroll_device(client, room_name="Витоша"):
    room = client.post("/api/rooms", json={"name": room_name, "recipients": ["team@test.bg"]}).json()
    code = client.post("/api/devices/enrollment-codes", json={"room_id": room["id"]}).json()["code"]
    r = client.post("/api/device/enroll", json={"code": code, "serial": "SN1", "name": "Bix 1"})
    assert r.status_code == 200, r.text
    # the code works only once
    assert client.post("/api/device/enroll", json={"code": code}).status_code == 403
    return room, r.json()["token"]


def record(client, token, seconds=(20, 15)) -> str:
    h = {"Authorization": f"Bearer {token}"}
    mid = uuid.uuid4().hex
    assert client.post("/api/device/meetings", json={"id": mid, "started_at": time.time() - 60}, headers=h).status_code == 200
    start = 0.0
    for seq, sec in enumerate(seconds):
        r = client.put(f"/api/device/meetings/{mid}/chunks/{seq}", params={"start_s": start, "duration_s": sec}, content=encode_flac(tone(sec, 200 + 50 * seq)), headers=h)
        assert r.status_code == 200, r.text
        start += sec
    # duplicates are idempotent (device retries after a timeout)
    assert client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": seconds[0]}, content=encode_flac(tone(seconds[0])), headers=h).json()["duplicate"]
    r = client.post(f"/api/device/meetings/{mid}/end", json={"ended_at": time.time(), "chunks": len(seconds), "duration_s": sum(seconds)}, headers=h)
    assert r.json()["status"] == "queued"
    return mid


def drain(srv):
    w = Worker(srv)
    while w.run_once():
        pass


def test_full_flow(srv, client):
    assert client.get("/auth/config").json()["setup"] is True
    setup_admin(client)
    room, token = enroll_device(client)

    hb = client.post("/api/device/heartbeat", json={"state": "idle", "telemetry": {"mic": "test"}}, headers={"Authorization": f"Bearer {token}"}).json()
    assert hb["config"]["room"] == "Витоша"
    assert hb["config"]["language"] == "bg"

    mid = record(client, token)
    drain(srv)

    m = client.get(f"/api/meetings/{mid}").json()
    assert m["status"] == "done", (m["steps"], m["error"], client.get("/api/jobs").json())
    assert m["has_audio"] is True
    assert abs(m["duration_s"] - 35) < 0.1
    assert [s["label"] for s in m["speakers"]] == ["S1"]
    tr = client.get(f"/api/meetings/{mid}/transcript").json()
    assert len(tr["segments"]) >= 8
    assert client.get(f"/api/meetings/{mid}/audio").status_code == 200

    # name a speaker, then regenerate minutes (LLM disabled in tests → skipped)
    r = client.post(f"/api/meetings/{mid}/speakers", json={"label": "S1", "name": "Мария"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/meetings/{mid}/reprocess", json={"step": "minutes"}).status_code == 200
    drain(srv)
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["speakers"][0]["name"] == "Мария"
    assert m["status"] == "done"

    # search finds transcript text
    assert client.get("/api/meetings", params={"q": "изречение"}).json()["total"] == 1

    # audit trail exists for the important actions
    actions = {a["action"] for a in client.get("/api/audit").json()["items"]}
    assert {"setup.complete", "device.enrolled", "meeting.started", "meeting.ended", "meeting.view", "meeting.speaker_named"} <= actions


def test_roles_and_csrf(srv, client):
    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    client.post("/api/users", json={"email": "member@test.bg", "role": "member", "password": "member password 1"})
    client.post("/api/users", json={"email": "audit@test.bg", "role": "auditor", "password": "auditor password 1"})

    # CSRF: no X-Bix header → rejected even with a valid session
    r = client.post("/api/rooms", json={"name": "x"}, headers={"X-Bix": ""})
    assert r.status_code == 403

    client.post("/auth/logout")
    assert client.post("/auth/login", json={"email": "member@test.bg", "password": "member password 1"}).status_code == 200
    assert client.get("/api/meetings").json()["total"] == 0  # did not attend
    assert client.get(f"/api/meetings/{mid}").status_code == 404
    assert client.get("/api/users").status_code == 403
    assert client.get("/api/devices").status_code == 403  # IPs and telemetry are for admins/auditors
    o = client.get("/api/overview").json()
    assert o["meetings_30d"] == 0 and o["devices_total"] == 0  # numbers match what the member may see

    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "audit@test.bg", "password": "auditor password 1"})
    items = client.get("/api/meetings").json()["items"]
    assert items and items[0]["title"] == "—"  # metadata only
    assert client.get(f"/api/meetings/{mid}/transcript").status_code == 403
    assert client.get("/api/audit").status_code == 200

    # members who attended (calendar e-mail) can read it
    with srv.db.session() as s:
        s.get(Meeting, mid).attendees = [{"name": "M", "email": "member@test.bg"}]
    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "member@test.bg", "password": "member password 1"})
    assert client.get(f"/api/meetings/{mid}").status_code == 200


def test_device_security(srv, client):
    setup_admin(client)
    _, token = enroll_device(client)
    assert client.post("/api/device/heartbeat", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    devs = client.get("/api/devices").json()
    client.post(f"/api/devices/{devs[0]['id']}/revoke")
    assert client.post("/api/device/heartbeat", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_commands_reach_device(srv, client):
    setup_admin(client)
    _, token = enroll_device(client)
    dev = client.get("/api/devices").json()[0]
    client.post(f"/api/devices/{dev['id']}/command", json={"type": "start"})
    h = {"Authorization": f"Bearer {token}"}
    cmds = client.post("/api/device/heartbeat", json={}, headers=h).json()["commands"]
    assert [c["type"] for c in cmds] == ["start"]
    cmds = client.post("/api/device/heartbeat", json={"acked_commands": [cmds[0]["id"]]}, headers=h).json()["commands"]
    assert cmds == []


def test_retention_and_erasure(srv, client):
    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    person = client.post("/api/people", json={"name": "Иван", "email": "ivan@test.bg"}).json()
    client.post(f"/api/meetings/{mid}/speakers", json={"label": "S1", "person_id": person["id"]})

    # GDPR erasure removes the name from the meeting
    r = client.delete(f"/api/people/{person['id']}").json()
    assert r["meetings_touched"] == 1
    assert client.get(f"/api/meetings/{mid}").json()["speakers"][0]["name"] == ""

    # retention: audio after 30 days, everything after 365
    from bix.server import pipeline

    with srv.db.session() as s:
        s.get(Meeting, mid).ended_at = time.time() - 40 * 86400
    pipeline.retention(srv)
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["has_audio"] is False and m["audio_deleted_at"]
    from bix.server.core import key as skey

    org_id = client.get("/auth/me").json()["org_id"]
    assert srv.storage.local_path(skey(org_id, mid, "audio.flac")) is None  # really gone from disk
    assert srv.storage.local_path(skey(org_id, mid, "audio.ogg")) is None
    assert client.get(f"/api/meetings/{mid}/transcript").json()["segments"]  # text kept
    with srv.db.session() as s:
        s.get(Meeting, mid).ended_at = time.time() - 400 * 86400
    pipeline.retention(srv)
    assert client.get(f"/api/meetings/{mid}").status_code == 404
    assert not (srv.cfg.storage_dir / "orgs" / org_id / "meetings" / mid).exists()
    with srv.db.session() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "retention.delete_meeting").count() == 1


def test_discard_on_device(srv, client):
    setup_admin(client)
    _, token = enroll_device(client)
    h = {"Authorization": f"Bearer {token}"}
    mid = uuid.uuid4().hex
    client.post("/api/device/meetings", json={"id": mid, "started_at": time.time()}, headers=h)
    client.put(f"/api/device/meetings/{mid}/chunks/0", params={"start_s": 0, "duration_s": 5}, content=encode_flac(tone(5)), headers=h)
    assert client.post(f"/api/device/meetings/{mid}/discard", headers=h).status_code == 200
    assert client.get(f"/api/meetings/{mid}").status_code == 404


def test_upload_recording(srv, client, tmp_path):
    import soundfile as sf

    setup_admin(client)
    p = tmp_path / "phone.wav"
    sf.write(p, tone(12), 16000)
    with p.open("rb") as f:
        r = client.post("/api/meetings/upload", files={"file": ("phone.wav", f, "audio/wav")}, data={"title": "Обаждане"})
    assert r.status_code == 200, r.text
    drain(srv)
    m = client.get(f"/api/meetings/{r.json()['id']}").json()
    assert m["status"] == "done" and m["title"] == "Обаждане"


def test_export_with_cyrillic_title(srv, client):
    """Regression: Content-Disposition with a Cyrillic file name crashed (500)."""
    from bix.server import minutes, pipeline

    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    with srv.db.session() as s:
        m = s.get(Meeting, mid)
        m.title = "Бюджет и сайт: Q4/2026"
        m.minutes = minutes.empty_minutes("Обобщение")
    pipeline._render(srv, mid)
    r = client.get(f"/api/meetings/{mid}/export.md")
    assert r.status_code == 200, r.text
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert "Обобщение" in r.text


def test_remember_voice_creates_profile(srv, client):
    """Regression: 'remember voice' for a new person crashed (500)."""
    import json as _json

    from bix.server.core import key

    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    with srv.db.session() as s:
        org_id = s.get(Meeting, mid).org_id
    srv.storage.put(key(org_id, mid, "embeddings.json"), _json.dumps({"S1": [0.6, 0.8]}).encode())
    r = client.post(f"/api/meetings/{mid}/speakers", json={"label": "S1", "name": "Мария", "remember": True})
    assert r.status_code == 400  # no consent
    r = client.post(f"/api/meetings/{mid}/speakers", json={"label": "S1", "name": "Мария", "remember": True, "consent": True})
    assert r.status_code == 200, r.text
    people = client.get("/api/people").json()
    assert people[0]["name"] == "Мария" and people[0]["has_voice"] and people[0]["consent_at"]


def test_invalid_emails_rejected(srv, client):
    """Regression: invalid recipient addresses were stored and used for sending."""
    setup_admin(client)
    r = client.post("/api/rooms", json={"name": "Зала", "recipients": ["ok@test.bg", "bad-address"]})
    assert r.status_code == 400 and "bad-address" in r.json()["detail"]
    assert client.post("/api/rooms", json={"name": "  "}).status_code == 400
    r = client.post("/api/rooms", json={"name": "Зала", "recipients": ["OK@Test.bg ", "ok@test.bg"]})
    assert r.json()["recipients"] == ["ok@test.bg"]
    assert client.patch("/api/settings", json={"settings": {"fixed_recipients": ["nope"]}}).status_code == 400
    assert client.post("/api/people", json={"name": "Иван", "email": "ivan@"}).status_code == 400


def test_schema_upgrade_adds_new_columns(tmp_path):
    """Regression: a database from an older version lacked new columns (no migrations)."""
    import sqlite3

    from bix.server.db import Database

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meetings (id VARCHAR(32) PRIMARY KEY, org_id VARCHAR(32), title VARCHAR(300))")
    con.execute("INSERT INTO meetings VALUES ('a', 'o', 'стара')")
    con.commit()
    con.close()
    Database(f"sqlite:///{path.as_posix()}").create_all()
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(meetings)")}
    assert {"title_auto", "minutes", "has_audio"} <= cols
    assert con.execute("SELECT title, title_auto FROM meetings").fetchone() == ("стара", 1)


def test_typed_title_survives_generated_minutes(srv, client, monkeypatch):
    """Regression: a title starting with "Среща " was treated as a placeholder and replaced."""
    from bix.server import minutes, pipeline

    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    srv.cfg.llm.provider = "fake"
    monkeypatch.setattr(minutes, "generate", lambda *a, **k: {**minutes.empty_minutes("x"), "title": "Генерирано заглавие"})
    pipeline.process(srv, mid, "minutes")
    assert client.get(f"/api/meetings/{mid}").json()["title"] == "Генерирано заглавие"  # placeholder replaced
    client.patch(f"/api/meetings/{mid}", json={"title": "Среща с клиент №1"})
    pipeline.process(srv, mid, "minutes")
    assert client.get(f"/api/meetings/{mid}").json()["title"] == "Среща с клиент №1"  # typed title kept
    assert client.patch(f"/api/meetings/{mid}", json={"title": "  "}).status_code == 400


def test_login_throttle_is_per_account(srv, client):
    """Regression: 10 failures from one IP locked every account behind that IP."""
    from bix.server import api_auth

    api_auth._failures.clear()
    setup_admin(client)
    client.post("/auth/logout")
    codes = [client.post("/auth/login", json={"email": "intruder@test.bg", "password": "x" * 12}).status_code for _ in range(11)]
    assert codes[-1] == 429
    assert client.post("/auth/login", json={"email": "admin@test.bg", "password": "correct horse battery"}).status_code == 200


def test_transient_failure_shows_retry_not_failed(srv, client, monkeypatch):
    """Regression: the meeting said 'failed' while a retry was already scheduled."""
    from bix.server import stt as stt_mod
    from bix.server.stt.base import SttError

    class Down:
        name, diarizes = "down", False

        def transcribe(self, *a, **k):
            raise SttError("Whisper сървърът не отговаря (http://x).")

    setup_admin(client)
    _, token = enroll_device(client)
    monkeypatch.setattr(stt_mod, "make", lambda cfg, provider=None: Down())
    mid = record(client, token)
    Worker(srv).run_once()
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["status"] == "queued" and "Нов опит" in m["error"]
    job = [j for j in client.get("/api/jobs").json() if j["meeting_id"] == mid][0]
    assert job["status"] == "queued"


def test_smtp_down_gives_readable_error(srv):
    from bix.server.config import SmtpCfg
    from bix.server.integrations import DeliveryError, smtp_send

    try:
        smtp_send(SmtpCfg(host="127.0.0.1", port=9, starttls=False, sender="bix@b.bg"), ["a@b.bg"], "s", "<p>x</p>", "x")
    except DeliveryError as e:
        assert "не отговаря" in str(e) and "WinError" not in str(e)
    else:
        raise AssertionError("expected DeliveryError")


def test_settings_validation(srv, client):
    """Regression: -5 days was silently stored as 0 (= never delete); text gave 500."""
    setup_admin(client)
    assert client.patch("/api/settings", json={"settings": {"retention_audio_days": -5}}).status_code == 400
    assert client.patch("/api/settings", json={"settings": {"retention_audio_days": "abc"}}).status_code == 400
    assert client.patch("/api/settings", json={"settings": {"max_meeting_hours": 0}}).status_code == 400
    assert client.get("/api/settings").json()["settings"]["retention_audio_days"] == 30
    assert client.patch("/api/settings", json={"settings": {"retention_audio_days": 90}}).json()["settings"]["retention_audio_days"] == 90


def test_search_is_case_insensitive_for_cyrillic(srv, client):
    """Regression: SQLite LIKE folds only ASCII, so 'Румъния' did not match 'румъния'."""
    setup_admin(client)
    _, token = enroll_device(client)
    mid = record(client, token)
    drain(srv)
    with srv.db.session() as s:
        s.get(Meeting, mid).transcript_text = "клиентите от румъния питат"
    assert client.get("/api/meetings", params={"q": "Румъния"}).json()["total"] == 1
    assert client.get("/api/meetings", params={"q": "РУМЪНИЯ"}).json()["total"] == 1


def test_isolated_processing_reports_child_errors(srv, client):
    """F-043: processing runs in a child process; its errors come back to the job."""
    from bix.server.db import Job, Meeting, Org

    srv.cfg.worker.isolate = True
    setup_admin(client)
    with srv.db.session() as s:
        org = s.query(Org).first()
        m = Meeting(org_id=org.id, status="queued", title="без звук")
        s.add(m)
        s.flush()
        s.add(Job(kind="process", meeting_id=m.id, args={}))
        mid = m.id
    assert Worker(srv).run_once()
    job = client.get("/api/jobs").json()[0]
    assert job["status"] == "failed" and "Няма записан звук" in job["error"]
    assert client.get(f"/api/meetings/{mid}").json()["status"] == "failed"


def test_naming_reuses_existing_person(srv, client):
    """Typing an existing name (any case) links to that person instead of a duplicate."""
    setup_admin(client)
    _, token = enroll_device(client)
    m1, m2 = record(client, token), record(client, token)
    drain(srv)
    client.post(f"/api/meetings/{m1}/speakers", json={"label": "S1", "name": "Мария", "email": "maria@test.bg"})
    client.post(f"/api/meetings/{m2}/speakers", json={"label": "S1", "name": "мария"})
    people = client.get("/api/people").json()
    assert [p["name"] for p in people] == ["Мария"]
    assert client.get(f"/api/meetings/{m2}").json()["speakers"][0]["person_id"] == people[0]["id"]
