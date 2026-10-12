from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from bix.server import stt as stt_mod
from bix.server.app import create_app
from bix.server.config import ServerConfig
from bix.server.core import Server
from bix.server.stt.base import Segment, Word


class FakeTranscriber:
    """Deterministic STT: one segment per 4 s of audio."""

    name = "fake"
    diarizes = False

    def check(self) -> str:
        return "fake"

    def transcribe(self, audio: np.ndarray, language: str, hints=None) -> list[Segment]:
        out = []
        dur = len(audio) / 16000
        t, i = 0.0, 0
        while t + 1 < dur:
            end = min(dur, t + 4)
            out.append(Segment(t, end, f"изречение {i}", words=[Word(t, (t + end) / 2, "изречение"), Word((t + end) / 2, end, str(i))]))
            t, i = end, i + 1
        return out


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    from bix.server import api_auth, api_device

    api_auth._failures.clear()  # throttles are per process; keep tests independent
    api_device._enroll_failures.clear()
    cfg = ServerConfig(data_dir=str(tmp_path / "server"))
    cfg.auth.secret_key = "test-secret"
    cfg.diarization.provider = "none"
    cfg.llm.provider = "none"
    cfg.http.public_url = "http://testserver"
    cfg.worker.isolate = False  # tests monkeypatch providers, which a child process wouldn't see
    monkeypatch.setattr(stt_mod, "make", lambda cfg, provider=None: FakeTranscriber())
    return Server.create(cfg, tmp_path / "server" / "server.toml")


@pytest.fixture()
def client(srv):
    app = create_app(srv, run_worker=False)
    with TestClient(app) as c:
        c.headers.update({"X-Bix": "1"})
        yield c


def tone(seconds: float, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * 16000)) / 16000
    return (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
