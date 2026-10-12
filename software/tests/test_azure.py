"""Azure: Entra ID tokens (secret, certificate, managed identity), Azure OpenAI for the
minutes and questions, Azure Speech with Entra ID, Blob Storage, Graph through [azure]."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import jwt
import pytest

from bix.server import minutes
from bix.server.azure_auth import EntraError, EntraToken, client_assertion
from bix.server.config import AzureCfg, GraphCfg, LlmCfg, ServerConfig


class Resp:
    def __init__(self, status: int, data: dict | None = None, text: str = "", headers: dict | None = None) -> None:
        self.status_code, self._data, self.headers = status, data, headers or {}
        self.text = text or json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


def _pem(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bix-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1) \
        .not_valid_before(now).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256())
    p = tmp_path / "bix.pem"
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                  + cert.public_bytes(serialization.Encoding.PEM))
    return p, key


def test_entra_certificate_assertion_and_caching(tmp_path, monkeypatch):
    pem, key = _pem(tmp_path)
    url = "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
    a = client_assertion(str(pem), "app-id", url)
    claims = jwt.decode(a, key.public_key(), algorithms=["RS256"], audience=url)
    assert claims["iss"] == claims["sub"] == "app-id"
    assert jwt.get_unverified_header(a)["x5t"]

    calls = []

    def fake_post(u, data=None, timeout=None):
        calls.append(data)
        return Resp(200, {"access_token": "T1", "expires_in": 3600})

    monkeypatch.setattr(httpx, "post", fake_post)
    tok = EntraToken(AzureCfg(tenant_id="tenant", client_id="app-id", certificate_path=str(pem)), "https://graph.microsoft.com/.default")
    assert tok.get() == "T1" and tok.get() == "T1"
    assert len(calls) == 1  # cached
    assert calls[0]["client_assertion_type"].endswith("jwt-bearer") and "client_secret" not in calls[0]

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(401, {"error_description": "AADSTS7000215: Invalid client secret provided."}))
    bad = EntraToken(AzureCfg(tenant_id="t", client_id="c", client_secret="wrong"), "x/.default")
    with pytest.raises(EntraError, match="AADSTS7000215"):
        bad.get()
    with pytest.raises(EntraError, match="тайна или сертификат"):
        EntraToken(AzureCfg(tenant_id="t", client_id="c", client_secret_env="BIX_NOPE"), "x/.default").get()


def test_managed_identity_uses_imds(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None, trust_env=True):
        seen.update(url=url, params=params, headers=headers, trust_env=trust_env)
        return Resp(200, {"access_token": "MI", "expires_in": "3599"})

    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.setattr(httpx, "get", fake_get)
    tok = EntraToken(AzureCfg(managed_identity=True, managed_identity_client_id="uai"), "https://cognitiveservices.azure.com/.default")
    assert tok.get() == "MI"
    assert seen["url"].startswith("http://169.254.169.254/") and seen["headers"] == {"Metadata": "true"}
    assert seen["params"]["resource"] == "https://cognitiveservices.azure.com" and seen["params"]["client_id"] == "uai"
    assert seen["trust_env"] is False  # never through the corporate proxy


def test_azure_openai_minutes_and_errors(monkeypatch):
    cfg = LlmCfg(provider="azure_openai", azure_endpoint="https://bank-ai.openai.azure.com/", azure_deployment="gpt-4.1", azure_api_key="k")
    sent = []
    content = json.dumps({**minutes.empty_minutes("Обобщение."), "title": "Преглед", "decisions": [{"text": "Да", "refs": [1]}]})

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return Resp(200, {"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = minutes.generate(cfg, [{"start": 0, "end": 2, "text": "Решаваме да", "speaker": "S1"}], {}, {}, "bg")
    url, body, headers = sent[0]
    assert url == "https://bank-ai.openai.azure.com/openai/deployments/gpt-4.1/chat/completions?api-version=2024-10-21"
    assert headers == {"api-key": "k"}
    assert body["response_format"]["json_schema"]["strict"] is True and "max_completion_tokens" in body and "temperature" not in body
    assert out["title"] == "Преглед" and out["decisions"][0]["text"] == "Да"

    # the new v1 API: model = deployment, no api-version
    cfg.azure_api_version = "v1"
    minutes.generate(cfg, [{"start": 0, "end": 2, "text": "x", "speaker": "S1"}], {}, {}, "bg")
    assert sent[-1][0] == "https://bank-ai.openai.azure.com/openai/v1/chat/completions" and sent[-1][1]["model"] == "gpt-4.1"

    # a quick question asks for low reasoning; a GPT-4.x deployment refuses that once, then it is dropped
    cfg.azure_api_version = "2024-10-21"
    replies = [Resp(400, {"error": {"code": "unsupported_parameter", "message": "Unsupported parameter: 'reasoning_effort'"}}),
               Resp(200, {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"kind": "answer", "answer": "Нищо.", "points": [], "note": None, "owner": None, "due": None})}}]})]
    bodies = []
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: (bodies.append(dict(json)), replies.pop(0))[1])
    a = minutes.answer(cfg, "Какво решихме?", [{"start": 1, "text": "x"}], 60, "bg")
    assert a["answer"] == "Нищо." and "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]

    for status, data, msg in [
        (401, {"error": {"message": "x"}}, "Cognitive Services OpenAI User"),
        (404, {"error": {"code": "DeploymentNotFound"}}, "няма deployment"),
        (400, {"error": {"code": "content_filter", "message": "filtered"}}, "Филтърът за съдържание"),
    ]:
        monkeypatch.setattr(httpx, "post", lambda *a, _s=status, _d=data, **k: Resp(_s, _d))
        with pytest.raises(minutes.MinutesError, match=msg):
            minutes.structured(cfg, "s", "p")
    with pytest.raises(minutes.MinutesError, match="BIX_NOPE"):
        minutes.structured(LlmCfg(provider="azure_openai", azure_endpoint="https://x", azure_deployment="d", azure_api_key_env="BIX_NOPE"), "s", "p")


def test_azure_openai_with_entra(monkeypatch):
    minutes.use_azure_identity(AzureCfg(tenant_id="t", client_id="c", client_secret="s"))
    ok = json.dumps({"kind": "answer", "answer": "ok", "points": [], "note": None, "owner": None, "due": None})
    monkeypatch.setattr(httpx, "post", lambda url, data=None, json=None, headers=None, timeout=None:
                        Resp(200, {"access_token": "AAD", "expires_in": 3600}) if data else
                        Resp(200, {"choices": [{"finish_reason": "stop", "message": {"content": ok}}]}))
    seen = {}
    real = minutes.azure_openai_request

    def spy(cfg, path):
        url, h = real(cfg, path)
        seen["h"] = h
        return url, h

    monkeypatch.setattr(minutes, "azure_openai_request", spy)
    cfg = LlmCfg(provider="azure_openai", azure_endpoint="https://x.openai.azure.com", azure_deployment="d", azure_auth="entra")
    assert minutes.answer(cfg, "?", [], 1, "bg")["answer"] == "ok"
    assert seen["h"] == {"Authorization": "Bearer AAD"}


def test_azure_speech_with_entra(monkeypatch):
    from bix.server.config import AzureSpeechCfg
    from bix.server.stt import azure as az
    from bix.server.stt.base import SttError

    az._tokens.clear()
    with pytest.raises(SttError, match="собствен адрес"):
        az.AzureFastTranscriber(AzureSpeechCfg(auth="entra"), AzureCfg()).check()
    monkeypatch.setattr(httpx, "post", lambda url, data=None, timeout=None, **k: Resp(200, {"access_token": "SPEECH", "expires_in": 3600}))
    t = az.AzureFastTranscriber(AzureSpeechCfg(auth="entra", endpoint="https://bank-speech.cognitiveservices.azure.com"), AzureCfg(tenant_id="t", client_id="c", client_secret="s"))
    assert t.check().endswith("(Entra ID)")
    assert t._headers() == {"Authorization": "Bearer SPEECH"}
    assert t.url.startswith("https://bank-speech.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe")


def test_graph_uses_the_shared_azure_app():
    from bix.server.integrations import Graph

    g = Graph(GraphCfg(enabled=True, sender_mailbox="bix@bank.bg"), AzureCfg(tenant_id="t", client_id="c", certificate_path="/etc/bix/bix.pem"))
    assert g.enabled and g.auth.client_id == "c" and g.auth.certificate_path == "/etc/bix/bix.pem"
    own = Graph(GraphCfg(enabled=True, tenant_id="t2", client_id="c2"), AzureCfg(tenant_id="t", client_id="c"))
    assert own.auth.client_id == "c2"
    assert not Graph(GraphCfg(enabled=True), AzureCfg()).enabled


def test_blob_storage_from_connection_string(monkeypatch):
    from bix.server import storage

    monkeypatch.setenv("BIX_AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    cfg = ServerConfig()
    cfg.storage.backend = "azure_blob"
    st = storage.make(cfg)
    assert isinstance(st, storage.AzureBlobStorage)
    assert st._k("orgs/o/meetings/m/audio.flac") == "bix/orgs/o/meetings/m/audio.flac"
    with pytest.raises(ValueError):
        st._k("../etc/passwd")
    monkeypatch.delenv("BIX_AZURE_STORAGE_CONNECTION_STRING")
    with pytest.raises(RuntimeError, match="azure_account_url"):
        storage.make(cfg)
