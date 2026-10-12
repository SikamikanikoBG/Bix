"""J3: core journey - settings → meeting in the panel (live, pause) → portal review → send."""
import re
import time

from ui import AT, BASE, PANEL, browser, report, shot

# 1. enable live text through the settings UI
with browser() as page:
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#sf")
    page.check("input[name=live_transcription]")
    page.check("input[name=send_to][value=room]")
    page.click("#sf button.primary")
    page.wait_for_timeout(800)
    print("settings toast:", page.inner_text("#toast"))
    shot(page, "j3_01_settings")

time.sleep(7)  # device heartbeat (5 s in the test config) picks up the policy

# 2. the meeting, as people in the room see it
with browser(state_file=AT / "panel_state.json") as panel:
    panel.goto(PANEL)
    panel.wait_for_selector("#btnStart")
    print("idle live hint:", panel.inner_text("#liveEmpty"))
    panel.click("#btnStart")
    panel.wait_for_timeout(2000)
    shot(panel, "j3_02_recording")
    print("notice:", panel.inner_text("#notice"))
    panel.wait_for_timeout(45000)
    shot(panel, "j3_03_live")
    print("live lines after 45 s:", panel.locator("#live p:not(#liveEmpty)").count())
    panel.click("#btnMute")
    panel.wait_for_timeout(1500)
    shot(panel, "j3_04_paused")
    print("paused notice:", panel.inner_text("#notice"), "| button:", panel.inner_text("#btnMute"))
    panel.wait_for_timeout(5000)
    panel.click("#btnMute")
    panel.wait_for_timeout(40000)
    print("live lines before stop:", panel.locator("#live p:not(#liveEmpty)").count())
    panel.click("#btnStop")
    panel.wait_for_timeout(2000)
    shot(panel, "j3_05_stopped")
    print("after stop state hint:", panel.inner_text("#notice"))

# 3. wait for processing and review in the portal
with browser() as page:
    page.goto(BASE + "/#/meetings")
    page.wait_for_selector("tr.click")
    mid = page.get_attribute("tr.click", "data-id")
    for _ in range(60):
        page.goto(BASE + f"/#/meetings/{mid}")
        page.wait_for_timeout(2000)
        status = page.inner_text(".badge")
        if status in ("готово", "грешка"):
            break
        time.sleep(3)
    print("status:", status)
    shot(page, "j3_06_minutes")
    refs = page.locator("[data-ref]")
    if refs.count():
        refs.first.click()
        page.wait_for_timeout(800)
        shot(page, "j3_07_ref_jump", full=False)
        print("highlighted line:", page.inner_text(".transcript p.hl") if page.locator(".transcript p.hl").count() else "NONE")
    page.click('.tabs button[data-tab="speakers"]')
    page.wait_for_timeout(500)
    shot(page, "j3_08_speakers")
    # audio
    dur = page.evaluate("""async () => { const a = document.querySelector('#audio'); if (!a) return -1;
        a.load(); await new Promise(r => { a.onloadedmetadata = r; a.onerror = r; setTimeout(r, 5000); }); return a.duration; }""")
    print("audio duration in browser:", dur)
    # export
    with page.expect_download() as dl:
        page.click("a[href$='export.md']")
    md = dl.value.path().read_text(encoding="utf-8")
    print("export md first line:", md.splitlines()[0][:80], "| bytes", len(md))
    # send to the room recipients (SMTP sink)
    page.click("#send")
    page.click(".modal button.primary")
    page.wait_for_timeout(2500)
    print("send toast:", page.inner_text("#toast"))
    shot(page, "j3_09_sent")
    (AT / "mid.txt").write_text(mid)
report()
