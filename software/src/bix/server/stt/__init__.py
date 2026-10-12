from __future__ import annotations

from bix.server.config import ServerConfig
from bix.server.stt.base import Segment, SttError, Transcriber, Word


def make(cfg: ServerConfig, provider: str | None = None) -> Transcriber:
    p = provider or cfg.stt.provider
    if p == "azure":
        from bix.server.stt.azure import AzureFastTranscriber

        return AzureFastTranscriber(cfg.stt.azure, cfg.azure)
    if p == "whisper_http":
        from bix.server.stt.whisper_http import WhisperHttpTranscriber

        return WhisperHttpTranscriber(cfg.stt.whisper_http)
    if p == "faster_whisper":
        from bix.server.stt.faster_whisper_local import FasterWhisperTranscriber

        return FasterWhisperTranscriber(cfg.stt.faster_whisper, cfg.models_dir)
    raise SttError(f"Непознат доставчик за реч: {p}")


__all__ = ["Segment", "SttError", "Transcriber", "Word", "make"]
