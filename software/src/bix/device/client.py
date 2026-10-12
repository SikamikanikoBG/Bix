"""HTTPS client for the Bix server's device API."""

from __future__ import annotations

import platform
from pathlib import Path

import httpx

from bix import __version__
from bix.device.config import DeviceConfig


class NotEnrolled(RuntimeError):
    pass


class ServerClient:
    def __init__(self, cfg: DeviceConfig) -> None:
        self.cfg = cfg
        creds = cfg.credentials()
        self.base = (creds.get("server_url") or cfg.server_url).rstrip("/")
        self.token = creds.get("token", "")
        self.device_id = creds.get("device_id", "")
        verify: bool | str = cfg.ca_bundle or True
        cert = (cfg.client_cert, cfg.client_key) if cfg.client_cert else None
        self.http = httpx.Client(
            verify=verify, cert=cert, timeout=httpx.Timeout(20, connect=10), headers={"User-Agent": f"bix-device/{__version__}"}
        )

    @property
    def enrolled(self) -> bool:
        return bool(self.base and self.token)

    def _auth(self) -> dict[str, str]:
        if not self.enrolled:
            raise NotEnrolled("Устройството не е регистрирано. Изпълнете: bix device enroll --server URL --code КОД")
        return {"Authorization": f"Bearer {self.token}"}

    def enroll(self, server_url: str, code: str) -> dict:
        r = self.http.post(
            server_url.rstrip("/") + "/api/device/enroll",
            json={"code": code, "serial": self.cfg.serial_number(), "name": self.cfg.name, "platform": platform_string(), "fw_version": __version__},
        )
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
        data = r.json()
        self.cfg.save_credentials({"server_url": server_url.rstrip("/"), "device_id": data["device_id"], "token": data["token"]})
        self.base, self.token, self.device_id = server_url.rstrip("/"), data["token"], data["device_id"]
        return data

    def heartbeat(self, payload: dict) -> dict:
        r = self.http.post(self.base + "/api/device/heartbeat", json=payload, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def start_meeting(self, mid: str, started_at: float, title: str = "", calendar_uid: str = "") -> dict:
        body = {"id": mid, "started_at": started_at, "title": title, "calendar_uid": calendar_uid}
        r = self.http.post(self.base + "/api/device/meetings", json=body, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def upload_chunk(self, mid: str, seq: int, start_s: float, duration_s: float, path: Path) -> None:
        r = self.http.put(
            f"{self.base}/api/device/meetings/{mid}/chunks/{seq}",
            params={"start_s": start_s, "duration_s": duration_s},
            content=path.read_bytes(),
            headers={**self._auth(), "Content-Type": "audio/flac"},
            timeout=httpx.Timeout(120, connect=10),
        )
        r.raise_for_status()

    def end_meeting(self, mid: str, ended_at: float, chunks: int, duration_s: float, notes: list[dict] | None = None) -> dict:
        body = {"ended_at": ended_at, "chunks": chunks, "duration_s": duration_s, "notes": notes or []}
        r = self.http.post(f"{self.base}/api/device/meetings/{mid}/end", json=body, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def discard(self, mid: str) -> None:
        r = self.http.post(f"{self.base}/api/device/meetings/{mid}/discard", headers=self._auth())
        if r.status_code not in (200, 404):
            r.raise_for_status()

    def ask(self, mid: str, preset: str = "", question: str = "", audio: bytes | None = None) -> dict:
        """A question about the meeting so far. Answers take a few seconds (speech + language model)."""
        t = httpx.Timeout(120, connect=10)
        if audio is not None:
            r = self.http.post(f"{self.base}/api/device/meetings/{mid}/ask-voice", content=audio, headers={**self._auth(), "Content-Type": "audio/flac"}, timeout=t)
        else:
            r = self.http.post(f"{self.base}/api/device/meetings/{mid}/ask", json={"preset": preset, "question": question}, headers=self._auth(), timeout=t)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise RuntimeError(detail if isinstance(detail, str) else f"Сървърът върна {r.status_code}.")
        return r.json()

    def live(self, mid: str, after: int) -> list[dict]:
        r = self.http.get(f"{self.base}/api/device/meetings/{mid}/live", params={"after": after}, headers=self._auth(), timeout=10)
        r.raise_for_status()
        return r.json()["segments"]


def platform_string() -> str:
    model = ""
    try:
        model = Path("/proc/device-tree/model").read_text().strip("\x00 \n")
    except OSError:
        pass
    return f"{model or platform.system()} {platform.release()} {platform.machine()}".strip()
