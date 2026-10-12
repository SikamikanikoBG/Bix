import httpx, json
from pathlib import Path
AT = Path(__file__).parent / "work"
def admin():
    d = json.loads((AT / "cred.json").read_text())
    c = httpx.Client(base_url="http://127.0.0.1:8720", headers={"X-Bix": "1"}, timeout=60)
    c.post("/auth/login", json={"email": d["email"], "password": d["password"]}).raise_for_status()
    return c
def login(email, pw):
    c = httpx.Client(base_url="http://127.0.0.1:8720", headers={"X-Bix": "1"}, timeout=60)
    r = c.post("/auth/login", json={"email": email, "password": pw}); r.raise_for_status()
    return c
