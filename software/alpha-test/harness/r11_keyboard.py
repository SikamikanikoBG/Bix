"""R11: keyboard only - log in, reach a meeting; start/stop in the panel."""
import httpx

from ui import AT, BASE, PANEL, browser, cred, report, shot

email, pw = cred()
with browser(fresh=True, state_file=AT / "kb_state.json") as page:
    page.goto(BASE)
    page.wait_for_selector("input[name=email]")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.name || document.activeElement.textContent.trim()")
    print("first Tab lands on:", focused)
    page.focus("input[name=email]")
    page.keyboard.type(email)
    page.keyboard.press("Tab")
    page.keyboard.type(pw)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    print("logged in by keyboard:", page.url.endswith("#/"))
    # tab to the "Срещи" link and open it with Enter
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.textContent.trim()") == "Срещи":
            break
    page.keyboard.press("Enter")
    page.wait_for_timeout(1000)
    print("navigated by keyboard to:", page.url.split("#")[1])
    # meeting rows are <tr> with click handlers - can they be reached by keyboard?
    reach = False
    for _ in range(25):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.className") == "rowlink":
            reach = True
            break
    print("meeting row reachable with Tab:", reach)
    if reach:
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        print("Enter opens:", page.url.split("#")[1][:20])
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    print("focus outline style:", outline)

with browser(state_file=AT / "panel_state.json") as panel:
    panel.goto(PANEL)
    panel.wait_for_selector("#btnStart")
    for _ in range(10):
        panel.keyboard.press("Tab")
        if panel.evaluate("document.activeElement.id") == "btnStart":
            break
    panel.keyboard.press("Enter")
    panel.wait_for_timeout(2500)
    print("panel start by keyboard:", httpx.get(PANEL + "/api/status").json()["state"])
    for _ in range(10):
        panel.keyboard.press("Tab")
        if panel.evaluate("document.activeElement.id") == "btnStop":
            break
    panel.keyboard.press("Enter")
    panel.wait_for_timeout(2500)
    print("panel stop by keyboard:", httpx.get(PANEL + "/api/status").json()["state"])
report()
