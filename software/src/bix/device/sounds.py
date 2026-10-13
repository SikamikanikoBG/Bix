"""Room sounds the face reacts to: a knock on the case, a sudden bang.

Works on the level and the spectrum of single 20 ms frames, in memory only:
nothing is kept and nothing says what was spoken (the room is not being
recorded when the face reacts). A knock on the case is short, comes out of
quiet and is mostly low frequencies (the shell carries it to the mics); a bang
(door, clap, a dropped bag) is a sudden loud jump of any kind.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME_S = 0.02
ONSET_DB = 18.0  # a jump this far above the room's quiet level starts a sound
MIN_DB = -48.0  # quieter than this is never a knock
BANG_DB = -20.0  # this loud is a bang, however long
KNOCK_MAX_S = 0.12  # a knock is over by then; words last longer
KNOCK_HZ = 1100.0  # spectral centroid below this: a thump, not a click or a word
KNOCK_GAP_S = 0.9  # knocks closer than this belong to one "knock-knock"
SPEECH_HOLD_S = 0.5  # right after speech, onsets are syllables, not knocks
SR = 16000


@dataclass
class Sound:
    kind: str  # "knock" | "bang"
    count: int = 1  # knocks in a row
    db: float = 0.0


def centroid_hz(frame: np.ndarray, sr: int = SR) -> float:
    # power-weighted, so the room's hiss doesn't pull a thump upwards
    spec = np.abs(np.fft.rfft(frame.astype(np.float64) * np.hanning(len(frame)))) ** 2
    freqs = np.fft.rfftfreq(len(frame), 1 / sr)
    total = float(spec.sum())
    return float((spec * freqs).sum() / total) if total > 0 else 0.0


class SoundEvents:
    def __init__(self) -> None:
        self.floor = -60.0
        self.prev = -90.0
        self.t = 0.0
        self.burst: dict | None = None
        self.knocks: list[float] = []
        self.last_speech = -9.0
        self.banged = False

    def feed(self, frame: np.ndarray, db: float, speech: bool) -> list[Sound]:
        self.t += FRAME_S
        t, out = self.t, []
        if speech:
            self.last_speech = t
        b = self.burst
        if b is None:
            from_quiet = self.prev - self.floor < 6
            if db - self.floor >= ONSET_DB and db >= MIN_DB and from_quiet:
                self.burst = b = {"t0": t, "peak": db, "hz": centroid_hz(frame), "near_speech": t - self.last_speech < SPEECH_HOLD_S}
                self.banged = False
        else:
            b["peak"] = max(b["peak"], db)
            if b["peak"] >= BANG_DB and not self.banged:
                self.banged = True
                out.append(Sound("bang", db=round(b["peak"], 1)))
            if db < b["peak"] - 12 or db < self.floor + 8:  # over
                length = t - b["t0"]
                if not self.banged and not b["near_speech"] and not speech and length <= KNOCK_MAX_S and b["hz"] < KNOCK_HZ:
                    self.knocks.append(t)
                self.burst = None
        if self.knocks and t - self.knocks[-1] > KNOCK_GAP_S and self.burst is None:
            out.append(Sound("knock", count=len(self.knocks)))
            self.knocks = []
        # the quiet level: follows drops at once, rises slowly (speech doesn't lift it much)
        if self.burst is None:
            self.floor += (db - self.floor) * (0.2 if db < self.floor else 0.004)
        self.prev = db
        return out
