"""J9: dependencies down - STT, LLM, SMTP. What does the user see?"""
import time

from api import AT, admin
from ui import BASE, browser, report, shot

a = admin()
st = a.get("/api/status").json()
print("status stt:", st["stt"]["ok"], st["stt"]["detail"][:80])
print("status llm:", st["llm"]["ok"], st["llm"]["detail"][:80])

# upload → STT fails
with (AT / "files" / "stereo48k.wav").open("rb") as f:
    mid = a.post("/api/meetings/upload", files={"file": ("dead-stt.wav", f, "audio/wav")}, data={"title": "Тест: STT е недостъпен"}).json()["id"]
t0 = time.time()
while time.time() - t0 < 60:
    m = a.get(f"/api/meetings/{mid}").json()
    if m["status"] in ("failed", "done") or "Нов опит" in m["error"]:
        break
    time.sleep(2)
print("after STT outage:", m["status"], "|", m["error"][:150])
jobs = [j for j in a.get("/api/jobs").json() if j["meeting_id"] == mid]
print("job:", jobs[0]["status"], "attempts", jobs[0]["attempts"], "| error:", jobs[0]["error"][:100])

# send minutes with SMTP down (the meeting from J3 has minutes)
good = (AT / "mid.txt").read_text()
r = a.post(f"/api/meetings/{good}/send", json={"to": []})
print("send with SMTP down:", r.status_code, r.json().get("detail", "")[:150])

with browser() as page:
    page.goto(BASE + f"/#/meetings/{mid}")
    page.wait_for_timeout(1500)
    shot(page, "j9_01_stt_down")
    page.goto(BASE + "/#/settings")
    page.wait_for_timeout(2500)
    shot(page, "j9_02_status")
report()
