"""R9: a 20-minute meeting (upload) and very long titles."""
import io
import time

import numpy as np
import soundfile as sf

from api import AT, admin
from ui import BASE, browser, report, shot

a = admin()
x, sr = sf.read(AT / "tm" / "meeting.wav", dtype="float32")
long = np.concatenate([x] * 11)  # ≈ 19 min
buf = io.BytesIO()
sf.write(buf, long, sr, format="FLAC")
print(f"audio {len(long)/sr/60:.1f} min, {len(buf.getvalue())/1e6:.1f} MB FLAC")
t0 = time.time()
r = a.post("/api/meetings/upload", files={"file": ("дълга среща.flac", buf.getvalue(), "audio/flac")}, data={"title": ""})
mid = r.json()["id"]
print(f"upload {time.time()-t0:.1f}s")
while time.time() - t0 < 1200:
    m = a.get(f"/api/meetings/{mid}").json()
    if m["status"] in ("done", "failed"):
        break
    time.sleep(10)
print(f"status {m['status']} after {time.time()-t0:.0f}s | error={m['error'] or '-'}")
for k, v in m["steps"].items():
    print(f"   {k:9} {v.get('took_s')}s {({kk: vv for kk, vv in v.items() if kk not in ('state', 'at', 'took_s')})}")
mins = m["minutes"] or {}
print("minutes:", {k: len(v) for k, v in mins.items() if isinstance(v, list)}, "| summary chars", len(mins.get("summary", "")))

r = a.patch(f"/api/meetings/{mid}", json={"title": "Дълго заглавие " + "я" * 400})
print("400-char title:", r.status_code, "| stored length", len(a.get(f"/api/meetings/{mid}").json()["title"]))
with browser() as page:
    t1 = time.time()
    page.goto(BASE + f"/#/meetings/{mid}")
    page.wait_for_selector(".tabs")
    page.click('.tabs button[data-tab="transcript"]')
    page.wait_for_selector(".transcript p")
    print(f"transcript tab: {page.locator('.transcript p').count()} lines, rendered in {time.time()-t1:.1f}s")
    shot(page, "r9_long_title", full=False)
    over = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    print("horizontal overflow with long title:", over)
report()
