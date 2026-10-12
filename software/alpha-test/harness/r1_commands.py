"""R1: portal → device commands reach the device and show in both UIs."""
import time

import httpx

from ui import BASE, PANEL, browser, report, shot


def panel_state():
    s = httpx.get(PANEL + "/api/status", timeout=5).json()
    return ("muted" if s["muted"] else s["state"])


def until(pred, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return round(time.time() - t0, 1)
        time.sleep(0.5)
    return None


with browser() as page:
    page.goto(BASE + "/#/devices")
    page.wait_for_selector("select[data-cmd]")
    for cmd, want in [("start", "recording"), ("mute", "muted"), ("unmute", "recording"), ("identify", None), ("stop", "idle")]:
        page.select_option("select[data-cmd]", cmd)
        page.wait_for_timeout(300)
        toast = page.inner_text("#toast") if page.is_visible("#toast") else ""
        took = until(lambda: panel_state() == want) if want else None
        expect = {"recording": "записва", "muted": "пауза", "idle": "онлайн"}.get(want)
        t0 = time.time()
        badge = ""
        while time.time() - t0 < 6:  # the page should follow on its own (no reload)
            badge = page.inner_text("table tbody tr td:nth-child(3)")
            if not expect or badge == expect:
                break
            page.wait_for_timeout(300)
        badge = f"{badge} (+{time.time() - t0:.1f}s)"
        print(f"{cmd:8} toast={toast!r:35} device={panel_state():9} after {took}s | portal badge={badge!r}")
        if cmd == "mute":
            shot(page, "r1_portal_muted")
        if cmd == "start":
            time.sleep(25)  # let it record something
report()
