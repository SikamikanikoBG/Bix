"""J1: first run, setup validation, login, logout."""
import json

from ui import AT, BASE, browser, report, shot

with browser(fresh=True) as page:
    page.goto(BASE)
    page.wait_for_selector("form#f")
    shot(page, "j1_01_setup")
    # short password: browser minlength blocks it; bypass to test server validation
    page.fill("input[name=org_name]", "Алфа ООД <script>alert(1)</script>")
    page.fill("input[name=name]", "Тест Админ")
    page.fill("input[name=email]", "admin@alpha.test")
    page.eval_on_selector("input[name=password]", "e => e.removeAttribute('minlength')")
    page.fill("input[name=password]", "short")
    page.click("button.primary")
    page.wait_for_timeout(800)
    shot(page, "j1_02_short_pw")
    print("after short pw hash:", page.url, "| toast:", page.inner_text("#toast") if page.is_visible("#toast") else "-")
    page.fill("input[name=password]", "alpha-test-pass-123")
    page.click("button.primary")
    page.wait_for_timeout(1500)
    print("after setup:", page.url)
    shot(page, "j1_03_home")
    (AT / "cred.json").write_text(json.dumps({"email": "admin@alpha.test", "password": "alpha-test-pass-123"}))
    # logout
    page.click("#logout")
    page.wait_for_timeout(800)
    print("after logout:", page.url)
    shot(page, "j1_04_login")
    page.fill("input[name=email]", "admin@alpha.test")
    page.fill("input[name=password]", "wrong-password-xx")
    page.click("button.primary")
    page.wait_for_timeout(800)
    print("wrong pw toast:", page.inner_text("#toast") if page.is_visible("#toast") else "-")
    page.fill("input[name=password]", "alpha-test-pass-123")
    page.click("button.primary")
    page.wait_for_timeout(1200)
    print("after login:", page.url, page.inner_text("#meName"))
report()
