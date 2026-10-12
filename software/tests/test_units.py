from __future__ import annotations

import numpy as np

from bix.common.audio import decode, encode_flac, encode_opus, resample
from bix.common.wer import cer, wer
from bix.device.hw.ld2410 import parse
from bix.device.spool import Spool, recover_open
from bix.server import minutes, speakers
from bix.server.stt.base import Segment, Word


def test_resample_keeps_length_and_tone():
    sr = 48000
    t = np.arange(sr) / sr
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    y = resample(x, sr, 16000)
    assert len(y) == 16000
    spec = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spec) - 440) <= 1


def test_codecs_roundtrip():
    x = (0.2 * np.sin(np.arange(16000) / 5)).astype(np.float32)
    assert np.allclose(decode(encode_flac(x)), x, atol=1e-3)
    assert len(encode_opus(x)) < len(encode_flac(x))


def test_wer():
    assert wer("Добро утро на всички", "добро утро на всички!") == 0
    assert wer("едно две три четири", "едно две три") == 0.25
    assert cer("абв", "абв") == 0


def test_ld2410_parser():
    data = bytes([0x02, 0xAA, 0x03, 0x64, 0x00, 0x32, 0x96, 0x00, 0x28, 0xC8, 0x00, 0x55, 0x00])
    frame = b"\xf4\xf3\xf2\xf1" + len(data).to_bytes(2, "little") + data + b"\xf8\xf7\xf6\xf5"
    buf = bytearray(b"\x00\x11" + frame + frame[:5])
    out = parse(buf)
    assert out == [{"state": 3, "moving_cm": 100, "moving_energy": 50, "static_cm": 150, "static_energy": 40, "distance_cm": 200}]
    assert bytes(buf) == frame[:5]  # partial frame kept for the next read


def test_spool_store_and_forward(tmp_path):
    sp = Spool(tmp_path)
    m = sp.create("a" * 32, 1000.0)
    sp.add_chunk(m.id, np.zeros(16000 * 3, dtype=np.float32), 0.0)
    sp.add_chunk(m.id, np.zeros(16000 * 2, dtype=np.float32), 3.0)
    assert sp.backlog_seconds() == 5.0
    sp.mark_uploaded(m.id, 0)
    assert sp.backlog_seconds() == 2.0
    assert not sp.chunk_path(m.id, 0).exists()
    # power loss before the meeting was ended
    assert recover_open(sp) == [m.id]
    assert sp.load(m.id).ended_at == 1005.0


def test_assign_splits_at_speaker_change():
    turns = [speakers.Turn(0, 3, "S1"), speakers.Turn(3, 6, "S2")]
    words = [Word(0.2, 0.8, "Кога"), Word(0.9, 1.5, "е"), Word(1.6, 2.5, "датата?"), Word(3.2, 4.0, "Петнадесети"), Word(4.1, 5.0, "ноември.")]
    out = speakers.assign([Segment(0.2, 5.0, "Кога е датата? Петнадесети ноември.", words=words)], turns)
    assert [(s.speaker, s.text) for s in out] == [("S1", "Кога е датата?"), ("S2", "Петнадесети ноември.")]


def test_profile_matching_is_one_to_one():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    ab = np.array([0.8, 0.6], dtype=np.float32)
    got = speakers.match_profiles({"S1": a, "S2": ab}, [("p1", a), ("p2", b)], threshold=0.5)
    assert got["S1"][0] == "p1"
    assert got["S2"][0] == "p2"


def test_minutes_clean_drops_unsupported_items():
    raw = {
        "title": "Т", "summary": "С", "speaker_names": [{"label": "S1", "name": "Мария", "evidence": "x"}],
        "topics": [], "decisions": [{"text": "без препратка", "refs": []}, {"text": "реално", "refs": [2, 99]}],
        "action_items": [{"task": "задача", "owner": None, "due": "петък", "refs": [1]}], "open_questions": [],
    }
    out = minutes._clean(raw, n_lines=3)
    assert out["decisions"] == [{"text": "реално", "refs": [2]}]
    assert out["action_items"][0]["due"] == "петък"
    md = minutes.render_markdown(out, {"title": "Т", "date": "1", "duration": "2", "room": "З"}, "bg")
    assert "## Решения" in md and "реално" in md
    html = minutes.render_html(out, {"title": "<b>", "url": "http://x/#/m"}, "bg")
    assert "&lt;b&gt;" in html and "http://x/#/m#L2" in html


def test_transcript_prompt_numbers_lines():
    segs = [{"start": 0, "speaker": "S1", "text": "Здравейте"}, {"start": 65, "speaker": "S2", "text": "Добро утро"}]
    txt = minutes.format_transcript(segs, {"S1": "Мария"})
    assert txt.splitlines() == ["[1] 00:00 S1 (Мария): Здравейте", "[2] 01:05 S2: Добро утро"]


def test_cli_errors_are_readable(tmp_path):
    """Regression: CLI failures printed Python tracebacks."""
    import pytest

    from bix.cli import main

    with pytest.raises(SystemExit) as e:
        main(["eval", str(tmp_path / "missing.txt"), str(tmp_path / "x.json")])
    assert str(e.value).startswith("Грешка: няма такъв файл")


def test_fix_caps():
    from bix.server.stt.base import fix_caps

    assert fix_caps("ДОБРЕ, РЕШАВАМЕ ТАКА. КАМПАНИЯТА ТРЪГВА 1 НОЕМВРИ.") == "Добре, решаваме така. Кампанията тръгва 1 ноември."
    assert fix_caps("Срещата с NATO и ЕС беше добра.") == "Срещата с NATO и ЕС беше добра."  # normal text untouched
    assert fix_caps("ОК") == "ОК"


def test_fix_caps_words_keeps_sentence_starts():
    from bix.server.stt.base import Word, fix_caps_words

    ws = [Word(0, 1, "ДОБРЕ,"), Word(1, 2, "РЕШАВАМЕ"), Word(2, 3, "ТАКА."), Word(3, 4, "КАМПАНИЯТА"), Word(4, 5, "ТРЪГВА.")]
    got = [w.text for w in fix_caps_words("ДОБРЕ, РЕШАВАМЕ ТАКА. КАМПАНИЯТА ТРЪГВА.", ws)]
    assert got == ["Добре,", "решаваме", "така.", "Кампанията", "тръгва."]


def test_whisper_http_sends_long_audio_in_pieces(monkeypatch):
    """Regression: a 1h49m meeting in one request hit the proxy's 600 s limit (504)."""
    import httpx

    from bix.server.config import WhisperHttpCfg
    from bix.server.stt.whisper_http import WhisperHttpTranscriber, _pieces

    sr = 16000
    audio = (0.3 * np.sin(np.arange(25 * 60 * sr) / 7)).astype(np.float32)
    audio[int(477.0 * sr) : int(477.4 * sr)] = 0  # a pause near the first cut
    pieces = _pieces(audio, 480)
    assert len(pieces) == 4 and all(len(p) <= 480 * sr for _, p in pieces)
    assert 477.0 <= pieces[1][0] <= 477.4
    assert sum(len(p) for _, p in pieces) == len(audio)

    calls = []

    def post(url, **kw):
        calls.append(kw)
        seg = {"start": 1.0, "end": 2.0, "text": "Добро утро", "words": [{"start": 1.0, "end": 1.5, "word": "Добро"}]}
        return httpx.Response(200, json={"segments": [seg]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    segs = WhisperHttpTranscriber(WhisperHttpCfg(url="http://x", chunk_s=480)).transcribe(audio, "bg")
    assert len(calls) == 4
    assert [round(s.start - o, 3) for s, (o, _) in zip(segs, pieces)] == [1.0] * 4
    assert segs[1].words[0].start == segs[1].start


def test_failed_model_download_is_retried(monkeypatch, tmp_path):
    """Regression: a broken Hugging Face download failed the meeting with no retry."""
    import sys
    import types

    from bix.server.config import FasterWhisperCfg
    from bix.server.stt import faster_whisper_local
    from bix.server.stt.base import SttError
    from bix.server.worker import _transient

    def broken(*a, **k):
        raise RuntimeError("Task error: File reconstruction error: CAS Client Error")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=broken))
    t = faster_whisper_local.FasterWhisperTranscriber(FasterWhisperCfg(model="tiny-test", device="cpu"), tmp_path)
    try:
        t.check()
        raise AssertionError("expected SttError")
    except SttError as e:
        assert _transient(e)
