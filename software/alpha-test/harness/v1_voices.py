"""Voice profiles end to end: name speakers by listening/reading in meeting 1,
then meeting 2 (same people, other words) must recognise them by voice."""
import json
import time

from api import AT, admin
from ui import BASE, browser, report, shot

a = admin()
ref = json.loads((AT / "tm" / "reference.json").read_text(encoding="utf-8"))


def upload(name, title):
    mid = a.post("/api/meetings/upload", files={"file": (name, (AT / "tm" / name).read_bytes(), "audio/wav")}, data={"title": title}, timeout=300).json()["id"]
    for _ in range(120):
        m = a.get(f"/api/meetings/{mid}").json()
        if m["status"] in ("done", "failed"):
            return m
        time.sleep(3)
    return m


def truth(text):
    """Who really said this (from the script) - what a person in the room would know."""
    words = set(text.lower().replace(",", "").replace(".", "").split())
    best = max(ref, key=lambda t: len(words & set(t["text"].lower().replace(",", "").replace(".", "").split())))
    return best["speaker"]


m1 = upload("part1.wav", "Среща 1")
print("meeting 1:", m1["status"], [s["label"] for s in m1["speakers"]])
with browser() as page:
    page.goto(BASE + f"/#/meetings/{m1['id']}")
    page.wait_for_selector(".tabs")
    shot(page, "v1_01_minutes_hint", full=False)
    print("hint on minutes tab:", page.locator(".card.hint").inner_text() if page.locator(".card.hint").count() else "NONE")
    page.click("[data-goto-tab=speakers]")
    page.wait_for_selector(".spk")
    shot(page, "v1_02_speakers")
    # ▶ plays only that moment
    btn = page.locator("button.play").first
    start, until = float(btn.get_attribute("data-play")), float(btn.get_attribute("data-until"))
    btn.click()
    page.wait_for_timeout(1500)
    playing = page.evaluate("!document.querySelector('#audio').paused")
    page.wait_for_timeout(int((until - start) * 1000) + 1500)
    t = page.evaluate("document.querySelector('#audio').currentTime")
    print(f"▶ {start:.1f}-{until:.1f}s: playing={playing}, stopped at {t:.1f}s, paused={page.evaluate('document.querySelector(`#audio`).paused')}")
    # name each speaker from what they said
    for i in range(page.locator(".spk").count()):
        spk = page.locator(".spk").nth(i)
        quotes = spk.locator(".quote span").all_inner_texts()
        if not quotes:
            continue
        name = truth(" ".join(quotes))
        spk.locator("input[name=name]").fill(name)
        spk.locator("input[name=remember]").check()
        spk.locator("form button").click()
        page.wait_for_timeout(1500)
        print(f"  {spk.locator('b').first.inner_text()} said {quotes[0][:45]!r}… → {name} | {page.inner_text('#toast')[:70]}")
        page.click('.tabs button[data-tab="speakers"]')
        page.wait_for_selector(".spk")
    shot(page, "v1_03_named")

people = a.get("/api/people").json()
print("people with voice:", [(p["name"], p["samples"]) for p in people])

m2 = upload("part2.wav", "Среща 2")
tr = a.get(f"/api/meetings/{m2['id']}/transcript").json()["segments"]
print("meeting 2:", m2["status"])
for s in m2["speakers"]:
    said = " ".join(x["text"] for x in tr if x["speaker"] == s["label"])
    real = truth(said) if said else "?"
    ok = "OK " if s["name"] == real else ("—  " if not s["name"] else "BAD")
    print(f"  {ok} {s['label']}: recognised as {s['name'] or '—':8} ({s['source'] or 'none'} {s['score'] or ''}) | really {real}")
with browser() as page:
    page.goto(BASE + f"/#/meetings/{m2['id']}")
    page.wait_for_selector(".tabs")
    page.click('.tabs button[data-tab="speakers"]')
    page.wait_for_selector(".spk")
    shot(page, "v1_04_meeting2_recognised")
report()
