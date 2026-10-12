from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from bix.server import oidc
from bix.server.auth import (
    SESSION_COOKIE,
    Principal,
    client_ip,
    current_user,
    password_problems,
    hash_password,
    session_cookie,
    sign,
    unsign,
    verify_password,
)
from bix.server.db import User, audit, first_org

router = APIRouter()

_failures: dict[str, list[float]] = defaultdict(list)


def _throttle(ip: str, email: str) -> None:
    """10 failures per account+IP or 100 per IP in 15 min. Per account+IP so that
    one person (or attacker) behind a shared office NAT can't lock out everyone."""
    now = time.time()
    for key, limit in ((f"{ip}|{email}", 10), (ip, 100)):
        _failures[key] = [t for t in _failures[key] if now - t < 900]
        if len(_failures[key]) >= limit:
            raise HTTPException(429, "Твърде много неуспешни опити. Опитайте след 15 минути.")


def _failed(ip: str, email: str) -> None:
    now = time.time()
    _failures[f"{ip}|{email}"].append(now)
    _failures[ip].append(now)


def _set_cookie(request: Request, response: Response, name: str, value: str, max_age: int) -> None:
    srv = request.app.state.srv
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=srv.cfg.http.cookie_secure, path="/")


class LoginIn(BaseModel):
    email: str
    password: str


@router.get("/auth/config")
def auth_config(request: Request) -> dict:
    srv = request.app.state.srv
    with srv.db.session() as s:
        need_setup = s.scalar(select(func.count()).select_from(User)) == 0
    return {"local": srv.cfg.auth.local_login, "oidc": srv.cfg.auth.oidc_enabled, "setup": need_setup}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    srv = request.app.state.srv
    if not srv.cfg.auth.local_login:
        raise HTTPException(403, "Входът с парола е изключен; използвайте SSO.")
    ip = client_ip(request)
    email = body.email.strip().lower()
    _throttle(ip, email)
    with srv.db.session() as s:
        user = s.scalars(select(User).where(func.lower(User.email) == email)).first()
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            _failed(ip, email)
            audit(s, f"user:{body.email.strip().lower()}", "auth.login_failed", ip=ip)
            raise HTTPException(401, "Грешен имейл или парола.")
        user.last_login = time.time()
        audit(s, f"user:{user.email}", "auth.login", org_id=user.org_id, ip=ip)
        _set_cookie(request, response, SESSION_COOKIE, session_cookie(srv, user), srv.cfg.auth.session_hours * 3600)
        return {"ok": True}


class SetupIn(BaseModel):
    org_name: str
    email: str
    name: str = ""
    password: str


@router.post("/auth/setup")
def setup(body: SetupIn, request: Request, response: Response) -> dict:
    """First run: create the organisation and its first admin."""
    srv = request.app.state.srv
    if problem := password_problems(body.password):
        raise HTTPException(400, problem)
    from bix.server.db import Org

    with srv.db.session() as s:
        if s.scalar(select(func.count()).select_from(User)) > 0:
            raise HTTPException(409, "Вече е настроено.")
        org = first_org(s) or Org(name=body.org_name.strip() or "Организация")
        s.add(org)
        s.flush()
        user = User(org_id=org.id, email=body.email.strip().lower(), name=body.name.strip(), role="admin", password_hash=hash_password(body.password))
        s.add(user)
        s.flush()
        audit(s, f"user:{user.email}", "setup.complete", org_id=org.id, ip=client_ip(request))
        _set_cookie(request, response, SESSION_COOKIE, session_cookie(srv, user), srv.cfg.auth.session_hours * 3600)
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current_user)) -> dict:
    return {"id": p.user.id, "email": p.user.email, "name": p.user.name, "role": p.user.role, "org_id": p.user.org_id}


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/auth/password")
def change_password(body: PasswordIn, request: Request, response: Response, p: Principal = Depends(current_user)) -> dict:
    srv = request.app.state.srv
    if problem := password_problems(body.new):
        raise HTTPException(400, problem)
    with srv.db.session() as s:
        user = s.get(User, p.user.id)
        if user.password_hash and not verify_password(body.current, user.password_hash):
            raise HTTPException(401, "Грешна текуща парола.")
        user.password_hash = hash_password(body.new)
        audit(s, p.actor, "auth.password_changed", org_id=p.org_id, ip=client_ip(request))
        s.flush()
        _set_cookie(request, response, SESSION_COOKIE, session_cookie(srv, user), srv.cfg.auth.session_hours * 3600)
    return {"ok": True}


def _redirect_uri(request: Request) -> str:
    return request.app.state.srv.cfg.http.public_url.rstrip("/") + "/auth/oidc/callback"


@router.get("/auth/oidc/login")
def oidc_login(request: Request) -> RedirectResponse:
    srv = request.app.state.srv
    if not srv.cfg.auth.oidc_enabled:
        raise HTTPException(404)
    url, state = oidc.start(srv.cfg.auth, _redirect_uri(request))
    resp = RedirectResponse(url)
    _set_cookie(request, resp, "bix_oidc", sign(srv.cfg.auth.secret_key, state), 600)
    return resp


@router.get("/auth/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    srv = request.app.state.srv
    saved = unsign(srv.cfg.auth.secret_key, request.cookies.get("bix_oidc", ""))
    if error or not saved or saved.get("state") != state or not code:
        return RedirectResponse("/#/login?error=sso")
    try:
        info = oidc.finish(srv.cfg.auth, _redirect_uri(request), code, saved)
    except Exception as e:
        return RedirectResponse(f"/#/login?error={type(e).__name__}")
    ip = client_ip(request)
    with srv.db.session() as s:
        user = s.scalars(select(User).where(User.oidc_sub == info["sub"])).first()
        if not user:
            user = s.scalars(select(User).where(func.lower(User.email) == info["email"])).first()
        if not user:
            org = first_org(s)
            if not org or srv.cfg.auth.oidc_default_role == "none":
                audit(s, f"user:{info['email']}", "auth.sso_denied", ip=ip)
                return RedirectResponse("/#/login?error=noaccess")
            user = User(org_id=org.id, email=info["email"], name=info["name"], role=srv.cfg.auth.oidc_default_role)
            s.add(user)
            audit(s, f"user:{info['email']}", "user.created_sso", org_id=org.id, ip=ip)
        if not user.active:
            return RedirectResponse("/#/login?error=disabled")
        user.oidc_sub = info["sub"]
        user.name = user.name or info["name"]
        user.last_login = time.time()
        s.flush()
        audit(s, f"user:{user.email}", "auth.login_sso", org_id=user.org_id, ip=ip)
        resp = RedirectResponse("/#/")
        _set_cookie(request, resp, SESSION_COOKIE, session_cookie(srv, user), srv.cfg.auth.session_hours * 3600)
        resp.delete_cookie("bix_oidc", path="/")
        return resp
