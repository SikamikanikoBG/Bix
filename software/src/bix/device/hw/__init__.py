"""Device hardware adapters. Each one is optional: if the part (or its Python
package) is missing, Bix runs without it."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Colours for the LED ring (0xRRGGBB).
COLORS = {"idle": 0x202020, "recording": 0x00A0FF, "muted": 0xFF0000, "uploading": 0xFFA000, "error": 0xFF00FF, "identify": 0xFFFFFF}


class NullLeds:
    kind = "none"

    def show(self, state: str) -> None:
        pass

    def doa(self) -> tuple[int, bool] | None:
        return None


class XvfLeds:
    """LED ring on the XVF3800: points at the speaker while recording, red when muted."""

    kind = "xvf3800"

    def __init__(self, brightness_pct: int) -> None:
        from bix.device.hw.xvf3800 import XVF3800

        self.x = XVF3800()
        # Blueprint: ring brightness capped at 25% in software.
        self.x.brightness(int(255 * max(0, min(100, brightness_pct)) / 100))

    def show(self, state: str) -> None:
        from bix.device.hw import xvf3800 as x

        try:
            if state == "recording":
                self.x.color(COLORS["recording"])
                self.x.effect(x.EFFECT_DOA)
            elif state == "idle":
                self.x.effect(x.EFFECT_OFF)
            elif state in ("muted", "error"):
                self.x.color(COLORS[state])
                self.x.effect(x.EFFECT_SOLID)
            else:
                self.x.color(COLORS.get(state, COLORS["uploading"]))
                self.x.effect(x.EFFECT_BREATH)
        except Exception as e:
            log.warning("LED: %s", e)

    def doa(self) -> tuple[int, bool] | None:
        try:
            return self.x.doa()
        except Exception:
            return None


def make_leds(kind: str, brightness_pct: int):
    if kind in ("auto", "xvf3800"):
        try:
            return XvfLeds(brightness_pct)
        except Exception as e:
            if kind == "xvf3800":
                log.warning("XVF3800 LEDs unavailable: %s", e)
    return NullLeds()
