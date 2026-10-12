"""J6 people & voices, J8 settings validation, J11 every page desktop + phone/dark."""
import numpy as np
import soundfile as sf

from ui import AT, BASE, browser, report, shot

# a 20 s voice sample of one speaker (Мария's first turn + later turns)
a, sr = sf.read(AT / "tm" / "meeting.wav", dtype="float32")
sf.write(AT / "files" / "voice_maria.wav", np.concatenate([a[0 : 16000 * 10], a[16000 * 33 : 16000 * 42]]), sr)

with browser() as page:
    page.goto(BASE + "/#/people")
    page.wait_for_selector("#add")
    page.click("#add")
    page.fill(".modal input[name=name]", "Иван Петров")
    page.fill(".modal input[name=email]", "ivan@alpha.test")
    page.click(".modal button.primary")
    page.wait_for_timeout(800)
    page.click("[data-voice]")
    page.set_input_files(".modal input[type=file]", str(AT / "files" / "voice_maria.wav"))
    page.click(".modal button.primary")  # consent not ticked → browser blocks (required)
    page.wait_for_timeout(500)
    print("blocked without consent (modal still open):", page.locator(".modal").count() == 1)
    page.check(".modal input[name=consent]")
    page.click(".modal button.primary")
    page.wait_for_timeout(4000)
    print("voice toast:", page.inner_text("#toast") if page.is_visible("#toast") else "")
    shot(page, "j6_01_people")
    print(page.inner_text("table"))

    # settings: invalid numbers, anonymous mode
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#sf")
    page.fill("input[name=retention_audio_days]", "-5")
    page.fill("input[name=fixed_recipients]", "boss@alpha.test, nope")
    page.click("#sf button.primary")
    page.wait_for_timeout(800)
    print("settings invalid toast:", page.inner_text("#toast") if page.is_visible("#toast") else "(none)")
    page.fill("input[name=fixed_recipients]", "")
    page.fill("input[name=retention_audio_days]", "30")
    page.click("#sf button.primary")
    page.wait_for_timeout(800)
    print("settings ok toast:", page.inner_text("#toast") if page.is_visible("#toast") else "(none)")
    shot(page, "j8_01_settings")

pages = ["#/", "#/meetings", f"#/meetings/{(AT / 'mid.txt').read_text()}", "#/rooms", "#/devices", "#/people", "#/users", "#/settings", "#/audit"]
for label, kw in [("desk", dict(width=1360, height=900)), ("phone_dark", dict(width=390, height=844, dark=True))]:
    with browser(**kw) as page:
        for h in pages:
            page.goto(BASE + "/" + h)
            page.wait_for_timeout(1200)
            name = h.strip("#/").split("/")[0] or "home"
            shot(page, f"j11_{label}_{name}")
            over = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            if over:
                print(f"{label} {h}: horizontal overflow ({page.evaluate('document.documentElement.scrollWidth')}px)")
report()
