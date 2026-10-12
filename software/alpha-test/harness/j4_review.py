"""J4: review actions - export, send, speakers, regenerate, title edit, search."""
from ui import AT, BASE, browser, report, shot

mid = (AT / "mid.txt").read_text()
with browser() as page:
    page.goto(BASE + f"/#/meetings/{mid}")
    page.wait_for_selector("#send")
    with page.expect_download() as dl:
        page.click("a[href$='export.md']")
    d = dl.value
    print("download name:", d.suggested_filename, "| first line:", d.path().read_text(encoding="utf-8").splitlines()[0][:70])

    page.click("#send")
    page.click(".modal button.primary")
    page.wait_for_timeout(2500)
    print("send toast:", page.inner_text("#toast"))

    # name speakers: S1 new name with remember but no consent (must be refused), then with consent
    page.click('.tabs button[data-tab="speakers"]')
    form = page.locator(".spkform").first
    form.locator("input[name=name]").fill("Мария 👩‍💼 <b>bold</b>")
    form.locator("input[name=remember]").check()
    form.locator("button").click()
    page.wait_for_timeout(1000)
    print("remember w/o consent:", page.inner_text("#toast"))
    page.click('.tabs button[data-tab="speakers"]')
    form = page.locator(".spkform").first
    form.locator("input[name=name]").fill("Мария")
    form.locator("input[name=remember]").check()
    form.locator("input[name=consent]").check()
    form.locator("button").click()
    page.wait_for_timeout(1500)
    print("remember with consent:", page.inner_text("#toast"))
    page.click('.tabs button[data-tab="speakers"]')
    page.wait_for_timeout(500)
    shot(page, "j4_01_speakers")
    page.click("#regen")
    page.wait_for_timeout(1000)
    print("regen toast:", page.inner_text("#toast"))
    page.wait_for_timeout(25000)
    page.goto(BASE + f"/#/meetings/{mid}")
    page.wait_for_timeout(1500)
    print("status after regen:", page.inner_text(".badge"))
    page.click('.tabs button[data-tab="transcript"]')
    shot(page, "j4_02_transcript")

    # search
    page.goto(BASE + "/#/meetings")
    page.wait_for_selector("#filt")
    page.fill("#filt input[name=q]", "Румъния")
    page.click("#filt button")
    page.wait_for_timeout(1000)
    print("search Румъния:", page.inner_text("p.muted.small"))
    page.fill("#filt input[name=q]", "%' OR 1=1 --")
    page.click("#filt button")
    page.wait_for_timeout(1000)
    print("search injection:", page.inner_text("p.muted.small"))
    shot(page, "j4_03_search")
report()
