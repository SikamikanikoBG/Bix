"""J8 anonymous mode + J12 CLI/ops."""
import os
import subprocess
import time
from pathlib import Path

from api import AT, admin

BIX = str(Path(__file__).resolve().parents[2] / ".venv" / "Scripts" / "bix.exe")
env = {**os.environ, "PYTHONUTF8": "1", "BIX_SERVER_CONFIG": str(AT / "server" / "server.toml")}
a = admin()

# J8: anonymous mode - names must not appear, even after reprocessing
a.patch("/api/settings", json={"settings": {"speaker_mode": "anonymous"}})
mid = (AT / "mid.txt").read_text()
a.post(f"/api/meetings/{mid}/reprocess", json={"step": "speakers"})
for _ in range(60):
    m = a.get(f"/api/meetings/{mid}").json()
    if m["status"] == "done":
        break
    time.sleep(3)
print("anonymous: speaker names =", [s["name"] for s in m["speakers"]], "| list shows:", a.get("/api/meetings").json()["items"][-1]["speakers"])
print("anonymous: remember voice →", a.post(f"/api/meetings/{mid}/speakers", json={"label": "S1", "name": "X", "remember": True, "consent": True}).status_code)
a.patch("/api/settings", json={"settings": {"speaker_mode": "voice_profiles"}})
print("invalid speaker_mode →", a.patch("/api/settings", json={"settings": {"speaker_mode": "spy"}}).status_code)
print("negative retention stored as →", a.patch("/api/settings", json={"settings": {"retention_audio_days": -5}}).json()["settings"]["retention_audio_days"])
print("text in number field →", a.patch("/api/settings", json={"settings": {"retention_audio_days": "abc"}}).status_code)
a.patch("/api/settings", json={"settings": {"retention_audio_days": 30}})
