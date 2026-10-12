"""J9b: after the outage, the scheduled retry finishes by itself."""
import time

from api import admin

a = admin()
mid = [m for m in a.get("/api/meetings").json()["items"] if m["title"].startswith("Тест: STT")][0]["id"]
for _ in range(60):
    m = a.get(f"/api/meetings/{mid}").json()
    if m["status"] in ("done", "failed"):
        break
    time.sleep(5)
print("recovered:", m["status"], "| error:", m["error"] or "-", "| title kept:", m["title"])
