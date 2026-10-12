"""Room panel API (device side)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from bix.common.events import Bus
from bix.device.panel import create_panel


class StubClient:
    def __init__(self) -> None:
        self.enrolled = False
        self.calls: list[tuple[str, str]] = []

    def enroll(self, server: str, code: str) -> dict:
        self.calls.append((server, code))
        self.enrolled = True
        return {"org": "Тест", "room": "Витоша"}


def make_panel(simulator=False):
    agent = SimpleNamespace(
        cfg=SimpleNamespace(panel=SimpleNamespace(host="127.0.0.1", simulator=simulator)),
        client=StubClient(),
        bus=Bus(),
        live_lines=[],
        _publish_state=lambda: None,
        sync_now=lambda: None,
        status=lambda: {"state": "idle"},
    )
    return agent, TestClient(create_panel(agent), base_url="http://127.0.0.1:8711")


def test_enroll_from_panel():
    agent, c = make_panel()
    r = c.post("/api/enroll", json={"server": "https://bix.example.eu", "code": "ABCD-EFGH-JKLM"}, headers={"X-Bix": "1"})
    assert r.status_code == 200, r.text  # regression: body was parsed as a query parameter (422)
    assert agent.client.calls == [("https://bix.example.eu", "ABCD-EFGH-JKLM")]
    assert c.post("/api/enroll", json={"server": "x", "code": "y"}, headers={"X-Bix": "1"}).status_code == 409


def test_panel_csrf_and_host_checks():
    _, c = make_panel()
    assert c.post("/api/stop").status_code == 403  # no X-Bix header
    assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 403  # DNS rebinding
    assert c.get("/api/status").status_code == 200


def test_simulator_only_when_enabled():
    """The virtual device (fake radar, fake mute button) is off on a real device."""
    _, c = make_panel(simulator=False)
    assert c.get("/sim").status_code == 404
    assert c.post("/api/sim/presence", json={"present": True}, headers={"X-Bix": "1"}).status_code == 404
    assert c.get("/").headers["x-frame-options"] == "DENY"
    agent, c = make_panel(simulator=True)
    seen = []
    agent._on_presence = seen.append
    assert c.get("/sim").status_code == 200
    assert "cdn.jsdelivr.net" in c.get("/sim").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in c.get("/").headers["content-security-policy"]  # the screen itself stays strict
    assert c.get("/sim/../panel.py").status_code == 404
    assert c.get("/sim/secret.json").status_code == 404
    assert c.post("/api/sim/presence", json={"present": False}, headers={"X-Bix": "1"}).status_code == 200 and seen == [False]
    assert c.post("/api/sim/presence", json={"present": True}).status_code == 403  # CSRF header still required
