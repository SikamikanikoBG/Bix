"""Who spoke when: diarization, speaker embeddings, voice-profile matching.

Runs locally with sherpa-onnx (pyannote segmentation 3.0 + a speaker
embedding model). No audio leaves the server for this step.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bix.common import models
from bix.server.config import DiarizationCfg
from bix.server.stt.base import Segment, Word

log = logging.getLogger(__name__)
SR = 16000


@dataclass
class Turn:
    start: float
    end: float
    label: str


_extractors: dict[str, object] = {}
_lock = threading.Lock()


def _extractor(models_dir: Path, cfg: DiarizationCfg):
    import sherpa_onnx as so

    key = cfg.embedding_model
    with _lock:
        if key not in _extractors:
            _extractors[key] = so.SpeakerEmbeddingExtractor(
                so.SpeakerEmbeddingExtractorConfig(model=str(models_dir / key), num_threads=cfg.num_threads)
            )
        return _extractors[key]


def diarize(audio: np.ndarray, models_dir: Path, cfg: DiarizationCfg, num_speakers: int = 0) -> list[Turn]:
    import sherpa_onnx as so

    models.ensure(models_dir, cfg.embedding_model, progress=log.info)
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(models_dir / models.SEGMENTATION)),
            num_threads=cfg.num_threads,
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(models_dir / cfg.embedding_model), num_threads=cfg.num_threads),
        clustering=so.FastClusteringConfig(num_clusters=num_speakers if num_speakers > 0 else -1, threshold=cfg.threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Невалидна конфигурация за разпознаване на говорещи (липсват модели?)")
    sd = so.OfflineSpeakerDiarization(config)
    result = sd.process(audio).sort_by_start_time()
    turns = [Turn(r.start, r.end, f"S{r.speaker + 1}") for r in result]
    return turns


def embed(audio: np.ndarray, models_dir: Path, cfg: DiarizationCfg) -> np.ndarray:
    ex = _extractor(models_dir, cfg)
    s = ex.create_stream()
    s.accept_waveform(SR, audio)
    s.input_finished()
    v = np.array(ex.compute(s), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def speaker_embeddings(audio: np.ndarray, turns: list[Turn], models_dir: Path, cfg: DiarizationCfg, max_seconds: float = 60.0) -> dict[str, np.ndarray]:
    """One embedding per label from its longest turns (up to max_seconds)."""
    by_label: dict[str, list[Turn]] = {}
    for t in turns:
        by_label.setdefault(t.label, []).append(t)
    out = {}
    for label, ts in by_label.items():
        ts = sorted(ts, key=lambda t: t.end - t.start, reverse=True)
        pieces, total = [], 0.0
        for t in ts:
            if t.end - t.start < 1.0 and pieces:
                break
            pieces.append(audio[int(t.start * SR) : int(t.end * SR)])
            total += t.end - t.start
            if total >= max_seconds:
                break
        clip = np.concatenate(pieces) if pieces else np.zeros(SR, dtype=np.float32)
        if len(clip) < SR // 2:
            continue
        out[label] = embed(clip, models_dir, cfg)
    return out


def merge_small(turns: list[Turn], embs: dict[str, np.ndarray], min_seconds: float) -> list[Turn]:
    """Fold clusters with very little speech into the most similar big one."""
    totals: dict[str, float] = {}
    for t in turns:
        totals[t.label] = totals.get(t.label, 0.0) + t.end - t.start
    big = [l for l, s in totals.items() if s >= min_seconds and l in embs]
    if not big:
        return turns
    remap = {}
    for label, secs in totals.items():
        if label in big:
            continue
        if label in embs:
            best = max(big, key=lambda b: float(embs[b] @ embs[label]))
        else:
            best = max(big, key=lambda b: totals[b])
        remap[label] = best
    if not remap:
        return turns
    return [Turn(t.start, t.end, remap.get(t.label, t.label)) for t in turns]


def relabel_by_first_appearance(turns: list[Turn]) -> list[Turn]:
    order: dict[str, str] = {}
    for t in sorted(turns, key=lambda t: t.start):
        if t.label not in order:
            order[t.label] = f"S{len(order) + 1}"
    return [Turn(t.start, t.end, order[t.label]) for t in turns]


def assign(segments: list[Segment], turns: list[Turn]) -> list[Segment]:
    """Give every transcript segment a speaker; split segments at speaker changes."""
    if not turns:
        return segments

    def label_at(a: float, b: float) -> str | None:
        best, best_ov = None, 0.0
        for t in turns:
            if t.end <= a:
                continue
            if t.start >= b:
                break
            ov = min(b, t.end) - max(a, t.start)
            if ov > best_ov:
                best, best_ov = t.label, ov
        if best is None:  # in a gap: nearest turn
            mid = (a + b) / 2
            best = min(turns, key=lambda t: min(abs(t.start - mid), abs(t.end - mid))).label
        return best

    out: list[Segment] = []
    for seg in segments:
        if seg.words and len(seg.words) > 1:
            groups: list[tuple[str, list[Word]]] = []
            for w in seg.words:
                lab = label_at(w.start, max(w.end, w.start + 0.05))
                if groups and groups[-1][0] == lab:
                    groups[-1][1].append(w)
                else:
                    groups.append((lab, [w]))
            # Ignore one-word flickers: fold groups of < 2 words into neighbours.
            merged: list[tuple[str, list[Word]]] = []
            for lab, ws in groups:
                if merged and (len(ws) < 2 or merged[-1][0] == lab):
                    merged[-1][1].extend(ws)
                else:
                    merged.append((lab, ws))
            if len(merged) > 1 and len(merged[0][1]) < 2:
                merged[1] = (merged[1][0], merged[0][1] + merged[1][1])
                merged = merged[1:]
            for lab, ws in merged:
                text = " ".join(w.text for w in ws).strip()
                out.append(Segment(ws[0].start, ws[-1].end, text, speaker=lab, confidence=seg.confidence, words=ws))
        else:
            seg.speaker = label_at(seg.start, seg.end)
            out.append(seg)
    return out


def match_profiles(embs: dict[str, np.ndarray], people: list[tuple[str, np.ndarray]], threshold: float) -> dict[str, tuple[str, float]]:
    """label -> (person_id, score); each person used at most once, best pairs first."""
    pairs = []
    for label, e in embs.items():
        for pid, pe in people:
            score = float(e @ pe)
            if score >= threshold:
                pairs.append((score, label, pid))
    pairs.sort(reverse=True)
    used_l, used_p, out = set(), set(), {}
    for score, label, pid in pairs:
        if label in used_l or pid in used_p:
            continue
        out[label] = (pid, round(score, 3))
        used_l.add(label)
        used_p.add(pid)
    return out


def update_profile(old: list[float], old_n: int, new: np.ndarray) -> list[float]:
    v = (np.array(old, dtype=np.float32) * old_n + new) / (old_n + 1) if old else new
    v = v / (np.linalg.norm(v) + 1e-9)
    return [round(float(x), 6) for x in v]
