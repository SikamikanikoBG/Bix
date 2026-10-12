"""J5: upload recordings of every kind through the portal UI."""
import time

from ui import AT, BASE, browser, report, shot

files = sorted((AT / "files").iterdir())
with browser() as page:
    for f in files:
        page.goto(BASE + "/#/meetings")
        page.wait_for_selector("#up")
        page.click("#up")
        page.set_input_files(".modal input[type=file]", str(f))
        t0 = time.time()
        page.click(".modal button.primary")
        page.wait_for_timeout(2500)
        modal_open = page.locator(".modal").count() > 0
        toast = page.inner_text("#toast") if page.is_visible("#toast") else ""
        print(f"{f.name:40} {time.time()-t0:4.1f}s  url={page.url.split('#')[1][:20]:20} modal={modal_open} toast={toast}")
        if modal_open:
            shot(page, f"j5_err_{f.stem[:10]}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            print("   modal closed by Esc:", page.locator(".modal").count() == 0)
        if f.name.startswith("Среща"):
            shot(page, "j5_01_uploaded")
            (AT / "upload_mid.txt").write_text(page.url.split("/")[-1])
    time.sleep(40)
    page.goto(BASE + "/#/meetings")
    page.wait_for_selector("table")
    page.wait_for_timeout(800)
    shot(page, "j5_02_list")
    print(page.inner_text("table"))
report()
