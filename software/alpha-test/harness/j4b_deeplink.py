"""J4b: e-mail deep link (#L10), logged in and logged out."""
from ui import AT, BASE, browser, cred, report, shot

mid = (AT / "mid.txt").read_text()
link = f"{BASE}/#/meetings/{mid}#L10"
with browser() as page:
    page.goto(link)
    page.wait_for_timeout(2000)
    hl = page.locator(".transcript p.hl")
    print("logged in → highlighted:", hl.inner_text().replace("\n", " ") if hl.count() else "NONE")
    shot(page, "j4b_01_deeplink", full=False)

with browser(fresh=True, state_file=AT / "anon_state.json") as page:
    page.goto(link)
    page.wait_for_timeout(1500)
    print("logged out → url:", page.url)
    email, pw = cred()
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("button.primary")
    page.wait_for_timeout(2000)
    print("after login → url:", page.url)
    hl = page.locator(".transcript p.hl")
    print("after login → highlighted:", hl.inner_text().replace("\n", " ") if hl.count() else "NONE")
report()
