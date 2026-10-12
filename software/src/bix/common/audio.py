"""Audio file helpers: decode anything to 16 kHz mono, resample, encode."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited resampling (windowed-sinc low-pass, then interpolation)."""
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    if sr_out < sr_in:
        cutoff = 0.45 * sr_out / sr_in
        taps = 101
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        h /= h.sum()
        x = np.convolve(x, h, mode="same")
    t_out = np.arange(int(len(x) * sr_out / sr_in)) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def decode(src: Path | bytes, sr: int = SR) -> np.ndarray:
    """Any audio file → float32 mono at `sr`. Uses ffmpeg for m4a/mp4/webm."""
    if (len(src) if isinstance(src, bytes) else Path(src).stat().st_size) == 0:
        raise ValueError("Файлът е празен.")
    try:
        f = io.BytesIO(src) if isinstance(src, bytes) else src
        data, file_sr = sf.read(f, dtype="float32", always_2d=True)
        data = data.mean(axis=1)
        return resample(data, file_sr, sr) if file_sr != sr else data
    except Exception:
        pass
    unreadable = "Файлът не е звуков запис или е повреден. Поддържат се WAV, FLAC, MP3 и OGG"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError(unreadable + "; за M4A, MP4 и WebM е нужен ffmpeg.")
    args = [ffmpeg, "-v", "error", "-i", "pipe:0" if isinstance(src, bytes) else str(src), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    try:
        raw = subprocess.run(args, input=src if isinstance(src, bytes) else None, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as e:
        raise ValueError(unreadable + ", M4A, MP4 и WebM.") from e
    audio = np.frombuffer(raw, dtype=np.float32).copy()
    if not len(audio):
        raise ValueError(unreadable + ", M4A, MP4 и WebM.")
    return audio


def encode_flac(audio: np.ndarray, sr: int = SR) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def encode_opus(audio: np.ndarray, sr: int = SR) -> bytes:
    """Ogg/Opus around 24 kbps: for playback in the browser and archive."""
    if sr not in (8000, 12000, 16000, 24000, 48000):
        audio, sr = resample(audio, sr, SR), SR
    buf = io.BytesIO()
    # compression_level 0.92 ≈ 24 kbps at 16 kHz mono (blueprint: ~11 MB per hour)
    with sf.SoundFile(buf, "w", samplerate=sr, channels=1, format="OGG", subtype="OPUS", compression_level=0.92) as f:
        f.write(audio)
    return buf.getvalue()


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"
