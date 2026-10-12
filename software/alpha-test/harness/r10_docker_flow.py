"""R10: full flow against the Docker container on vader (setup → upload → processed)."""
import sys
import time

import httpx

from api import AT

base = sys.argv[1] if len(sys.argv) > 1 else "http://<vllm-server>:18730"
c = httpx.Client(base_url=base, headers={"X-Bix": "1"}, timeout=120)
print("setup:", c.post("/auth/setup", json={"org_name": "Docker тест", "email": "admin@docker.test", "password": "docker-test-pass-1"}).status_code)
f = AT / "tm" / "meeting.wav"
r = c.post("/api/meetings/upload", files={"file": ("Среща.wav", f.read_bytes(), "audio/wav")}, data={"title": ""})
mid = r.json()["id"]
t0 = time.time()
while time.time() - t0 < 300:
    m = c.get(f"/api/meetings/{mid}").json()
    if m["status"] in ("done", "failed"):
        break
    time.sleep(3)
print(f"processed in container: {m['status']} in {time.time()-t0:.0f}s | title={m['title']!r} | speakers={[s['label'] for s in m['speakers']]} | decisions={len((m['minutes'] or {}).get('decisions', []))} | error={m['error'] or '-'}")
print("portal page:", c.get("/").status_code, "| static js:", c.get("/static/app.js").status_code, "| metrics from remote:", c.get("/metrics").status_code)
