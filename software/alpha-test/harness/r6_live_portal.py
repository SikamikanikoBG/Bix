"""R6: the portal follows a running meeting without reloads (SSE)."""
import time

import httpx

from ui import BASE, PANEL, browser, report, shot

X = {"X-Bix": "1"}
with browser() as page:
    page.goto(BASE + "/#/meetings")
    page.wait_for_selector("#filt")
    rows0 = page.locator("tr.click").count()
    httpx.post(PANEL + "/api/start", headers=X)
    t0 = time.time()
    while time.time() - t0 < 20 and page.locator("tr.click").count() == rows0:
        page.wait_for_timeout(500)
    print(f"new meeting appears in list without reload: {page.locator('tr.click').count() > rows0} after {time.time()-t0:.1f}s")
    print("its status:", page.locator("tr.click .badge").first.inner_text())
    page.locator("tr.click").first.click()
    page.wait_for_selector(".tabs")
    page.click('.tabs button[data-tab="transcript"]')
    t1 = time.time()
    while time.time() - t1 < 60 and page.locator(".transcript p").count() == 0:
        page.wait_for_timeout(1000)
    print(f"live text in portal transcript tab: {page.locator('.transcript p').count()} lines after {time.time()-t1:.0f}s")
    shot(page, "r6_live_portal")
    httpx.post(PANEL + "/api/stop", headers=X)
    t2 = time.time()
    status = ""
    while time.time() - t2 < 180:
        status = page.locator(".badge").first.inner_text()
        if status == "готово":
            break
        page.wait_for_timeout(2000)
    print(f"status follows to '{status}' without reload after {time.time()-t2:.0f}s; tab now: {page.locator('.tabs button.on').inner_text()}")
report()
