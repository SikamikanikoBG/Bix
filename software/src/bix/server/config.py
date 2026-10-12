"""Server (cloud) configuration.

Deployment-level settings and secrets live here (TOML file or BIX_* env vars,
12-factor style for containers). Organisation-level policy that admins change
day to day (retention, language, live mode, recipients) lives in the database
and is edited in the portal.
"""

from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass, field
from pathlib import Path

from bix.common import settings


@dataclass
class HttpCfg:
    host: str = "127.0.0.1"
    port: int = 8710
    # Публичният адрес на портала (за линкове в имейлите и OIDC redirect).
    public_url: str = "http://127.0.0.1:8710"
    # Зад обратно прокси с mTLS: заглавката с SHA-256 отпечатъка на клиентския
    # сертификат на устройството (напр. X-Client-Cert-Sha256). Празно = изключено.
    mtls_fingerprint_header: str = ""
    trusted_proxies: list[str] = field(default_factory=lambda: ["127.0.0.1"])
    cookie_secure: bool = False


@dataclass
class DatabaseCfg:
    # sqlite:///path или postgresql+psycopg://user:pass@host/db
    url: str = ""


@dataclass
class StorageCfg:
    # local | s3 | azure_blob
    backend: str = "local"
    local_dir: str = ""
    s3_bucket: str = ""
    s3_prefix: str = "bix/"
    s3_region: str = "eu-central-1"
    # KMS ключ на клиента за SSE-KMS; празно = SSE-S3.
    s3_kms_key_id: str = ""
    s3_endpoint_url: str = ""
    # Azure Blob Storage: https://<account>.blob.core.windows.net + контейнер. Вход: managed identity /
    # приложението от [azure], или connection string (ключ на акаунта) в променлива на средата.
    azure_account_url: str = ""
    azure_container: str = "bix"
    azure_prefix: str = "bix/"
    azure_connection_string_env: str = "BIX_AZURE_STORAGE_CONNECTION_STRING"


@dataclass
class AuthCfg:
    # Ключ за подписване на сесиите. Генерира се при първо пускане.
    secret_key: str = ""
    session_hours: int = 12
    # OIDC (Microsoft Entra ID, Google Workspace, Okta, Keycloak ...)
    oidc_enabled: bool = False
    oidc_issuer: str = ""  # https://login.microsoftonline.com/<tenant>/v2.0
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_client_secret_env: str = "BIX_OIDC_CLIENT_SECRET"
    # Нови потребители през SSO получават тази роля: member | none
    oidc_default_role: str = "member"
    # Ако е зададено: само имейли от тези домейни могат да влязат.
    oidc_allowed_domains: list[str] = field(default_factory=list)
    local_login: bool = True


@dataclass
class AzureSpeechCfg:
    # Ресурсът трябва да е в регион на ЕС (westeurope, germanywestcentral ...).
    # endpoint има предимство: https://<resource>.cognitiveservices.azure.com (задължителен за вход с Entra ID
    # и за private endpoint).
    endpoint: str = ""
    region: str = "westeurope"
    # key = с ключ на ресурса; entra = с приложението / managed identity от [azure] (без ключове)
    auth: str = "key"
    key: str = ""
    key_env: str = "AZURE_SPEECH_KEY"
    api_version: str = "2025-10-15"
    max_speakers: int = 10


@dataclass
class WhisperHttpCfg:
    # Собствен (частен облак) сървър с whisper-asr-webservice API.
    url: str = ""
    timeout_s: int = 1800
    api_key: str = ""


@dataclass
class FasterWhisperCfg:
    model: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "int8"


@dataclass
class SttCfg:
    # azure | whisper_http | faster_whisper
    provider: str = "faster_whisper"
    # Резервен доставчик, ако основният не отговаря (празно = без резервен).
    fallback_provider: str = ""
    azure: AzureSpeechCfg = field(default_factory=AzureSpeechCfg)
    whisper_http: WhisperHttpCfg = field(default_factory=WhisperHttpCfg)
    faster_whisper: FasterWhisperCfg = field(default_factory=FasterWhisperCfg)


@dataclass
class DiarizationCfg:
    # sherpa | provider (ако доставчикът на реч сам разделя говорещите) | none
    provider: str = "sherpa"
    embedding_model: str = "nemo_en_titanet_small.onnx"
    threshold: float = 0.75
    # absolute minimum; the real floor is min_share of the meeting (longer meetings: more)
    min_speaker_seconds: float = 3.0
    # Clusters this similar are the same voice (far microphone, echo). Lower = fewer speakers.
    merge_threshold: float = 0.75
    # A "speaker" with less than this share of the meeting's speech is folded into the closest voice.
    min_share: float = 0.025
    profile_match: float = 0.55
    num_threads: int = 4


@dataclass
class LlmCfg:
    # anthropic | bedrock | azure_openai | vllm | claude_cli | none
    provider: str = "anthropic"
    fallback_provider: str = ""
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    api_key: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    bedrock_region: str = "eu-central-1"
    claude_cli_path: str = "claude"
    # Собствен езиков модел в частния облак: vLLM или друг OpenAI-съвместим сървър.
    vllm_url: str = ""  # напр. http://gpu-server:8000/v1
    vllm_model: str = ""  # празно = първият модел от /v1/models
    vllm_api_key: str = ""
    vllm_api_key_env: str = "BIX_VLLM_API_KEY"
    vllm_thinking: bool = False
    vllm_timeout_s: int = 900
    # Azure OpenAI (Azure AI Foundry): https://<resource>.openai.azure.com, името на deployment-а (напр. gpt-4.1).
    azure_endpoint: str = ""
    azure_deployment: str = ""
    # GA версия със structured outputs; "v1" = новото API без версия (/openai/v1/...).
    azure_api_version: str = "2024-10-21"
    # key = с ключ на ресурса; entra = с приложението / managed identity от [azure]
    azure_auth: str = "key"
    azure_api_key: str = ""
    azure_api_key_env: str = "AZURE_OPENAI_API_KEY"
    azure_timeout_s: int = 600


@dataclass
class SmtpCfg:
    """SMTP: вътрешен relay на банката (обикновено порт 25, без парола, по IP) или сървър с вход."""

    host: str = ""
    port: int = 587
    # starttls на 587/25; порт 465 = TLS от самото начало
    starttls: bool = True
    username: str = ""  # празно = relay без вход
    password: str = ""
    password_env: str = "BIX_SMTP_PASSWORD"
    sender: str = ""  # bix@banka.bg
    sender_name: str = "Bix"
    reply_to: str = ""  # напр. ИТ поддръжката; иначе отговорите отиват на sender
    # Вътрешен сертификат на банката (PEM с CA), ако relay-ят е с вътрешна PKI.
    ca_file: str = ""
    # Името, с което Bix се представя (EHLO); някои relay-и проверяват.
    local_hostname: str = ""
    timeout_s: int = 30


@dataclass
class GraphCfg:
    """Microsoft 365: календар на залите и изпращане на минутите. Празните tenant_id/client_id
    означават: същото приложение като в [azure]."""

    enabled: bool = False
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    client_secret_env: str = "BIX_GRAPH_CLIENT_SECRET"
    certificate_path: str = ""
    # Пощенска кутия, от която се пращат минутите (Mail.Send, application), напр. споделена bix@banka.bg.
    sender_mailbox: str = ""
    # Копие в „Изпратени“ на тази кутия (за проследимост).
    save_to_sent: bool = True


@dataclass
class AzureCfg:
    """Приложение в Microsoft Entra ID (или managed identity на машината) за Azure OpenAI,
    Azure AI Speech, Blob Storage и Microsoft Graph, когато те са с вход „entra“."""

    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    client_secret_env: str = "BIX_AZURE_CLIENT_SECRET"
    # PEM с частния ключ и сертификата (за предпочитане пред тайна)
    certificate_path: str = ""
    certificate_password: str = ""
    certificate_password_env: str = "BIX_AZURE_CERT_PASSWORD"
    # Сървърът е в Azure (VM, Container Apps, AKS): без тайни, с managed identity.
    managed_identity: bool = False
    managed_identity_client_id: str = ""  # за user-assigned identity
    authority: str = "https://login.microsoftonline.com"


@dataclass
class WorkerCfg:
    threads: int = 1
    poll_s: float = 2.0
    # Process meetings in a separate process: the speaker models hold Python's
    # GIL for minutes on long meetings, which would freeze the web server.
    isolate: bool = True


@dataclass
class ServerConfig:
    data_dir: str = ""
    # Models somewhere else than <data_dir>/models (the Docker image ships them in /opt/bix/models).
    models_path: str = ""
    http: HttpCfg = field(default_factory=HttpCfg)
    database: DatabaseCfg = field(default_factory=DatabaseCfg)
    storage: StorageCfg = field(default_factory=StorageCfg)
    auth: AuthCfg = field(default_factory=AuthCfg)
    stt: SttCfg = field(default_factory=SttCfg)
    diarization: DiarizationCfg = field(default_factory=DiarizationCfg)
    llm: LlmCfg = field(default_factory=LlmCfg)
    smtp: SmtpCfg = field(default_factory=SmtpCfg)
    graph: GraphCfg = field(default_factory=GraphCfg)
    azure: AzureCfg = field(default_factory=AzureCfg)
    worker: WorkerCfg = field(default_factory=WorkerCfg)

    @property
    def dir(self) -> Path:
        return Path(self.data_dir) if self.data_dir else settings.data_dir("server")

    @property
    def models_dir(self) -> Path:
        return Path(self.models_path) if self.models_path else self.dir / "models"

    @property
    def db_url(self) -> str:
        return self.database.url or f"sqlite:///{(self.dir / 'bix.db').as_posix()}"

    @property
    def storage_dir(self) -> Path:
        return Path(self.storage.local_dir) if self.storage.local_dir else self.dir / "objects"


def config_path(path: str | Path | None = None) -> Path:
    import os

    if path:
        return Path(path)
    if env := os.environ.get("BIX_SERVER_CONFIG"):
        return Path(env)
    return settings.data_dir("server") / "server.toml"


def load(path: str | Path | None = None) -> tuple[ServerConfig, Path]:
    p = config_path(path)
    cfg = settings.load(ServerConfig, p)
    if not cfg.data_dir:
        cfg.data_dir = str(p.parent)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    if not cfg.auth.secret_key:
        # Persist only the generated key: values that came from environment
        # variables (API keys...) must not end up in the file.
        import tomllib

        import tomli_w

        cfg.auth.secret_key = _secrets.token_urlsafe(48)
        data = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data.setdefault("auth", {})["secret_key"] = cfg.auth.secret_key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tomli_w.dumps(data), encoding="utf-8")
        if os.name != "nt":
            os.chmod(p, 0o600)
    return cfg, p
