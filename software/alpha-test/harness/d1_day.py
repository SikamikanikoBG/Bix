"""A day in the room, on the room screen: booked meeting now → start it → ask Bix →
⭐ → countdown → end. Screenshots at the real screen size (1280 x 720)."""
import json
import sys
import time
from datetime import datetime

import httpx

from api import AT, admin
from ui import browser, shot

DEV = "http://127.0.0.1:8721"
X = {"X-Bix": "1"}
a = admin()
a.patch("/api/settings", json={"face_style": "pixel", "live_transcription": False}).raise_for_status()

rooms = a.get("/api/rooms").json()
room = next((r for r in rooms if r["name"] == "Зала Витоша"), None) or a.post("/api/rooms", json={"name": "Зала Витоша", "recipients": ["office@example.com"]}).json()
st = httpx.get(DEV + "/api/status", headers=X).json()
if not st["enrolled"]:
    code = a.post("/api/devices/enrollment-codes", json={"room_id": room["id"]}).json()["code"]
    print("enroll:", httpx.post(DEV + "/api/enroll", json={"server": "http://127.0.0.1:8720", "code": code}, headers=X).status_code)
    time.sleep(3)

# today's bookings: one running now, one later, a weekly one tomorrow morning
for ev in a.get(f"/api/rooms/{room['id']}/calendar").json()["events"]:
    if ev["booking_id"]:
        a.delete(f"/api/bookings/{ev['booking_id']}")
now = time.time()
for title, s0, dur, rep in [("Седмичен преглед", now - 300, 3600, "weekly"), ("Ретро на спринта", now + 4200, 1800, ""), ("Обяд с клиент", now + 7200, 3600, "")]:
    r = a.post(f"/api/rooms/{room['id']}/bookings", json={
        "title": title, "start": s0, "end": s0 + dur, "organizer_name": "Мария Петрова", "organizer_email": "maria@example.com",
        "attendees": ["Иван Георгиев <ivan@example.com>", "Елена <elena@example.com>", "Петър"], "repeat": rep,
    })
    print("booking", title, r.status_code, r.text[:80])
cal = a.get(f"/api/rooms/{room['id']}/calendar").json()
print("calendar:", [(e["title"], datetime.fromtimestamp(e["start"]).strftime("%a %H:%M")) for e in cal["events"]], cal["problem"])

# the device picks the agenda up with the next heartbeat
for _ in range(30):
    st = httpx.get(DEV + "/api/status", headers=X).json()
    if st["agenda"]:
        break
    time.sleep(1)
print("device agenda:", [e["title"] for e in st["agenda"]], "face:", st["policy"].get("face_style"))

with browser() as page:
    page.set_viewport_size({"width": 1280, "height": 720})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(DEV + "/")
    page.wait_for_timeout(2500)
    shot(page, "d1_01_idle_booked", full=False)
    page.click("#btnStartEvent")
    page.wait_for_timeout(2500)
    shot(page, "d1_02_recording", full=False)
    page.click("#btnStar")
    page.wait_for_timeout(1200)
    page.click("#btnAsk")
    page.wait_for_timeout(600)
    shot(page, "d1_03_ask", full=False)
    if "--ask" in sys.argv:
        page.wait_for_timeout(45000)  # let some of the meeting be said first
        t = time.time()
        page.click('[data-preset="decisions"]')
        page.wait_for_selector("#askAnswer:not([hidden])", timeout=120000)
        print(f"answer in {time.time() - t:.1f}s:", page.inner_text("#askAnswer")[:400])
        shot(page, "d1_04_answer", full=False)
    page.click("#btnAskClose")
    # an automatic stop coming (shown as the agent would send it)
    page.evaluate("st.ending = {reason: 'next_meeting', left_s: 47, next: 'Ретро на спринта', next_start: Date.now()/1000 + 240}; render()")
    page.wait_for_timeout(700)
    shot(page, "d1_05_countdown", full=False)
    page.click("#btnKeep")
    page.wait_for_timeout(1200)
    page.click("#btnStop")
    page.wait_for_timeout(2500)
    shot(page, "d1_06_done", full=False)
    page.click("#btnDone")
    page.wait_for_timeout(1500)
    shot(page, "d1_07_idle_after", full=False)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(800)
    shot(page, "d1_08_phone", full=True)
    print("overflow:", page.evaluate("document.documentElement.scrollWidth"), "errors:", errs)
print(json.dumps(httpx.get(DEV + "/api/status", headers=X).json()["last_meeting"], ensure_ascii=False))
