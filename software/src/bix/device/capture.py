"""Microphone capture: 20 ms frames of float32 mono at 16 kHz.

Works with any PortAudio input: the laptop microphone (WASAPI/MME on Windows),
ALSA on Linux, and the reSpeaker XVF3800 (USB Audio 2.0, already 16 kHz with
AEC and beamforming done on the array).
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

import numpy as np

from bix.common.audio import SR, resample

log = logging.getLogger(__name__)
FRAME = 320  # 20 ms


def list_inputs() -> list[dict]:
    import sounddevice as sd

    out = []
    default = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            out.append({"index": i, "name": d["name"], "api": api, "channels": d["max_input_channels"], "rate": d["default_samplerate"], "default": i == default})
    return out


def resolve_device(spec: str) -> int | None:
    """'' → default; '3' → index 3; 'XVF3800' → first input whose name contains it."""
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    inputs = list_inputs()
    # Prefer WASAPI / ALSA over MME duplicates of the same device.
    rank = {"Windows WASAPI": 0, "ALSA": 0, "MME": 1, "Windows DirectSound": 2}
    hits = [d for d in inputs if spec.lower() in d["name"].lower()]
    if not hits:
        raise RuntimeError(f"Няма микрофон, чието име съдържа „{spec}“. Налични: " + "; ".join(d["name"] for d in inputs))
    hits.sort(key=lambda d: rank.get(d["api"], 3))
    return hits[0]["index"]


class _FileStream:
    """Looks like a sounddevice stream to Capture."""

    def __init__(self, stop: threading.Event) -> None:
        self._stop_ev = stop
        self.active = True

    def stop(self) -> None:
        self._stop_ev.set()
        self.active = False

    def close(self) -> None:
        pass


class Capture:
    """Reads the microphone on PortAudio's thread, hands out 20 ms frames."""

    def __init__(self, device: str = "") -> None:
        self.device_spec = device
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=3000)  # 60 s backlog
        self._stream = None
        self._rate = SR
        self._pending = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._carry = np.zeros(0, dtype=np.float32)
        self.dropped = 0
        self.device_name = ""

    def start(self) -> None:
        if self.device_spec.startswith("file:"):
            return self._start_file(self.device_spec[5:])
        import sounddevice as sd

        dev = resolve_device(self.device_spec)
        info = sd.query_devices(dev, "input")
        self.device_name = info["name"]
        channels = 1
        try:
            sd.check_input_settings(device=dev, samplerate=SR, channels=channels, dtype="float32")
            self._rate = SR
        except Exception:
            self._rate = int(info["default_samplerate"])
            log.info("%s does not take 16 kHz; capturing at %d Hz and resampling", self.device_name, self._rate)
        block = int(self._rate * 0.02)
        self._stream = sd.InputStream(device=dev, samplerate=self._rate, channels=channels, dtype="float32", blocksize=block, callback=self._cb)
        self._stream.start()
        log.info("capturing from %s at %d Hz", self.device_name, self._rate)

    def _start_file(self, path: str) -> None:
        """Virtual microphone: plays an audio file in real time, in a loop
        with 3 s of silence between rounds.
        For demos and tests without a room (input_device = "file:meeting.wav")."""
        import time

        from bix.common.audio import decode

        audio = np.concatenate([decode(Path(path)), np.zeros(SR * 3, dtype=np.float32)])
        self.device_name = f"файл: {Path(path).name}"
        self._file_stop = threading.Event()

        def run() -> None:
            t0, i = time.monotonic(), 0
            while not self._file_stop.is_set():
                j = i % len(audio)
                frame = audio[j : j + FRAME]
                if len(frame) < FRAME:
                    frame = np.concatenate([frame, np.zeros(FRAME - len(frame), dtype=np.float32)])
                self._push(frame)
                i += FRAME
                delay = t0 + i / SR - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

        self._stream = _FileStream(self._file_stop)
        threading.Thread(target=run, name="bix-filemic", daemon=True).start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    def _cb(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        if status and status.input_overflow:
            self.dropped += 1
        x = indata[:, 0].copy()
        if self._rate != SR:
            # Resample in larger pieces to keep the filter's edge effects small.
            self._pending = np.concatenate([self._pending, x])
            if len(self._pending) < self._rate // 5:
                return
            x = resample(self._pending, self._rate, SR)
            self._pending = np.zeros(0, dtype=np.float32)
        self._push(x)

    def _push(self, x: np.ndarray) -> None:
        with self._lock:
            buf = np.concatenate([self._carry, x])
            n = len(buf) // FRAME
            for i in range(n):
                try:
                    self.frames.put_nowait(buf[i * FRAME : (i + 1) * FRAME])
                except queue.Full:
                    self.dropped += 1
            self._carry = buf[n * FRAME :]
