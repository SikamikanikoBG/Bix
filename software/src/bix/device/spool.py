"""Store-and-forward buffer on the device.

Each meeting is a folder of FLAC chunks plus a small JSON manifest. Chunks are
deleted only after the server has confirmed them, so a network outage or a
power cut loses nothing that was already written (blueprint: up to 2 hours).
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from bix.common.audio import SR


@dataclass
class ChunkInfo:
    seq: int
    start_s: float
    duration_s: float
    uploaded: bool = False


@dataclass
class SpoolMeeting:
    id: str
    started_at: float
    title: str = ""
    registered: bool = False  # server knows about it
    ended_at: float | None = None
    ended_sent: bool = False
    discard: bool = False
    calendar_uid: str = ""
    notes: list[dict] = field(default_factory=list)  # stars and notes made on the device
    chunks: list[ChunkInfo] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return sum(c.duration_s for c in self.chunks)


class Spool:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _dir(self, mid: str) -> Path:
        return self.root / mid

    def create(self, mid: str, started_at: float, title: str = "", calendar_uid: str = "") -> SpoolMeeting:
        with self._lock:
            self._dir(mid).mkdir(parents=True, exist_ok=True)
            m = SpoolMeeting(id=mid, started_at=started_at, title=title, calendar_uid=calendar_uid)
            self.save(m)
            return m

    def save(self, m: SpoolMeeting) -> None:
        with self._lock:
            p = self._dir(m.id) / "manifest.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(m)), encoding="utf-8")
            tmp.replace(p)

    def load(self, mid: str) -> SpoolMeeting | None:
        p = self._dir(mid) / "manifest.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        d["chunks"] = [ChunkInfo(**c) for c in d.get("chunks", [])]
        return SpoolMeeting(**d)

    def meetings(self) -> list[SpoolMeeting]:
        with self._lock:
            out = []
            for d in sorted(self.root.iterdir()):
                if d.is_dir() and (m := self.load(d.name)):
                    out.append(m)
            return out

    def add_chunk(self, mid: str, audio: np.ndarray, start_s: float) -> ChunkInfo:
        with self._lock:
            m = self.load(mid)
            seq = len(m.chunks)
            path = self.chunk_path(mid, seq)
            tmp = path.with_suffix(".part")
            sf.write(tmp, audio, SR, format="FLAC", subtype="PCM_16")
            tmp.replace(path)
            c = ChunkInfo(seq=seq, start_s=round(start_s, 3), duration_s=round(len(audio) / SR, 3))
            m.chunks.append(c)
            self.save(m)
            return c

    def chunk_path(self, mid: str, seq: int) -> Path:
        return self._dir(mid) / f"chunk_{seq:06d}.flac"

    def mark_uploaded(self, mid: str, seq: int) -> None:
        with self._lock:
            m = self.load(mid)
            if m is None:
                return
            m.chunks[seq].uploaded = True
            self.save(m)
            self.chunk_path(mid, seq).unlink(missing_ok=True)

    def add_note(self, mid: str, note: dict) -> None:
        with self._lock:
            m = self.load(mid)
            if m is not None:
                m.notes = [*m.notes, note][-200:]
                self.save(m)

    def update(self, mid: str, **fields) -> None:
        with self._lock:
            m = self.load(mid)
            if m is None:
                return
            for k, v in fields.items():
                setattr(m, k, v)
            self.save(m)

    def remove(self, mid: str) -> None:
        with self._lock:
            shutil.rmtree(self._dir(mid), ignore_errors=True)

    def backlog_seconds(self) -> float:
        return sum(c.duration_s for m in self.meetings() for c in m.chunks if not c.uploaded)

    def enforce_limit(self, max_hours: float) -> list[str]:
        """Over the limit: drop the oldest finished meeting's pending audio."""
        dropped = []
        while self.backlog_seconds() > max_hours * 3600:
            finished = [m for m in self.meetings() if m.ended_at]
            if not finished:
                break
            victim = min(finished, key=lambda m: m.started_at)
            self.remove(victim.id)
            dropped.append(victim.id)
        return dropped


def recover_open(spool: Spool) -> list[str]:
    """After a crash, meetings without an end get one at their last chunk."""
    fixed = []
    for m in spool.meetings():
        if m.ended_at is None:
            spool.update(m.id, ended_at=m.started_at + m.duration_s or time.time())
            fixed.append(m.id)
    return fixed
