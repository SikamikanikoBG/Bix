"""The virtual device: 3D model with the real screen inside, radar, mute button, lights."""
import time

import httpx

from ui import browser, report, shot

DEV = "http://127.0.0.1:8721"
with browser(width=1440, height=860) as page:
    page.goto(DEV + "/sim")
    page.wait_for_selector("#msg", state="hidden", timeout=30000)
    page.wait_for_timeout(2500)
    shot(page, "d3_01_sim_angle", full=False)
    page.click('[data-view="front"]')
    page.wait_for_timeout(1500)
    shot(page, "d3_02_sim_front", full=False)
    # press "Започни" on the screen inside the model (it is the real panel in an iframe)
    fr = page.frame_locator("iframe")
    btn = fr.locator("#btnStartEvent:visible, #btnStart:visible").first
    btn.click()
    page.wait_for_timeout(2500)
    page.fill("#doa", "45")
    page.dispatch_event("#doa", "input")
    page.click('[data-view="angle"]')
    page.wait_for_timeout(3000)
    shot(page, "d3_03_sim_recording", full=False)
    page.click("#mute")
    page.wait_for_timeout(1500)
    page.click('[data-view="top"]')
    page.wait_for_timeout(1800)
    shot(page, "d3_04_sim_muted_top", full=False)
    page.click("#mute")
    page.click('[data-view="front"]')
    page.wait_for_timeout(1500)
    fr.locator("#btnStop").click()
    page.wait_for_timeout(2500)
    page.click("#pOut")
    page.wait_for_timeout(2500)
    shot(page, "d3_05_sim_empty_room", full=False)
    page.click("#pIn")
    page.wait_for_timeout(1200)
    shot(page, "d3_06_sim_someone_enters", full=False)
report()
print(httpx.get(DEV + "/api/status", headers={"X-Bix": "1"}).json()["state"])
