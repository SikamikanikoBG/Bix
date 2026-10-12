"""Local ONNX models: Silero VAD, pyannote segmentation, speaker embeddings.

All come from the sherpa-onnx GitHub releases and are downloaded once into
the data directory under models/ (about 50 MB together).
"""

from __future__ import annotations

import logging
import tarfile
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

VAD = "silero_vad.onnx"
SEGMENTATION = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"

EMBEDDINGS = {
    # Speaker verification models. TitaNet-small separated four Bulgarian
    # voices best in our test (tools/make_test_meeting.py) and is the fastest.
    "nemo_en_titanet_small.onnx": f"{RELEASES}/speaker-recongition-models/nemo_en_titanet_small.onnx",
    "wespeaker_en_voxceleb_resnet34_LM.onnx": f"{RELEASES}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx",
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx": f"{RELEASES}/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
}


def path(models_dir: Path, name: str) -> Path:
    return models_dir / name


def have(models_dir: Path, embedding: str) -> dict[str, bool]:
    return {
        "vad": (models_dir / VAD).exists(),
        "segmentation": (models_dir / SEGMENTATION).exists(),
        "embedding": (models_dir / embedding).exists(),
    }


def ensure_vad(models_dir: Path, progress=print) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    if not (models_dir / VAD).exists():
        _download(f"{RELEASES}/asr-models/silero_vad.onnx", models_dir / VAD, progress)


def ensure(models_dir: Path, embedding: str, progress=print) -> None:
    ensure_vad(models_dir, progress)
    if not (models_dir / SEGMENTATION).exists():
        with tempfile.TemporaryDirectory() as tmp:
            arc = Path(tmp) / "seg.tar.bz2"
            _download(f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", arc, progress)
            with tarfile.open(arc, "r:bz2") as t:
                member = t.getmember("sherpa-onnx-pyannote-segmentation-3-0/model.onnx")
                (models_dir / SEGMENTATION).parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(member) as src, open(models_dir / SEGMENTATION, "wb") as dst:
                    dst.write(src.read())
    if not (models_dir / embedding).exists():
        url = EMBEDDINGS.get(embedding)
        if not url:
            raise ValueError(f"Непознат модел за гласове: {embedding}")
        _download(url, models_dir / embedding, progress)


def _download(url: str, dest: Path, progress) -> None:
    progress(f"Изтеглям {url.rsplit('/', 1)[-1]} ...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
