"""Microsoft 365 (Graph) calendar lookup and mail delivery, plus SMTP.

Graph uses app-only (client credentials) auth. The Entra ID app needs the
application permissions Calendars.Read (room mailboxes) and Mail.Send; limit
both to the room and sender mailboxes with an Exchange application access
policy.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from types import SimpleNamespace
from typing import Any

import httpx

from bix.common.settings import secret
from bix.server.azure_auth import EntraError, EntraToken
from bix.server.config import AzureCfg, GraphCfg, SmtpCfg

log = logging.getLogger(__name__)


class DeliveryError(RuntimeError):
    pass


class Graph:
    def __init__(self, cfg: GraphCfg, azure: AzureCfg | None = None) -> None:
        self.cfg = cfg
        az = azure or AzureCfg()
        # Graph's own app if it has one, else the shared one from [azure] (or the managed identity)
        own = bool(cfg.tenant_id and cfg.client_id)
        self.auth = SimpleNamespace(
            tenant_id=cfg.tenant_id if own else az.tenant_id,
            client_id=cfg.client_id if own else az.client_id,
            client_secret=cfg.client_secret if own else az.client_secret,
            client_secret_env=cfg.client_secret_env if own else az.client_secret_env,
            certificate_path=cfg.certificate_path if own else az.certificate_path,
            certificate_password=az.certificate_password,
            certificate_password_env=az.certificate_password_env,
            managed_identity=False if own else az.managed_identity,
            managed_identity_client_id=az.managed_identity_client_id,
            authority=az.authority,
        )
        self._entra = EntraToken(self.auth, "https://graph.microsoft.com/.default")

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and (bool(self.auth.tenant_id and self.auth.client_id) or self.auth.managed_identity)

    def token(self) -> str:
        try:
            return self._entra.get()
        except EntraError as e:
            raise DeliveryError(str(e)) from e

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        r = httpx.get(f"https://graph.microsoft.com/v1.0{path}", params=params, headers={"Authorization": f"Bearer {self.token()}"}, timeout=20)
        if r.status_code >= 400:
            raise DeliveryError(f"Graph {r.status_code}: {r.text[:300]}")
        return r.json()

    def events(self, mailbox: str, start: float, end: float) -> list[dict[str, Any]]:
        """Events in a room mailbox between start and end, in the calendar module's format."""
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        data = self._get(
            f"/users/{mailbox}/calendarView",
            startDateTime=iso(start),
            endDateTime=iso(end),
            **{"$select": "iCalUId,subject,organizer,attendees,start,end,isCancelled", "$top": "50", "$orderby": "start/dateTime"},
        )
        out = []
        for ev in data.get("value", []):
            if ev.get("isCancelled"):
                continue
            org = ev.get("organizer", {}).get("emailAddress", {})
            attendees = [
                {"name": a["emailAddress"].get("name", ""), "email": a["emailAddress"].get("address", "")}
                for a in ev.get("attendees", [])
                if a.get("type") != "resource" and a.get("emailAddress", {}).get("address", "").lower() != mailbox.lower()
            ]
            out.append({
                "uid": "m365:" + (ev.get("iCalUId") or ev.get("id", "")) + f":{_parse_graph_time(ev['start']):.0f}",
                "title": ev.get("subject", ""),
                "start": _parse_graph_time(ev["start"]),
                "end": _parse_graph_time(ev["end"]),
                "organizer": {"name": org.get("name", ""), "email": org.get("address", "")},
                "attendees": attendees,
                "source": "m365",
            })
        return out

    def send_mail(self, to: list[str], subject: str, html: str, attachments: list[tuple[str, bytes, str]] | None = None) -> None:
        import base64

        if not self.cfg.sender_mailbox:
            raise DeliveryError("Не е зададена пощенска кутия за изпращане (graph.sender_mailbox).")
        msg: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            # an automatic message: no out-of-office replies back to the Bix mailbox
            "internetMessageHeaders": [{"name": "X-Auto-Response-Suppress", "value": "All"}, {"name": "X-Bix", "value": "minutes"}],
        }
        if attachments:
            msg["attachments"] = [
                {"@odata.type": "#microsoft.graph.fileAttachment", "name": n, "contentType": ct, "contentBytes": base64.b64encode(b).decode()}
                for n, b, ct in attachments
            ]
        r = httpx.post(
            f"https://graph.microsoft.com/v1.0/users/{self.cfg.sender_mailbox}/sendMail",
            json={"message": msg, "saveToSentItems": self.cfg.save_to_sent},
            headers={"Authorization": f"Bearer {self.token()}"},
            timeout=30,
        )
        if r.status_code >= 400:
            raise DeliveryError(f"Graph sendMail {r.status_code}: {r.text[:300]}")


def _parse_graph_time(t: dict[str, str]) -> float:
    # Graph returns UTC when no Prefer: outlook.timezone header is sent.
    s = t["dateTime"].split(".")[0]
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def smtp_send(cfg: SmtpCfg, to: list[str], subject: str, html: str, text: str, attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    if not cfg.host:
        raise DeliveryError("Не е настроен SMTP сървър (smtp.host).")
    sender = cfg.sender or cfg.username
    if not sender:
        raise DeliveryError("Не е зададен подател (smtp.sender).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.sender_name, sender)) if cfg.sender_name else sender
    msg["To"] = ", ".join(to)
    if cfg.reply_to:
        msg["Reply-To"] = cfg.reply_to
    # Mail gateways reject or quarantine messages without these; smtplib does not add them.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    # An automatic message (RFC 3834): no out-of-office replies, no auto-forward loops.
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for name, data, ctype in attachments or []:
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    try:
        _smtp_deliver(cfg, msg)
    except smtplib.SMTPAuthenticationError as e:
        raise DeliveryError("Пощенският сървър отказа потребителя/паролата (smtp).") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise DeliveryError("Пощенският сървър отказа получателите: " + ", ".join(e.recipients)) from e
    except smtplib.SMTPSenderRefused as e:
        raise DeliveryError(f"Пощенският сървър отказа подателя {sender}: {_smtp_text(e)} (разрешен ли е relay от този сървър?)") from e
    except smtplib.SMTPNotSupportedError as e:
        raise DeliveryError("Пощенският сървър не поддържа STARTTLS: задайте smtp.starttls = false само ако relay-ят е във вътрешната мрежа.") from e
    except ssl.SSLCertVerificationError as e:
        raise DeliveryError(f"Сертификатът на пощенския сървър не е доверен: задайте smtp.ca_file с вътрешния CA ({e.verify_message}).") from e
    except smtplib.SMTPResponseException as e:
        raise DeliveryError(f"Пощенският сървър върна {e.smtp_code}: {_smtp_text(e)}") from e
    except (OSError, smtplib.SMTPException) as e:
        raise DeliveryError(f"Пощенският сървър не отговаря ({cfg.host}:{cfg.port}): {type(e).__name__}.") from e


def _smtp_text(e: smtplib.SMTPResponseException) -> str:
    err = e.smtp_error
    return (err.decode(errors="replace") if isinstance(err, bytes) else str(err))[:200]


def _smtp_deliver(cfg: SmtpCfg, msg: EmailMessage) -> None:
    ctx = ssl.create_default_context(cafile=cfg.ca_file or None)
    password = secret(cfg.password, cfg.password_env)
    host = cfg.local_hostname or None
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, local_hostname=host, context=ctx, timeout=cfg.timeout_s) as s:
            if cfg.username:
                s.login(cfg.username, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg.host, cfg.port, local_hostname=host, timeout=cfg.timeout_s) as s:
            s.ehlo()
            if cfg.starttls:
                s.starttls(context=ctx)
                s.ehlo()
            if cfg.username:
                s.login(cfg.username, password)
            s.send_message(msg)
