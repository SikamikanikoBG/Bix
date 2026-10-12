"""Speech-to-text interface shared by all providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import numpy as np


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None  # set by the provider (Azure) or by diarization
    confidence: float | None = None
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not self.words:
            d.pop("words")
        return d


class Transcriber(Protocol):
    name: str
    #: True if segments already carry speaker labels
    diarizes: bool

    def transcribe(self, audio: np.ndarray, language: str, hints: list[str] | None = None) -> list[Segment]:
        """audio: float32 mono 16 kHz. hints: names/terms to bias recognition."""
        ...


class SttError(RuntimeError):
    pass


def locale(language: str) -> str:
    return {"bg": "bg-BG", "en": "en-US", "de": "de-DE", "ro": "ro-RO", "el": "el-GR", "sr": "sr-RS", "mk": "mk-MK"}.get(language, language)


def fix_caps(text: str) -> str:
    """Whisper sometimes gets stuck writing in capitals for a whole stretch.
    Turn such segments into sentence case; leave normal text alone."""
    import re

    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8 or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text
    low = text.lower()
    return re.sub(r"(^|[.!?…]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), low)


def fix_caps_words(text: str, words: list[Word]) -> list[Word]:
    """Same correction for word timestamps (segments are rebuilt from words when
    they are split between speakers): lower-case, capitalise sentence starts."""
    if fix_caps(text) == text:
        return words
    out, start = [], True
    for w in words:
        t = w.text.lower()
        if start and t[:1].isalpha():
            t = t[:1].upper() + t[1:]
        out.append(Word(w.start, w.end, t))
        start = t.rstrip()[-1:] in ".!?…" if t.strip() else start
    return out
