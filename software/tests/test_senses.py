"""What the face reacts to: knocks and bangs (mic), distance (radar), the simulator's buttons."""

from __future__ import annotations

import numpy as np

from bix.common.vad import level_db
from bix.device.hw.ld2410 import Presence, nearest_cm
from bix.device.sounds import SoundEvents
from tests.test_panel import make_panel

SR, FRAME = 16000, 320
rng = np.random.default_rng(7)


def quiet(n=1):
    return [rng.normal(0, 0.001, FRAME).astype(np.float32) for _ in range(n)]


def tone(hz, amp, frames, decay=0.0):
    t = np.arange(FRAME * frames) / SR
    x = amp * np.sin(2 * np.pi * hz * t) * np.exp(-decay * t)
    return [(x[i * FRAME:(i + 1) * FRAME] + rng.normal(0, 0.001, FRAME)).astype(np.float32) for i in range(frames)]


def run(frames, speech=None):
    s, out = SoundEvents(), []
    for i, f in enumerate(frames):
        out += s.feed(f, level_db(f), bool(speech and speech(i)))
    return out


def test_knock_knock_is_one_event_with_the_count():
    frames = quiet(50) + tone(150, 0.05, 2, decay=40) + quiet(15) + tone(150, 0.05, 2, decay=40) + quiet(80)
    out = run(frames)
    assert [(e.kind, e.count) for e in out] == [("knock", 2)]


def test_a_bang_startles_once():
    frames = quiet(50) + tone(400, 0.6, 15) + quiet(80)
    out = run(frames)
    assert [e.kind for e in out] == ["bang"]


def test_talking_is_not_knocking():
    # a long voiced sound, and a short one right after speech: neither is a knock
    frames = quiet(50) + tone(220, 0.05, 20) + quiet(5) + tone(220, 0.05, 2, decay=40) + quiet(80)
    out = run(frames, speech=lambda i: 50 <= i < 72)
    assert out == []


def test_a_click_is_not_a_knock():
    frames = quiet(50) + tone(4000, 0.05, 2, decay=40) + quiet(80)
    assert run(frames) == []


def test_radar_nearest_target_and_throttled_distance():
    assert nearest_cm({"state": 0, "moving_cm": 100, "static_cm": 50}) is None
    assert nearest_cm({"state": 1, "moving_cm": 100, "static_cm": 50}) == 100
    assert nearest_cm({"state": 3, "moving_cm": 100, "static_cm": 50}) == 50
    seen = []
    p = Presence("x", lambda _: None, on_distance=seen.append)
    p.DISTANCE_EVERY_S = 0
    for cm in (200, 195, 180, 179, 60):
        p._distance({"state": 1, "moving_cm": cm, "static_cm": 0})
    p._distance({"state": 0, "moving_cm": 0, "static_cm": 0})
    assert seen == [200, 180, 60, None]  # small moves are not news


def test_simulator_sound_and_distance():
    _, c = make_panel(simulator=False)
    assert c.post("/api/sim/sound", json={"kind": "knock"}, headers={"X-Bix": "1"}).status_code == 404
    agent, c = make_panel(simulator=True)
    agent.sim_doa, agent.present = 30, None
    seen, dist = [], []
    agent._on_presence = lambda p: setattr(agent, "present", p)
    agent._on_distance = dist.append
    agent.bus.on(lambda ev: seen.append((ev.type, ev.data)))
    h = {"X-Bix": "1"}
    assert c.post("/api/sim/sound", json={"kind": "knock", "count": 2}, headers=h).status_code == 200
    assert seen[-1] == ("sound", {"kind": "knock", "count": 2, "db": -35.0, "doa": 30})
    assert c.post("/api/sim/sound", json={"kind": "scream"}, headers=h).status_code == 422
    assert c.post("/api/sim/distance", json={"cm": 80}, headers=h).status_code == 200
    assert agent.present is True and dist == [80]  # somebody near: the room is not empty
