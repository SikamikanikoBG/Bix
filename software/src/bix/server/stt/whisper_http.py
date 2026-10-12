"""Self-hosted Whisper behind the whisper-asr-webservice HTTP API.

For a private-cloud deployment with its own GPU server.
https://github.com/ahmetoner/whisper-asr-webservice
"""

from __future__ import annotations

import httpx
import numpy as np

from bix.common.audio import encode_flac
from bix.server.config import WhisperHttpCfg
from bix.server.stt.base import Segment, SttError, Word, fix_caps, fix_caps_words


class WhisperHttpTranscriber:
    name = "whisper_http"
    diarizes = False

    def __init__(self, cfg: WhisperHttpCfg) -> None:
        if not cfg.url:
            raise SttError("Не е зададен адрес на Whisper сървъра (stt.whisper_http.url).")
        self.cfg = cfg

    def check(self) -> str:
        try:
            r = httpx.get(self.cfg.url.rstrip("/") + "/docs", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SttError(f"Whisper сървърът не отговаря ({self.cfg.url}).") from e
        return self.cfg.url

    def transcribe(self, audio: np.ndarray, language: str, hints: list[str] | None = None) -> list[Segment]:
        params = {"task": "transcribe", "language": language, "output": "json", "word_timestamps": "true", "vad_filter": "true", "encode": "true"}
        if hints:
            # Whisper's prompt biases spelling of names and terms.
            params["initial_prompt"] = ", ".join(sorted(set(hints)))[:600]
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        try:
            r = httpx.post(
                self.cfg.url.rstrip("/") + "/asr",
                params=params,
                files={"audio_file": ("audio.flac", encode_flac(audio), "audio/flac")},
                headers=headers,
                timeout=httpx.Timeout(connect=10, read=self.cfg.timeout_s, write=300, pool=10),
            )
        except httpx.HTTPError as e:
            raise SttError(f"Whisper сървърът не отговаря ({self.cfg.url}).") from e
        if r.status_code >= 400:
            raise SttError(f"Whisper {r.status_code}: {r.text[:300]}")
        out = []
        for s in r.json().get("segments", []):
            if _hallucination(s):
                continue
            words = [Word(w["start"], w["end"], w["word"].strip()) for w in (s.get("words") or [])]
            words = fix_caps_words(s["text"], words)
            out.append(Segment(float(s["start"]), float(s["end"]), fix_caps(s["text"].strip()), confidence=_conf(s), words=words))
        return [s for s in out if s.text]


def _conf(s: dict) -> float | None:
    lp = s.get("avg_logprob")
    return round(float(np.exp(lp)), 3) if lp is not None else None


def _hallucination(s: dict) -> bool:
    """Whisper's usual failure modes on silence and noise, by its own scores."""
    nsp = s.get("no_speech_prob", 0.0)
    lp = s.get("avg_logprob", 0.0)
    cr = s.get("compression_ratio", 1.0)
    return (nsp > 0.6 and lp < -0.7) or cr > 2.6 or lp < -1.5
