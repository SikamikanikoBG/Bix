"""J2: room, enrollment code in portal, enroll through the device panel, devices page."""
from ui import AT, BASE, PANEL, browser, report, shot

with browser() as page:
    page.goto(BASE + "/#/rooms")
    page.wait_for_selector("#add")
    page.click("#add")
    page.fill("input[name=name]", "Витоша <img src=x onerror=alert(1)>")
    page.fill("input[name=location]", "София, ет. 3")
    page.fill("input[name=recipients]", "team@alpha.test, bad-address")
    page.click(".modal button.primary")
    page.wait_for_timeout(800)
    print("bad recipient toast:", page.inner_text("#toast"))
    page.fill("input[name=recipients]", "team@alpha.test")
    page.click(".modal button.primary")
    page.wait_for_timeout(800)
    shot(page, "j2_01_rooms")

    page.goto(BASE + "/#/devices")
    page.wait_for_selector("#code")
    shot(page, "j2_02_devices_empty")
    page.click("#code")
    page.select_option(".modal select[name=room_id]", index=1)
    page.click(".modal button.primary")
    page.wait_for_selector(".modal .code")
    code = page.inner_text(".modal .code").strip()
    shot(page, "j2_03_code")
    print("code:", code)
    (AT / "code.txt").write_text(code)

with browser(state_file=AT / "panel_state.json") as panel:
    panel.goto(PANEL)
    panel.wait_for_timeout(1000)
    shot(panel, "j2_04_panel_unenrolled")
    # lower-case code without dashes, URL with trailing slash: should still work
    panel.fill("#enServer", BASE + "/")
    panel.fill("#enCode", code.lower().replace("-", ""))
    panel.click("#enroll button")
    panel.wait_for_timeout(2500)
    shot(panel, "j2_05_panel_enrolled")
    print("panel net chip:", panel.inner_text("#net"), "| room:", panel.inner_text("#room"))

with browser() as page:
    page.goto(BASE + "/#/devices")
    page.wait_for_timeout(1500)
    shot(page, "j2_06_devices")
    print(page.inner_text("table"))
report()
