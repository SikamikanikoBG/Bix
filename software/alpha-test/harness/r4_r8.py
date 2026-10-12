"""R4 edit/delete flows, R5 member journey, R7 audit, R8 reprocess dialog — in the browser."""
import time

from api import AT, admin, login
from ui import BASE, browser, report, shot

a = admin()
meetings = a.get("/api/meetings").json()["items"]
plovdiv = next(m for m in meetings if m["room"].startswith("Пловдив"))
vitosha = next(m for m in meetings if m["room"].startswith("Витоша"))

# --- R4: rooms ---------------------------------------------------------------
with browser() as page:
    page.goto(BASE + "/#/rooms")
    page.wait_for_selector("[data-edit]")
    page.click("[data-edit]")  # first room (Витоша - has a device)
    page.fill(".modal input[name=location]", "София, ет. 4 " + "x" * 300)
    page.click(".modal button.primary")
    page.wait_for_timeout(800)
    shot(page, "r4_01_room_long")
    page.click("[data-edit]")
    page.click("#rdel")
    page.wait_for_timeout(800)
    print("delete room with device:", page.inner_text("#toast"))

    # --- R4: meeting title edit (prompt dialog) + 300-char title, delete meeting ---
    page.on("dialog", lambda d: d.accept("Нов заглавие 🚀 " + "я" * 300) if d.type == "prompt" else d.accept())
    page.goto(BASE + f"/#/meetings/{vitosha['id']}")
    page.wait_for_selector("#title")
    page.click("#title")
    page.wait_for_timeout(1000)
    t = page.inner_text("#title")
    print("title after edit: len", len(t), "| starts:", t[:20])
    shot(page, "r4_02_long_title", full=False)

    # --- R8: reprocess dialog while processing -----------------------------------
    page.click("#more")
    page.click("[data-step=minutes]")
    page.wait_for_timeout(500)
    page.click("#more")
    page.click("[data-step=speakers]")
    page.wait_for_timeout(800)
    print("reprocess while queued/processing:", page.inner_text("#toast"))

    # --- R5: member who attended Plovdiv meeting -------------------------------------
    p = a.post("/api/people", json={"name": "Георги Иванов", "email": "member2@alpha.test"}).json()
    a.post(f"/api/meetings/{plovdiv['id']}/speakers", json={"label": "S1", "person_id": p["id"]})
    a.post("/api/users", json={"email": "member2@alpha.test", "name": "Георги", "role": "member", "password": "member2-pass-123"})

with browser(fresh=True, state_file=AT / "member_state.json") as page:
    page.goto(BASE)
    page.fill("input[name=email]", "member2@alpha.test")
    page.fill("input[name=password]", "member2-pass-123")
    page.click("button.primary")
    page.wait_for_timeout(1500)
    shot(page, "r5_01_member_home")
    nav = [x for x in page.locator("#nav a").all_inner_texts() if x]
    visible_nav = [n.inner_text() for n in page.locator("#nav a").all() if n.is_visible()]
    print("member nav:", visible_nav)
    page.goto(BASE + "/#/meetings")
    page.wait_for_timeout(1000)
    print("member sees:", page.locator("tr.click").count(), "meeting(s):", page.locator("tr.click td:first-child a.rowlink").all_inner_texts())
    page.goto(BASE + f"/#/meetings/{vitosha['id']}")
    page.wait_for_timeout(1000)
    print("member opens other meeting:", page.inner_text("#view")[:80].replace("\n", " "))
    page.goto(BASE + "/#/settings")
    page.wait_for_timeout(1200)
    shot(page, "r5_02_member_settings")
    print("member settings form disabled:", page.locator("#sf input[disabled]").count() > 0, "| save button:", page.locator("#sf button.primary").count())
    # member uploads
    page.goto(BASE + "/#/meetings")
    page.click("#up")
    page.set_input_files(".modal input[type=file]", str(AT / "tm" / "meeting.wav"))
    page.fill(".modal input[name=title]", "Качено от член")
    page.click(".modal button.primary")
    page.wait_for_timeout(3000)
    print("member upload →", page.url.split("#")[1][:25])
    # password change in the UI; the old session must end
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#pw")
    page.fill("#pw input[name=current]", "member2-pass-123")
    page.fill("#pw input[name=new]", "member2-new-pass-456")
    page.click("#pw button")
    page.wait_for_timeout(1000)
    print("password change toast:", page.inner_text("#toast"))
    page.goto(BASE + "/#/people")
    page.wait_for_timeout(1000)
    print("still logged in on this tab after change:", "login" not in page.url)
old = login.__wrapped__ if hasattr(login, "__wrapped__") else None
try:
    login("member2@alpha.test", "member2-pass-123")
    print("old password still works: True (BAD)")
except Exception:
    print("old password rejected: True")

# --- R4: disable user in the UI → their session ends --------------------------------
m2 = login("member2@alpha.test", "member2-new-pass-456")
with browser() as page:
    page.goto(BASE + "/#/users")
    page.wait_for_selector("[data-active]")
    row = page.locator("tr", has_text="member2@alpha.test")
    row.locator("[data-active]").click()
    page.wait_for_timeout(800)
    print("disabled member session:", m2.get("/api/meetings").status_code)
    row = page.locator("tr", has_text="member2@alpha.test")
    row.locator("[data-active]").click()
    page.wait_for_timeout(800)
    shot(page, "r4_03_users")

    # --- R4: person erase in UI, meeting delete in UI ----------------------------------
    page.goto(BASE + "/#/people")
    page.wait_for_selector("[data-erase]")
    n_before = page.locator("[data-erase]").count()
    page.locator("tr", has_text="Георги Иванов").locator("[data-erase]").click()
    page.wait_for_timeout(1200)
    print("erase person toast:", page.inner_text("#toast"), "| rows", n_before, "→", page.locator("[data-erase]").count())
    page.goto(BASE + f"/#/meetings/{plovdiv['id']}")
    page.wait_for_selector("#more")
    page.click("#more")
    page.click("#del")
    page.wait_for_timeout(1500)
    print("after delete meeting →", page.url.split("#")[1], "| API:", a.get(f"/api/meetings/{plovdiv['id']}").status_code)

    # --- R7: audit page ---------------------------------------------------------------
    page.goto(BASE + "/#/audit")
    page.wait_for_selector("table")
    total = page.inner_text(".row .muted.small")
    page.fill("#af input[name=action]", "meeting.delete")
    page.click("#af button")
    page.wait_for_timeout(1000)
    rows = page.locator("tbody tr").all_inner_texts()
    print("audit total:", total, "| meeting.delete rows:", len(rows), rows[0].replace("\t", " | ")[:120] if rows else "")
    shot(page, "r7_audit")
report()
