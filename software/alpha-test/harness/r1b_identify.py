"""R1b: 'identify' from the portal is visible on the room panel."""
from api import admin
from ui import AT, PANEL, browser, report, shot

a = admin()
dev = a.get("/api/devices").json()[0]
with browser(state_file=AT / "panel_state.json") as panel:
    panel.goto(PANEL)
    panel.wait_for_selector("#btnStart")
    a.post(f"/api/devices/{dev['id']}/command", json={"type": "identify"})
    panel.wait_for_selector("#identify:not([hidden])", timeout=20000)
    shot(panel, "r1b_identify", full=False)
    print("identify visible:", panel.is_visible("#identify"), "| text:", panel.inner_text("#identify").replace("\n", " / "))
    panel.wait_for_timeout(5500)
    print("hidden again after 5 s:", not panel.is_visible("#identify"))
report()
