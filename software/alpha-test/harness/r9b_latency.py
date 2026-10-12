"""R9b: is the API responsive while a 20-minute meeting is processed?"""
import io
import time

import httpx
import numpy as np
import soundfile as sf

from api import AT, admin

a = admin()
x, sr = sf.read(AT / "tm" / "meeting.wav", dtype="float32")
buf = io.BytesIO()
sf.write(buf, np.concatenate([x] * 11), sr, format="FLAC")
t0 = time.time()
mid = a.post("/api/meetings/upload", files={"file": ("дълга.flac", buf.getvalue(), "audio/flac")}, data={"title": "Латентност"}, timeout=300).json()["id"]
print(f"upload took {time.time()-t0:.1f}s")
worst, step, samples = 0.0, "", []
while time.time() - t0 < 1500:
    s0 = time.time()
    try:
        httpx.get("http://127.0.0.1:8720/healthz", timeout=120)
    except httpx.HTTPError as e:
        print("healthz error", e)
    lat = time.time() - s0
    m = a.get(f"/api/meetings/{mid}", timeout=300).json()
    cur = next((k for k, v in m["steps"].items() if v.get("state") == "running"), "")
    samples.append((cur, lat))
    if lat > worst:
        worst, step = lat, cur
    if m["status"] in ("done", "failed"):
        break
    time.sleep(1)
print(f"status {m['status']} after {time.time()-t0:.0f}s | worst /healthz {worst:.1f}s during step '{step}'")
by = {}
for st, lat in samples:
    by.setdefault(st, []).append(lat)
for st, ls in by.items():
    print(f"   {st or '-':9} n={len(ls):3} max={max(ls):5.1f}s avg={sum(ls)/len(ls):.2f}s")
for k, v in m["steps"].items():
    print(f"   step {k:9} {v.get('took_s')}s")
