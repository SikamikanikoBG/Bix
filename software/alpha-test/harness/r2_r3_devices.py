"""R2: discard + double clicks. R3: two devices in two rooms at the same time."""
import subprocess
import time
from pathlib import Path

import httpx

from api import AT, admin

H = Path(__file__).parent
A, B = "http://127.0.0.1:8721", "http://127.0.0.1:8722"
X = {"X-Bix": "1"}
a = admin()

# --- device B in its own room --------------------------------------------
room_b = a.post("/api/rooms", json={"name": "Пловдив · Зала 2"}).json()
code = a.post("/api/devices/enrollment-codes", json={"room_id": room_b["id"]}).json()["code"]
subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(H / "start.ps1"), "-what", "deviceB"],
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
for _ in range(60):
    try:
        httpx.get(B + "/api/status", timeout=2)
        break
    except httpx.HTTPError:
        time.sleep(0.5)
print("B enroll:", httpx.post(B + "/api/enroll", json={"server": "http://127.0.0.1:8720", "code": code}, headers=X).status_code)
time.sleep(2)
print("B room:", httpx.get(B + "/api/status").json()["policy"]["room"])

# --- R2: double clicks, discard ---------------------------------------------
before = {m["id"] for m in a.get("/api/meetings").json()["items"]}
r1 = httpx.post(A + "/api/start", headers=X).json()
r2 = httpx.post(A + "/api/start", headers=X).json()
print("double start → same meeting:", r1 == r2)
time.sleep(25)
spool = AT / "device" / "spool"
print("spool while recording:", [p.name for p in spool.iterdir()])
httpx.post(A + "/api/discard", headers=X)
httpx.post(A + "/api/discard", headers=X)  # double click
time.sleep(8)
after = {m["id"] for m in a.get("/api/meetings").json()["items"]}
print("discard → nothing new on server:", after == before, "| spool empty:", not any(spool.iterdir()))
print("double stop when idle:", httpx.post(A + "/api/stop", headers=X).status_code, httpx.post(A + "/api/stop", headers=X).status_code)

# --- R3: both rooms record at once -------------------------------------------
t0 = time.time()
httpx.post(A + "/api/start", headers=X)
httpx.post(B + "/api/start", headers=X)
time.sleep(40)
httpx.post(A + "/api/stop", headers=X)
httpx.post(B + "/api/stop", headers=X)
for _ in range(80):
    new = [m for m in a.get("/api/meetings").json()["items"] if m["started_at"] > t0 - 5]
    if len(new) == 2 and all(m["status"] in ("done", "failed") for m in new):
        break
    time.sleep(4)
for m in new:
    print(f"room={m['room']!r:40} status={m['status']} duration={m['duration_s']:.0f}s speakers={m['speakers']}")
print("rooms distinct:", len({m["room_id"] for m in new}) == 2)
