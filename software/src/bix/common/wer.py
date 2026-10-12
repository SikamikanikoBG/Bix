"""Word and character error rate, for checking recognition quality (Phase 1:
target WER under 15% on real Bulgarian meetings)."""

from __future__ import annotations

import re
import unicodedata



def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().replace("ѝ", "и")
    text = re.sub(r"^\s*[\w\- ]{1,30}:\s", " ", text, flags=re.M)  # "Name: " speaker prefixes
    text = re.sub(r"[^\w\s%]", " ", text)
    return text.split()


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    r, h = normalize(reference), normalize(hypothesis)
    return edit_distance(r, h) / max(1, len(r))


def cer(reference: str, hypothesis: str) -> float:
    r, h = list(" ".join(normalize(reference))), list(" ".join(normalize(hypothesis)))
    return edit_distance(r, h) / max(1, len(r))
