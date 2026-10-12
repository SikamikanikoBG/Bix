"""Tiny Playwright harness for alpha-test journeys."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

AT = Path(__file__).parent / "work"
SHOTS = AT / "shots"
STATE = AT / "state.json"
BASE = "http://127.0.0.1:8720"
PANEL = "http://127.0.0.1:8721"
problems: list[str] = []


@contextmanager
def browser(width=1360, height=900, dark=False, fresh=False, state_file: Path | None = None):
    sf = state_file or STATE
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(
            viewport={"width": width, "height": height},
            color_scheme="dark" if dark else "light",
            storage_state=str(sf) if sf.exists() and not fresh else None,
            locale="bg-BG",
        )
        page = ctx.new_page()
        page.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"console {m.type}: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status} {r.request.method} {r.url}"))
        page.on("dialog", lambda d: d.accept())
        try:
            yield page
        finally:
            ctx.storage_state(path=str(sf))
            b.close()


def shot(page, name: str, full=True) -> str:
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=full)
    return str(path)


def report() -> None:
    print("PROBLEMS:" if problems else "no browser problems")
    for x in problems:
        print("  ", x)
    problems.clear()


def cred() -> tuple[str, str]:
    d = json.loads((AT / "cred.json").read_text(encoding="utf-8"))
    return d["email"], d["password"]
