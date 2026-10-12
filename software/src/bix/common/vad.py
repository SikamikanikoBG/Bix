"""Voice activity detection: Silero (via sherpa-onnx) with an energy fallback.

Streaming use: feed() 20 ms frames, get back whether speech is going on and
any finished utterances (for live transcription). Offline use: speech_regions().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Utterance:
    start_s: float
    samples: np.ndarray

    @property
    def end_s(self) -> float:
        return self.start_s + len(self.samples) / 16000


class Vad:
    """Streaming VAD at 16 kHz."""

    def __init__(self, model: Path | None, energy_threshold_db: float = -48.0, max_utterance_s: float = 20.0) -> None:
        self.sr = 16000
        self.threshold_db = energy_threshold_db
        self.max_utt = max_utterance_s
        self._silero = None
        if model and model.exists():
            try:
                import sherpa_onnx as so

                c = so.VadModelConfig()
                c.silero_vad.model = str(model)
                c.silero_vad.threshold = 0.5
                c.silero_vad.min_silence_duration = 0.5
                c.silero_vad.min_speech_duration = 0.25
                c.silero_vad.max_speech_duration = max_utterance_s
                c.sample_rate = self.sr
                c.num_threads = 1
                self._silero = so.VoiceActivityDetector(c, buffer_size_in_seconds=max_utterance_s * 3)
            except Exception:
                log.exception("Silero VAD unavailable, using energy VAD")
        # energy fallback state
        self._buf: list[np.ndarray] = []
        self._buf_start = 0.0
        self._silence_run = 0.0
        self._t = 0.0  # seconds fed so far
        self._hang = 0.0

    @property
    def kind(self) -> str:
        return "silero" if self._silero else "energy"

    def feed(self, frame: np.ndarray) -> tuple[bool, list[Utterance]]:
        dur = len(frame) / self.sr
        out: list[Utterance] = []
        if self._silero is not None:
            self._silero.accept_waveform(frame)
            while not self._silero.empty():
                seg = self._silero.front
                out.append(Utterance(seg.start / self.sr, np.array(seg.samples, dtype=np.float32)))
                self._silero.pop()
            speech = self._silero.is_speech_detected()
        else:
            speech = self._energy(frame, dur, out)
        self._t += dur
        return speech, out

    def flush(self) -> list[Utterance]:
        out: list[Utterance] = []
        if self._silero is not None:
            self._silero.flush()
            while not self._silero.empty():
                seg = self._silero.front
                out.append(Utterance(seg.start / self.sr, np.array(seg.samples, dtype=np.float32)))
                self._silero.pop()
        elif self._buf:
            out.append(Utterance(self._buf_start, np.concatenate(self._buf)))
            self._buf = []
        return out

    def _energy(self, frame: np.ndarray, dur: float, out: list[Utterance]) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)) + 1e-12)
        loud = 20 * np.log10(rms) > self.threshold_db
        self._hang = 0.3 if loud else max(0.0, self._hang - dur)
        speech = loud or self._hang > 0
        if speech:
            if not self._buf:
                self._buf_start = self._t
            self._buf.append(frame.copy())
            self._silence_run = 0.0
        elif self._buf:
            self._silence_run += dur
            self._buf.append(frame.copy())
            if self._silence_run >= 0.5:
                out.append(Utterance(self._buf_start, np.concatenate(self._buf)))
                self._buf = []
        if self._buf and sum(len(b) for b in self._buf) / self.sr >= self.max_utt:
            out.append(Utterance(self._buf_start, np.concatenate(self._buf)))
            self._buf = []
        return speech


def speech_regions(audio: np.ndarray, model: Path | None, energy_threshold_db: float = -48.0) -> list[tuple[float, float]]:
    """Whole-file speech regions in seconds."""
    vad = Vad(model, energy_threshold_db, max_utterance_s=30.0)
    regions: list[tuple[float, float]] = []
    step = 320
    for i in range(0, len(audio), step):
        _, utts = vad.feed(audio[i : i + step])
        regions += [(u.start_s, u.end_s) for u in utts]
    regions += [(u.start_s, u.end_s) for u in vad.flush()]
    return regions


def overlap(a0: float, a1: float, regions: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(a1, r1) - max(a0, r0)) for r0, r1 in regions)


def level_db(frame: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)) + 1e-12)
    return max(-90.0, 20 * np.log10(rms))
