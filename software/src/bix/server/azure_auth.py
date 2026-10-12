"""Microsoft Entra ID tokens for the server's own Azure / Microsoft 365 calls.

One app registration (or the managed identity of the VM) can serve all of them:
Microsoft Graph (calendar, mail), Azure OpenAI, Azure AI Speech and Blob Storage.
Credentials, in order of preference for a bank:
  managed identity    the server runs on an Azure VM / AKS / Container Apps: no secret at all
  certificate         PEM with the private key and the certificate (client assertion, RS256)
  client secret       simplest; rotate it, keep it in an environment variable
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
import uuid
from typing import Any

import httpx

from bix.common.settings import secret


class EntraError(RuntimeError):
    pass


class EntraToken:
    """Client-credentials tokens for one scope, cached until shortly before they expire."""

    def __init__(self, cfg: Any, scope: str) -> None:
        self.cfg = cfg  # AzureCfg-like: tenant_id, client_id, client_secret(_env), certificate_path, managed_identity(_client_id)
        self.scope = scope
        self._tok: tuple[str, float] | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        c = self.cfg
        return bool(c.managed_identity or (c.tenant_id and c.client_id and (c.certificate_path or secret(c.client_secret, c.client_secret_env))))

    def get(self) -> str:
        with self._lock:
            if self._tok and self._tok[1] > time.time() + 120:
                return self._tok[0]
            tok, ttl = self._managed() if self.cfg.managed_identity else self._client_credentials()
            self._tok = (tok, time.time() + ttl)
            return tok

    def _client_credentials(self) -> tuple[str, int]:
        c = self.cfg
        if not (c.tenant_id and c.client_id):
            raise EntraError("Не са зададени tenant_id и client_id на приложението в Entra ID.")
        url = f"{c.authority.rstrip('/')}/{c.tenant_id}/oauth2/v2.0/token"
        data = {"grant_type": "client_credentials", "client_id": c.client_id, "scope": self.scope}
        if c.certificate_path:
            data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            data["client_assertion"] = client_assertion(c.certificate_path, c.client_id, url, secret(c.certificate_password, c.certificate_password_env))
        else:
            sec = secret(c.client_secret, c.client_secret_env)
            if not sec:
                raise EntraError(f"Няма тайна или сертификат за приложението в Entra ID ({c.client_secret_env}).")
            data["client_secret"] = sec
        try:
            r = httpx.post(url, data=data, timeout=20)
        except httpx.HTTPError as e:
            raise EntraError(f"Microsoft Entra ID не отговаря ({type(e).__name__}); проверете прокси и защитна стена.") from e
        if r.status_code >= 400:
            try:
                err = r.json().get("error_description", r.text)
            except ValueError:
                err = r.text
            raise EntraError(f"Microsoft Entra ID отказа: {err[:300]}")
        d = r.json()
        return d["access_token"], int(d.get("expires_in", 3600))

    def _managed(self) -> tuple[str, int]:
        """Managed identity: App Service / Container Apps give IDENTITY_ENDPOINT, a VM has IMDS."""
        resource = self.scope.removesuffix("/.default")
        params = {"resource": resource}
        if self.cfg.managed_identity_client_id:
            params["client_id"] = self.cfg.managed_identity_client_id
        try:
            if os.environ.get("IDENTITY_ENDPOINT"):
                r = httpx.get(os.environ["IDENTITY_ENDPOINT"], params={**params, "api-version": "2019-08-01"},
                              headers={"X-IDENTITY-HEADER": os.environ.get("IDENTITY_HEADER", "")}, timeout=10)
            else:
                r = httpx.get("http://169.254.169.254/metadata/identity/oauth2/token", params={**params, "api-version": "2018-02-01"},
                              headers={"Metadata": "true"}, timeout=10, trust_env=False)
        except httpx.HTTPError as e:
            raise EntraError("Няма managed identity на тази машина (IMDS не отговаря).") from e
        if r.status_code >= 400:
            raise EntraError(f"Managed identity: {r.text[:300]}")
        d = r.json()
        return d["access_token"], int(d.get("expires_in", 3600))


def client_assertion(pem_path: str, client_id: str, audience: str, password: str = "") -> str:
    """A signed JWT that proves the app holds the certificate registered in Entra ID."""
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    try:
        data = open(pem_path, "rb").read()
    except OSError as e:
        raise EntraError(f"Не мога да прочета сертификата {pem_path}: {e}") from e
    try:
        key = serialization.load_pem_private_key(data, password=password.encode() if password else None)
        cert = x509.load_pem_x509_certificate(data)
    except Exception as e:
        raise EntraError("Сертификатът трябва да е PEM файл с частния ключ и сертификата заедно.") from e
    thumb = cert.fingerprint(hashes.SHA1())  # noqa: S324 - "x5t" is defined as the SHA-1 thumbprint
    now = int(time.time())
    claims = {"aud": audience, "iss": client_id, "sub": client_id, "jti": str(uuid.uuid4()), "nbf": now - 60, "iat": now, "exp": now + 600}
    headers = {"x5t": base64.urlsafe_b64encode(thumb).decode().rstrip("="), "x5t#S256": base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA256())).decode().rstrip("=")}
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def thumbprint_hex(pem_path: str) -> str:
    """For the portal/doctor: which certificate is in use (as Azure shows it)."""
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(open(pem_path, "rb").read())
    return hashlib.sha1(cert.public_bytes(_der())).hexdigest().upper()  # noqa: S324


def _der():
    from cryptography.hazmat.primitives.serialization import Encoding

    return Encoding.DER
