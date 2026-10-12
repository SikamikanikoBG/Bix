"""R9c: long title and a long transcript in the UI."""
import time

from api import admin
from ui import BASE, browser, report, shot

a = admin()
m = next(x for x in a.get("/api/meetings").json()["items"] if x["title"] == "Латентност")
r = a.patch(f"/api/meetings/{m['id']}", json={"title": "Дълго заглавие " + "я" * 400})
print("400-char title:", r.status_code, "| stored length", len(a.get(f"/api/meetings/{m['id']}").json()["title"]))
mins = a.get(f"/api/meetings/{m['id']}").json()["minutes"] or {}
print("minutes of 19 min:", {k: len(v) for k, v in mins.items() if isinstance(v, list)})
for width in (1360, 390):
    with browser(width=width, height=900) as page:
        t1 = time.time()
        page.goto(BASE + f"/#/meetings/{m['id']}")
        page.wait_for_selector(".tabs")
        page.click('.tabs button[data-tab="transcript"]')
        page.wait_for_selector(".transcript p")
        print(f"{width}px: {page.locator('.transcript p').count()} lines in {time.time()-t1:.1f}s | overflow:",
              page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
        shot(page, f"r9c_long_{width}", full=False)
        page.goto(BASE + "/#/meetings")
        page.wait_for_selector("tr.click")
        print(f"{width}px list overflow:", page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
report()
