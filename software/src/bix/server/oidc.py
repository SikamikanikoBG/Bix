"""OpenID Connect single sign-on (authorization code flow with PKCE).

Works with Microsoft Entra ID (issuer https://login.microsoftonline.com/<tenant>/v2.0),
Google Workspace (https://accounts.google.com), Okta, Keycloak and others.
Redirect URI to register: <public_url>/auth/oidc/callback
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from bix.common.settings import secret
from bix.server.config import AuthCfg


class OidcError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def discovery(issuer: str) -> dict[str, Any]:
    r = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10)
    r.raise_for_status()
    return r.json()


@lru_cache(maxsize=4)
def _jwks(uri: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(uri, cache_keys=True)


def start(cfg: AuthCfg, redirect_uri: str) -> tuple[str, dict[str, Any]]:
    """Return (authorization URL, state to keep in a signed cookie)."""
    d = discovery(cfg.oidc_issuer)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {
        "client_id": cfg.oidc_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{d['authorization_endpoint']}?{urlencode(params)}", {"state": state, "nonce": nonce, "verifier": verifier, "exp": time.time() + 600}


def finish(cfg: AuthCfg, redirect_uri: str, code: str, saved: dict[str, Any]) -> dict[str, Any]:
    d = discovery(cfg.oidc_issuer)
    r = httpx.post(
        d["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": cfg.oidc_client_id,
            "client_secret": secret(cfg.oidc_client_secret, cfg.oidc_client_secret_env),
            "code_verifier": saved["verifier"],
        },
        timeout=15,
    )
    if r.status_code >= 400:
        raise OidcError(f"Обменът на кода не успя: {r.text[:200]}")
    id_token = r.json().get("id_token")
    if not id_token:
        raise OidcError("Липсва id_token.")
    key = _jwks(d["jwks_uri"]).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256", "RS384", "RS512", "ES256", "PS256"],
        audience=cfg.oidc_client_id,
        issuer=d["issuer"],
        options={"require": ["exp", "iat", "sub"]},
        leeway=60,
    )
    if claims.get("nonce") != saved["nonce"]:
        raise OidcError("Невалиден nonce.")
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").lower()
    if not email or "@" not in email:
        raise OidcError("Доставчикът не върна имейл.")
    if cfg.oidc_allowed_domains and email.split("@", 1)[1] not in [x.lower() for x in cfg.oidc_allowed_domains]:
        raise OidcError("Този домейн няма достъп.")
    return {"sub": f"{d['issuer']}|{claims['sub']}", "email": email, "name": claims.get("name", "")}
