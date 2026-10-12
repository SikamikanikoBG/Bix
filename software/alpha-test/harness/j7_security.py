"""J7 + J10: roles, access matrix, CSRF, IDs, headers, secrets."""
import httpx

from api import AT, admin, login

a = admin()
mid = (AT / "mid.txt").read_text()
out = []


def check(name, cond, detail=""):
    out.append(("OK " if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# users
r = a.post("/api/users", json={"email": "member@alpha.test", "name": "Член", "role": "member", "password": "member-pass-1234"})
check("create member", r.status_code in (200, 409), r.text)
r = a.post("/api/users", json={"email": "audit@alpha.test", "name": "Одитор", "role": "auditor", "password": "audit-pass-1234"})
check("create auditor", r.status_code in (200, 409), r.text)
check("duplicate email (case)", a.post("/api/users", json={"email": "MEMBER@alpha.test", "role": "member"}).status_code == 409)
check("weak password", a.post("/api/users", json={"email": "x@alpha.test", "role": "member", "password": "123"}).status_code == 400)
check("bad role", a.post("/api/users", json={"email": "y@alpha.test", "role": "root"}).status_code == 400)
me = a.get("/auth/me").json()
check("self-demotion blocked", a.patch(f"/api/users/{me['id']}", json={"role": "member"}).status_code == 400)
check("self-disable blocked", a.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400)

m = login("member@alpha.test", "member-pass-1234")
au = login("audit@alpha.test", "audit-pass-1234")
anon = httpx.Client(base_url="http://127.0.0.1:8720", headers={"X-Bix": "1"})

matrix = [
    ("GET", f"/api/meetings/{mid}", {"admin": 200, "member": 404, "auditor": 403, "anon": 401}),
    ("GET", f"/api/meetings/{mid}/transcript", {"admin": 200, "member": 404, "auditor": 403, "anon": 401}),
    ("GET", f"/api/meetings/{mid}/audio", {"admin": 200, "member": 404, "auditor": 403, "anon": 401}),
    ("GET", "/api/users", {"admin": 200, "member": 403, "auditor": 403, "anon": 401}),
    ("GET", "/api/audit", {"admin": 200, "member": 403, "auditor": 200, "anon": 401}),
    ("GET", "/api/people", {"admin": 200, "member": 200, "auditor": 403, "anon": 401}),
    ("GET", "/api/devices", {"admin": 200, "member": 403, "auditor": 200, "anon": 401}),
    ("GET", "/api/status", {"admin": 200, "member": 403, "auditor": 403, "anon": 401}),
    ("POST", "/api/rooms", {"admin": None, "member": 403, "auditor": 403, "anon": 401}),
    ("DELETE", f"/api/meetings/{mid}", {"admin": None, "member": 403, "auditor": 403, "anon": 401}),
]
clients = {"admin": a, "member": m, "auditor": au, "anon": anon}
for method, path, exp in matrix:
    for role, want in exp.items():
        if want is None:
            continue
        r = clients[role].request(method, path, json={"name": "x"} if method == "POST" else None)
        check(f"{role:7} {method:6} {path[:55]}", r.status_code == want, f"got {r.status_code}, want {want}")

# member sees nothing they didn't attend; auditor sees no titles
check("member meeting list empty", m.get("/api/meetings").json()["total"] == 0)
items = au.get("/api/meetings").json()["items"]
check("auditor sees no titles/speakers", all(i["title"] == "—" and not i["speakers"] for i in items))
check("auditor search does not leak text", au.get("/api/meetings", params={"q": "Румъния"}).json()["total"] == len(items))

# CSRF and IDs
check("CSRF: no X-Bix → 403", httpx.post("http://127.0.0.1:8720/api/rooms", json={"name": "x"}, cookies=a.cookies).status_code == 403)
for bad in ["../../etc/passwd", "..%2f..%2fserver.toml", "x" * 32, "A" * 32, "'; DROP TABLE meetings; --"]:
    r = a.get(f"/api/meetings/{bad}")
    check(f"bad id {bad[:20]!r}", r.status_code in (404, 422), str(r.status_code))
check("offset negative", a.get("/api/meetings", params={"offset": -5}).status_code in (200, 422))
check("limit huge", a.get("/api/meetings", params={"limit": 10**9}).status_code == 200)

# headers & secrets
r = a.get("/")
for h in ["content-security-policy", "x-frame-options", "x-content-type-options"]:
    check(f"header {h}", h in r.headers)
cookie = [c for c in a.cookies.jar][0]
check("session cookie HttpOnly", cookie.has_nonstandard_attr("HttpOnly") or "httponly" in str(cookie._rest).lower())
s = a.get("/api/settings").text
check("no secret_key in settings", "secret_key" not in s)
check("no API keys in settings", "sk-ant" not in s)
logs = (AT / "server" / "logs" / "server.log").read_text(encoding="utf-8", errors="replace")
check("no passwords in logs", "alpha-test-pass-123" not in logs and "member-pass-1234" not in logs)
check("metrics local ok", anon.get("/metrics").status_code == 200)
check("openapi docs reachable (info)", anon.get("/api/docs").status_code == 200)

# login throttling
bad = httpx.Client(base_url="http://127.0.0.1:8720", headers={"X-Bix": "1"})
codes = [bad.post("/auth/login", json={"email": "nobody@alpha.test", "password": "wrong-wrong-1"}).status_code for _ in range(11)]
check("login throttled after 10 failures", codes[-1] == 429, str(codes))
check("admin can still log in after attack", bool(admin()))

# disabled user loses access immediately
uid = [u for u in a.get("/api/users").json() if u["email"] == "member@alpha.test"][0]["id"]
a.patch(f"/api/users/{uid}", json={"active": False})
check("disabled user session revoked", m.get("/api/people").status_code == 401)
a.patch(f"/api/users/{uid}", json={"active": True})

print("\n".join(out))
print(sum(1 for x in out if x.startswith("FAIL")), "failures")
