"""J11 re-run: every page, desktop + phone/dark, overflow + console."""
from ui import AT, BASE, browser, report, shot
mid = (AT / "mid.txt").read_text()
pages = ["#/", "#/meetings", f"#/meetings/{mid}", "#/rooms", "#/devices", "#/people", "#/users", "#/settings", "#/audit"]
for label, kw in [("desk", dict(width=1360, height=900)), ("phone_dark", dict(width=390, height=844, dark=True))]:
    with browser(**kw) as page:
        for h in pages:
            page.goto(BASE + "/" + h)
            page.wait_for_timeout(1200)
            name = h.strip("#/").replace("/", "_")[:20] or "home"
            shot(page, f"j11b_{label}_{name}")
            if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                print(f"{label} {h}: horizontal overflow ({page.evaluate('document.documentElement.scrollWidth')}px)")
report()
