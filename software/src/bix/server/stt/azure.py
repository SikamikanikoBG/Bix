"""Azure AI Speech, fast transcription REST API (bg-BG, diarization built in).

Use a Speech resource in an EU region so audio stays in the EU.
Docs: https://learn.microsoft.com/azure/ai-services/speech-service/fast-transcription-create
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np

from bix.common.audio import encode_flac
from bix.common.settings import secret
from bix.server.config import AzureSpeechCfg
from bix.server.stt.base import Segment, SttError, Word, locale


_tokens: dict[str, Any] = {}  # Entra ID tokens, shared by all transcribers in the process


class AzureFastTranscriber:
    name = "azure"
    diarizes = True

    def __init__(self, cfg: AzureSpeechCfg, azure: Any = None) -> None:
        self.cfg = cfg
        self.azure = azure
        self.key = secret(cfg.key, cfg.key_env)
        base = cfg.endpoint.rstrip("/") if cfg.endpoint else f"https://{cfg.region}.api.cognitive.microsoft.com"
        self.url = f"{base}/speechtotext/transcriptions:transcribe?api-version={cfg.api_version}"

    def check(self) -> str:
        if self.cfg.auth == "entra":
            if not self.cfg.endpoint:
                raise SttError("Вход с Entra ID в Azure Speech иска собствен адрес на ресурса (stt.azure.endpoint).")
            self._headers()
            return f"{self.url} (Entra ID)"
        if not self.key:
            raise SttError(f"Няма ключ за Azure Speech ({self.cfg.key_env}).")
        return self.url

    def _headers(self) -> dict[str, str]:
        if self.cfg.auth != "entra":
            return {"Ocp-Apim-Subscription-Key": self.key}
        from bix.server.azure_auth import EntraToken

        if self.azure is None:
            raise SttError("Вход с Entra ID в Azure Speech: попълнете [azure] в настройките на сървъра.")
        tok = _tokens.setdefault("speech", EntraToken(self.azure, "https://cognitiveservices.azure.com/.default"))
        try:
            return {"Authorization": f"Bearer {tok.get()}"}
        except Exception as e:
            raise SttError(f"Azure Speech, вход с Entra ID: {e}") from e

    def transcribe(self, audio: np.ndarray, language: str, hints: list[str] | None = None, diarize: bool = True) -> list[Segment]:
        self.check()
        definition: dict = {"locales": [locale(language)], "profanityFilterMode": "None"}
        if diarize and len(audio) > 16000 * 5:
            definition["diarization"] = {"enabled": True, "maxSpeakers": max(2, min(35, self.cfg.max_speakers))}
        if hints:
            definition["phraseList"] = {"phrases": sorted(set(h for h in hints if h))[:500]}
        files = {
            "audio": ("audio.flac", encode_flac(audio), "audio/flac"),
            "definition": (None, json.dumps(definition), "application/json"),
        }
        timeout = httpx.Timeout(connect=15, read=max(120, len(audio) / 16000 / 2), write=300, pool=15)
        for attempt in range(4):
            try:
                r = httpx.post(self.url, files=files, headers=self._headers(), timeout=timeout)
            except httpx.HTTPError as e:
                raise SttError(f"Azure Speech не отговаря ({type(e).__name__}); проверете прокси и private endpoint.") from e
            if r.status_code == 429 and attempt < 3:
                import time

                time.sleep(float(r.headers.get("Retry-After", 5 * (attempt + 1))))
                continue
            break
        if r.status_code >= 400:
            raise SttError(f"Azure Speech {r.status_code}: {r.text[:300]}")
        data = r.json()
        out = []
        for p in data.get("phrases", []):
            start = p["offsetMilliseconds"] / 1000
            end = start + p["durationMilliseconds"] / 1000
            words = [
                Word(w["offsetMilliseconds"] / 1000, (w["offsetMilliseconds"] + w["durationMilliseconds"]) / 1000, w["text"])
                for w in p.get("words", [])
            ]
            spk = p.get("speaker")
            out.append(Segment(start, end, p["text"].strip(), speaker=f"A{spk}" if spk is not None else None, confidence=p.get("confidence"), words=words))
        return [s for s in out if s.text]
