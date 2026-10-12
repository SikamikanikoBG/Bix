"""Portal: room calendar, a booking made in the browser, the face setting; the minutes of
the meeting from d1_day.py carry the ⭐ and the calendar title/attendees."""
import time

from api import admin
from ui import BASE, browser, cred, report, shot

a = admin()
room = next(r for r in a.get("/api/rooms").json() if r["name"] == "Зала Витоша")
email, pw = cred()
with browser(fresh=True) as page:
    page.goto(BASE + "/#/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("form button")
    page.wait_for_timeout(1500)
    page.goto(BASE + "/#/rooms")
    page.wait_for_timeout(1000)
    shot(page, "d2_01_rooms")
    page.click(f'a[href="#/rooms/{room["id"]}"]')
    page.wait_for_selector("#book")
    shot(page, "d2_02_room_calendar")
    page.click("#book")
    page.fill("#bf input[name=title]", "Среща с доставчик")
    page.fill("#bf textarea[name=attendees]", "Георги Тодоров <georgi@example.com>\nДесислава")
    shot(page, "d2_03_booking_dialog", full=False)
    page.click("#bf button.primary")
    page.wait_for_timeout(1200)
    print("after booking:", page.locator("table b").all_inner_texts())
    page.click("[data-edit]") if page.locator("[data-edit]").count() else None
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("select[name=face_style]")
    print("face setting:", page.locator("select[name=face_style]").input_value())
report()

# the minutes of the d1 meeting: calendar title/attendees and the ⭐ went in
ms = a.get("/api/meetings?limit=5").json()["items"]
m = next((x for x in ms if x["title"] == "Седмичен преглед"), None)
if m:
    for _ in range(60):
        full = a.get(f"/api/meetings/{m['id']}").json()
        if full["status"] in ("done", "failed"):
            break
        time.sleep(3)
    print("meeting:", full["status"], full["title"], "| attendees:", [x.get("name") or x.get("email") for x in full.get("attendees", [])])
    print("steps:", {k: v.get("result") for k, v in (full.get("steps") or {}).items() if k in ("calendar", "deliver")})
    print("notes:", full.get("notes"))
