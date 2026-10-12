"""The organisation's glossary: names, products, abbreviations and terms, spelled right.

One entry per line in Settings:
    Пощенска банка
    ДСК: DSK, ди ес ке          the right spelling, then what speech recognition writes instead
    Иван Петров = Иван Петрoв

Used three ways:
  1. speech recognition is biased towards the terms (Azure phrase list, Whisper prompt);
  2. the known wrong spellings are replaced in the transcript (whole words, any case);
  3. the language model gets the list, so the minutes and the answers use the right spelling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_LINES = 1000
MAX_LINE = 300


@dataclass
class Entry:
    term: str
    variants: list[str] = field(default_factory=list)


def parse(lines: list[str] | str | None) -> list[Entry]:
    if not lines:
        return []
    if isinstance(lines, str):
        lines = lines.splitlines()
    out, seen = [], set()
    for raw in lines:
        line = str(raw).strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.+?)\s*[:=]\s*(.+)$", line)
        term, rest = (m.group(1).strip(), m.group(2)) if m else (line, "")
        variants = [v.strip() for v in re.split(r"[,;]", rest) if v.strip() and v.strip().lower() != term.lower()]
        if term.lower() in seen:
            continue
        seen.add(term.lower())
        out.append(Entry(term, variants))
    return out


def clean(lines: list[str] | str) -> list[str]:
    """Validate what an admin typed; raises ValueError with a readable message."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    lines = [str(x).strip() for x in lines if str(x).strip()]
    if len(lines) > MAX_LINES:
        raise ValueError(f"Речникът може да има до {MAX_LINES} реда.")
    long = [x for x in lines if len(x) > MAX_LINE]
    if long:
        raise ValueError(f"Твърде дълъг ред в речника (до {MAX_LINE} знака): {long[0][:40]}…")
    return lines


def hints(entries: list[Entry]) -> list[str]:
    return [e.term for e in entries]


def _pattern(entries: list[Entry]) -> tuple[re.Pattern, dict[str, str]] | None:
    pairs = {v.lower(): e.term for e in entries for v in e.variants}
    # names and acronyms also get their capitals back ("мария петрова", "дск");
    # a term written all in lower case is left alone, so common words are not touched
    for e in entries:
        if e.term != e.term.lower():
            pairs.setdefault(e.term.lower(), e.term)
    if not pairs:
        return None
    alts = sorted(pairs, key=len, reverse=True)  # longest first: "ди ес ке" before "ди"
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(a) for a in alts) + r")(?!\w)", re.IGNORECASE), pairs


def fix(text: str, entries: list[Entry]) -> str:
    p = _pattern(entries)
    if not p or not text:
        return text
    rx, pairs = p
    return rx.sub(lambda m: pairs[m.group(1).lower()], text)


def fix_segments(segments: list[Any], entries: list[Entry]) -> int:
    """Correct segment texts in place; returns how many changed."""
    p = _pattern(entries)
    if not p:
        return 0
    rx, pairs = p
    n = 0
    for s in segments:
        new = rx.sub(lambda m: pairs[m.group(1).lower()], s.text)
        if new != s.text:
            s.text = new
            n += 1
    return n


def prompt_block(entries: list[Entry]) -> str:
    """For the language model: the right spellings (speech recognition may have got them wrong)."""
    if not entries:
        return ""
    lines = []
    for e in entries[:400]:
        lines.append(f"- {e.term}" + (f" (may appear as: {', '.join(e.variants[:6])})" if e.variants else ""))
    return (
        "Glossary of the organisation: names, products and terms with their correct spelling. Speech recognition may have "
        "misheard or misspelled them; when the transcript clearly means one of these, write it exactly as below:\n" + "\n".join(lines)
    )
