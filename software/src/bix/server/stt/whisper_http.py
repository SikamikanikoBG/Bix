"""Self-hosted Whisper behind the whisper-asr-webservice HTTP API.

For a private-cloud deployment with its own GPU server.
https://github.com/ahmetoner/whisper-asr-webservice
"""

from __future__ import annotations

import httpx
import numpy as np

from bix.common.audio import SR, encode_flac
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
        out = []
        for offset, piece in _pieces(audio, self.cfg.chunk_s):
            for seg in self._transcribe_one(piece, language, hints):
                seg.start += offset
                seg.end += offset
                for w in seg.words:
                    w.start += offset
                    w.end += offset
                out.append(seg)
        return out

    def _transcribe_one(self, audio: np.ndarray, language: str, hints: list[str] | None) -> list[Segment]:
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


def _pieces(audio: np.ndarray, chunk_s: int) -> list[tuple[float, np.ndarray]]:
    """Long audio as pieces of at most chunk_s, each cut at the quietest moment of
    its last 30 s so no word is torn in two. Returns (offset in seconds, piece)."""
    size = int(chunk_s * SR)
    if chunk_s <= 0 or len(audio) <= size:
        return [(0.0, audio)]
    frame = SR // 10
    out, start = [], 0
    while len(audio) - start > size:
        lo = start + max(frame, size - 30 * SR)
        window = audio[lo : start + size].astype(np.float32)
        n = len(window) // frame
        energy = np.sqrt(np.mean(window[: n * frame].reshape(n, frame) ** 2, axis=1))
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        out.append((start / SR, audio[start:cut]))
        start = cut
    out.append((start / SR, audio[start:]))
    return out


def _conf(s: dict) -> float | None:
    lp = s.get("avg_logprob")
    return round(float(np.exp(lp)), 3) if lp is not None else None


def _hallucination(s: dict) -> bool:
    """Whisper's usual failure modes on silence and noise, by its own scores."""
    nsp = s.get("no_speech_prob", 0.0)
    lp = s.get("avg_logprob", 0.0)
    cr = s.get("compression_ratio", 1.0)
    return (nsp > 0.6 and lp < -0.7) or cr > 2.6 or lp < -1.5
