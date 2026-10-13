"""HLK-LD2410C 24 GHz presence radar on UART (256000 baud, 8N1).

Report frame: F4 F3 F2 F1 | len (2, LE) | 0x02 0xAA | target state | moving
distance (2) | moving energy | static distance (2) | static energy |
detection distance (2) | 0x55 0x00 | F8 F7 F6 F5.
Target state: 0 none, 1 moving, 2 static, 3 both.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

HEAD = b"\xf4\xf3\xf2\xf1"
TAIL = b"\xf8\xf7\xf6\xf5"


def parse(buf: bytearray) -> list[dict]:
    """Consume complete frames from buf; return parsed reports."""
    out = []
    while True:
        i = buf.find(HEAD)
        if i < 0:
            del buf[: max(0, len(buf) - 3)]
            return out
        if i:
            del buf[:i]
        if len(buf) < 6:
            return out
        n = int.from_bytes(buf[4:6], "little")
        end = 6 + n + 4
        if n > 64:
            del buf[:4]
            continue
        if len(buf) < end:
            return out
        frame = bytes(buf[:end])
        del buf[:end]
        if frame[-4:] != TAIL:
            continue
        data = frame[6 : 6 + n]
        if len(data) >= 13 and data[1] == 0xAA:
            out.append(
                {
                    "state": data[2],
                    "moving_cm": int.from_bytes(data[3:5], "little"),
                    "moving_energy": data[5],
                    "static_cm": int.from_bytes(data[6:8], "little"),
                    "static_energy": data[8],
                    "distance_cm": int.from_bytes(data[9:11], "little"),
                }
            )


def nearest_cm(r: dict) -> int | None:
    """The closer of the moving and the still target; None when nobody is there."""
    found = []
    if r["state"] & 1:
        found.append(r["moving_cm"])
    if r["state"] & 2:
        found.append(r["static_cm"])
    return min(found) if found else None


class Presence:
    """Calls on_change(True/False). 'Absent' only after no target for hold_s.
    on_distance(cm or None) gets how far the nearest person is, when it changes
    by DISTANCE_STEP_CM or more (at most a few times a second): the face reacts
    to someone walking up to it."""

    DISTANCE_STEP_CM = 15
    DISTANCE_EVERY_S = 0.25

    def __init__(self, port: str, on_change: Callable[[bool], None], hold_s: float = 30.0,
                 on_distance: Callable[[int | None], None] | None = None) -> None:
        self.port = port
        self.on_change = on_change
        self.on_distance = on_distance
        self.hold_s = hold_s
        self.distance: int | None = None
        self._distance_at = 0.0
        self.present: bool | None = None
        self.last_seen = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="bix-presence", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import serial

        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, 256000, timeout=0.5) as ser:
                    buf = bytearray()
                    while not self._stop.is_set():
                        buf += ser.read(256)
                        for r in parse(buf):
                            if r["state"]:
                                self.last_seen = time.time()
                            self._distance(r)
                        self._update()
            except Exception as e:
                log.warning("radar on %s: %s", self.port, e)
                self._stop.wait(10)

    def _distance(self, r: dict) -> None:
        if not self.on_distance:
            return
        cm = nearest_cm(r)
        now = time.time()
        if now - self._distance_at < self.DISTANCE_EVERY_S:
            return
        if (cm is None) != (self.distance is None) or (cm is not None and abs(cm - self.distance) >= self.DISTANCE_STEP_CM):
            self.distance, self._distance_at = cm, now
            self.on_distance(cm)

    def _update(self) -> None:
        now = time.time()
        present = (now - self.last_seen) < self.hold_s
        if present != self.present:
            self.present = present
            self.on_change(present)
