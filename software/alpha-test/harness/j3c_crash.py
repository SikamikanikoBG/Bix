"""J3c: the device process dies during a meeting; after restart the recording is recovered."""
import subprocess
import time

import httpx

from api import AT, admin

P = "http://127.0.0.1:8721"
H = {"X-Bix": "1"}


def ps(script, what):
    # no captured pipes: the started server/device would inherit them and we'd wait forever
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(AT / script), "-what", what],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def up(url):
    for _ in range(80):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


a = admin()
t2 = time.time()
httpx.post(P + "/api/start", headers=H)
time.sleep(30)
ps("stop.ps1", "device")  # hard kill, like a power cut
print("spool after crash:", [p.name for p in (AT / "device" / "spool").iterdir()])
ps("start.ps1", "device")
print("device restarted:", up(P + "/api/status"))
m = None
for _ in range(60):
    items = [x for x in a.get("/api/meetings").json()["items"] if x["started_at"] > t2 - 5]
    m = items[0] if items else None
    if m and m["status"] in ("done", "failed"):
        break
    time.sleep(4)
print(f"crash meeting: {m and m['status']} duration={m and round(m['duration_s'])}s (≈ 20-30 expected) error={(m or {}).get('error') or '-'}")
print("device state after restart:", httpx.get(P + "/api/status").json()["state"])
