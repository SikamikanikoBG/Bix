"""Authentication and authorisation.

People: local accounts (scrypt) and/or OIDC single sign-on (Entra ID, Google,
Okta, Keycloak). Sessions are HMAC-signed cookies; state-changing requests
must carry the X-Bix header, which a cross-site form cannot send (CSRF).

Devices: a per-device bearer token from enrollment, optionally pinned to the
device certificate fingerprint passed by the mTLS-terminating proxy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from bix.server.db import Device, Meeting, Person, User

SESSION_COOKIE = "bix_session"


# --- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, n, r, p, salt, dk = stored.split("$")
    test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(test, base64.b64decode(dk))


def password_problems(password: str) -> str:
    if len(password) < 12:
        return "Паролата трябва да е поне 12 знака."
    return ""


# --- signed tokens -----------------------------------------------------------


def sign(secret_key: str, payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(secret_key: str, token: str) -> dict[str, Any] | None:
    try:
        body, mac = token.rsplit(".", 1)
    except ValueError:
        return None
    good = hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- request context -------------------------------------------------------


@dataclass
class Principal:
    user: User

    @property
    def actor(self) -> str:
        return f"user:{self.user.email}"

    @property
    def org_id(self) -> str:
        return self.user.org_id

    def is_admin(self) -> bool:
        return self.user.role == "admin"


def is_trusted(ip: str, trusted: list[str]) -> bool:
    """ip matches one of the addresses or CIDR ranges in trusted."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for t in trusted:
        try:
            if addr in ipaddress.ip_network(t.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    srv = request.app.state.srv
    peer = request.client.host if request.client else ""
    if is_trusted(peer, srv.cfg.http.trusted_proxies):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def current_user(request: Request) -> Principal:
    srv = request.app.state.srv
    token = request.cookies.get(SESSION_COOKIE, "")
    data = unsign(srv.cfg.auth.secret_key, token) if token else None
    if not data or data.get("t") != "session":
        raise HTTPException(401, "Нужен е вход.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-bix") != "1":
        raise HTTPException(403, "Липсва заглавка X-Bix (CSRF защита).")
    with srv.db.session() as s:
        user = s.get(User, data["uid"])
        if not user or not user.active or data.get("v") != _session_version(user):
            raise HTTPException(401, "Сесията е изтекла.")
        s.expunge(user)
    return Principal(user)


def _session_version(user: User) -> str:
    # Changes when the password or role changes, which logs out old sessions.
    return hashlib.sha256(f"{user.password_hash}|{user.role}|{user.active}".encode()).hexdigest()[:12]


def session_cookie(srv, user: User) -> str:
    return sign(srv.cfg.auth.secret_key, {"t": "session", "uid": user.id, "v": _session_version(user), "exp": time.time() + srv.cfg.auth.session_hours * 3600})


def require(*roles: str):
    def dep(p: Principal = Depends(current_user)) -> Principal:
        if p.user.role not in roles:
            raise HTTPException(403, "Нямате права за това.")
        return p

    return dep


admin_only = require("admin")
any_user = require("admin", "member", "auditor")
content_reader = require("admin", "member")


def can_read_meeting(s, p: Principal, m: Meeting) -> bool:
    """Admins: all. Members: meetings they organised, attended, uploaded or were recognised in."""
    if m.org_id != p.org_id:
        return False
    if p.user.role == "admin":
        return True
    if p.user.role != "member":
        return False
    from bix.server.db import Org

    if s.get(Org, m.org_id).setting("members_see_all_meetings"):
        return True
    email = p.user.email.lower()
    if m.created_by == p.user.id or (m.organizer_email or "").lower() == email:
        return True
    if any((a.get("email") or "").lower() == email for a in m.attendees or []):
        return True
    pids = [x.get("person_id") for x in m.speakers or [] if x.get("person_id")]
    if pids:
        for person in s.scalars(select(Person).where(Person.id.in_(pids))).all():
            if person.email.lower() == email:
                return True
    return False


# --- devices -----------------------------------------------------------------


@dataclass
class DevicePrincipal:
    device_id: str
    org_id: str
    room_id: str | None

    @property
    def actor(self) -> str:
        return f"device:{self.device_id}"


def current_device(request: Request) -> DevicePrincipal:
    srv = request.app.state.srv
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "device token required")
    th = token_hash(auth[7:].strip())
    with srv.db.session() as s:
        d = s.scalars(select(Device).where(Device.token_hash == th)).first()
        if not d or d.revoked:
            raise HTTPException(401, "unknown or revoked device")
        hdr = srv.cfg.http.mtls_fingerprint_header
        if hdr:
            fp = request.headers.get(hdr, "").lower().replace(":", "")
            if not fp:
                raise HTTPException(401, "client certificate required")
            if d.cert_fingerprint and d.cert_fingerprint != fp:
                raise HTTPException(401, "client certificate does not match device")
            if not d.cert_fingerprint:
                d.cert_fingerprint = fp  # pin on first use
        return DevicePrincipal(d.id, d.org_id, d.room_id)
