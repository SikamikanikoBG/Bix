"""Whisper on the server itself (faster-whisper / CTranslate2). No data leaves the host.

CPU works but is slow for long meetings; a CUDA GPU makes it real time.
Install: pip install "bix[local-stt]"
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from bix.server.config import FasterWhisperCfg
from bix.server.stt.base import Segment, SttError, Word, fix_caps, fix_caps_words
from bix.server.stt.whisper_http import _hallucination

log = logging.getLogger(__name__)

_models: dict[tuple, object] = {}
_lock = threading.Lock()


class FasterWhisperTranscriber:
    name = "faster_whisper"
    diarizes = False

    def __init__(self, cfg: FasterWhisperCfg, models_dir) -> None:
        self.cfg = cfg
        self.models_dir = models_dir

    def _model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise SttError('faster-whisper не е инсталиран: pip install "bix[local-stt]"') from e
        device = self.cfg.device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = self.cfg.compute_type if device == "cpu" else ("float16" if self.cfg.compute_type == "int8" else self.cfg.compute_type)
        key = (self.cfg.model, device, compute)
        with _lock:
            if key not in _models:
                log.info("loading faster-whisper %s on %s (%s)", *key)
                try:
                    _models[key] = WhisperModel(self.cfg.model, device=device, compute_type=compute, download_root=str(self.models_dir / "whisper"))
                except Exception as e:
                    # Usually the first download from Hugging Face breaking off; the worker retries.
                    raise SttError(f"Моделът {self.cfg.model} не се зареди (Няма връзка до Hugging Face?): {str(e)[:200]}") from e
            return _models[key]

    def check(self) -> str:
        self._model()
        return self.cfg.model

    def transcribe(self, audio: np.ndarray, language: str, hints: list[str] | None = None) -> list[Segment]:
        model = self._model()
        segments, _info = model.transcribe(
            audio,
            language=language,
            vad_filter=True,
            word_timestamps=True,
            initial_prompt=", ".join(sorted(set(hints)))[:600] if hints else None,
            beam_size=5,
        )
        out = []
        for s in segments:
            d = {"no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob, "compression_ratio": s.compression_ratio}
            if _hallucination(d) or not s.text.strip():
                continue
            words = [Word(w.start, w.end, w.word.strip()) for w in (s.words or [])]
            words = fix_caps_words(s.text, words)
            out.append(Segment(s.start, s.end, fix_caps(s.text.strip()), confidence=round(float(np.exp(s.avg_logprob)), 3), words=words))
        return out
