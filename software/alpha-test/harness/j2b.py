"""J2b: enroll through the device panel with the existing code, then devices page."""
from ui import AT, BASE, PANEL, browser, report, shot

code = (AT / "code.txt").read_text().strip()
with browser(state_file=AT / "panel_state.json") as panel:
    panel.goto(PANEL)
    panel.wait_for_timeout(1000)
    # first a wrong code: the error must stay visible
    panel.fill("#enServer", BASE + "/")
    panel.fill("#enCode", "AAAA-BBBB-CCCC")
    panel.click("#enroll button")
    panel.wait_for_timeout(1500)
    shot(panel, "j2b_01_wrong_code")
    print("error shown:", panel.is_visible("#err"), panel.inner_text("#err") if panel.is_visible("#err") else "")
    panel.fill("#enCode", code.lower().replace("-", ""))
    panel.click("#enroll button")
    panel.wait_for_timeout(1500)
    shot(panel, "j2b_02_enrolled")
    print("panel net:", panel.inner_text("#net"), "| room:", panel.inner_text("#room"), "| err visible:", panel.is_visible("#err"))

with browser() as page:
    page.goto(BASE + "/#/devices")
    page.wait_for_selector("table")
    page.wait_for_timeout(500)
    shot(page, "j2b_03_devices")
    print(page.inner_text("table"))
    page.goto(BASE + "/#/rooms")
    page.wait_for_selector("table")
    shot(page, "j2b_04_rooms")
report()
