"""Face on the 7" Touch Display 2: SDL2 on KMS, no browser.

Shows eyes, the recording notice (AI Act art. 50), timer and big touch
buttons. Run with: bix device --face   (needs: pip install "bix[face]")
On the Pi without a desktop set SDL_VIDEODRIVER=kmsdrm (the systemd unit does).
"""

from __future__ import annotations

import math
import os
import time

from bix.device.agent import Agent

BG = (14, 17, 22)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
ACCENT = (58, 160, 255)
REC = (255, 77, 79)
PANEL = (22, 27, 34)


def run_face(agent: Agent) -> None:
    import pygame

    os.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")
    pygame.init()
    fullscreen = os.environ.get("BIX_FACE_WINDOWED") != "1"
    screen = pygame.display.set_mode((0, 0) if fullscreen else (1280, 720), pygame.FULLSCREEN if fullscreen else 0)
    pygame.display.set_caption("Bix")
    pygame.mouse.set_visible(not fullscreen)
    w, h = screen.get_size()
    big = pygame.font.SysFont("dejavusans,segoeui,arial", max(36, h // 9))
    mid = pygame.font.SysFont("dejavusans,segoeui,arial", max(22, h // 22))
    small = pygame.font.SysFont("dejavusans,segoeui,arial", max(16, h // 34))
    clock = pygame.time.Clock()
    buttons: dict[str, pygame.Rect] = {}
    last_touch = time.time()
    blink_at = time.time() + 4

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE and not fullscreen):
                return
            if ev.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
                last_touch = time.time()
                pos = ev.pos if ev.type == pygame.MOUSEBUTTONUP else (int(ev.x * w), int(ev.y * h))
                for name, rect in buttons.items():
                    if rect.collidepoint(pos):
                        _press(agent, name)
        st = agent.status()
        rec = st["state"] == "recording"
        # Screen sleeps when nobody is in the room (radar) or nothing happened for 10 min.
        asleep = not rec and (st["present"] is False or (st["present"] is None and time.time() - last_touch > 600))
        screen.fill(BG)
        if asleep:
            pygame.display.flip()
            clock.tick(5)
            continue

        # eyes
        colour = MUTED if st["muted"] else (REC if rec else ACCENT)
        level = max(0.0, min(1.0, (st["level_db"] + 70) / 60))
        eye_h = int(h * 0.18 * (0.75 + 0.35 * level))
        if time.time() > blink_at:
            eye_h = max(6, eye_h // 10)
            if time.time() > blink_at + 0.12:
                blink_at = time.time() + 3 + 4 * abs(math.sin(time.time()))
        if st["muted"]:
            eye_h = max(8, h // 50)
        ew = int(w * 0.07)
        cy = int(h * 0.28)
        for cx in (w // 2 - int(w * 0.09), w // 2 + int(w * 0.09)):
            pygame.draw.rect(screen, colour, pygame.Rect(cx - ew // 2, cy - eye_h // 2, ew, eye_h), border_radius=ew // 2)

        # header
        room = st["policy"].get("room") or "Bix"
        _text(screen, small, room, (24, 18), MUTED)
        net = "онлайн" if st["online"] else ("без връзка" if st["enrolled"] else "не е регистрирано")
        _text(screen, small, net, (w - 24, 18), MUTED, anchor="topright")

        # timer and notice
        if rec:
            _text(screen, big, _fmt(st["elapsed_s"]), (w // 2, int(h * 0.5)), FG, anchor="center")
            notice = "Записът е на пауза." if st["muted"] else st["policy"].get("notice_text", "")
            _wrap(screen, small, notice, pygame.Rect(int(w * 0.1), int(h * 0.58), int(w * 0.8), int(h * 0.12)), FG)
        else:
            _text(screen, mid, "Докоснете, за да започне среща", (w // 2, int(h * 0.52)), MUTED, anchor="center")
        if st["error"]:
            _wrap(screen, small, st["error"], pygame.Rect(int(w * 0.1), int(h * 0.66), int(w * 0.8), int(h * 0.08)), REC)

        # buttons
        buttons.clear()
        bh, gap = int(h * 0.14), int(w * 0.02)
        names = [("stop", "Край", REC), ("mute", "Продължи" if st["muted"] else "Пауза", PANEL), ("discard", "Не записвай", PANEL)] if rec else [("start", "Започни среща", ACCENT)]
        bw = int((w * 0.86 - gap * (len(names) - 1)) / len(names))
        x = int(w * 0.07)
        for name, label, bg in names:
            r = pygame.Rect(x, h - bh - int(h * 0.06), bw, bh)
            pygame.draw.rect(screen, bg, r, border_radius=18)
            _text(screen, mid, label, r.center, FG if bg != ACCENT else BG, anchor="center")
            buttons[name] = r
            x += bw + gap
        pygame.display.flip()
        clock.tick(20)


def _press(agent: Agent, name: str) -> None:
    try:
        if name == "start":
            agent.start_meeting("touch")
        elif name == "stop":
            agent.stop_meeting("touch")
        elif name == "mute":
            agent.set_muted(not agent.muted)
        elif name == "discard":
            agent.discard_meeting()
    except Exception:
        pass


def _fmt(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, x = divmod(rem, 60)
    return f"{h}:{m:02}:{x:02}" if h else f"{m:02}:{x:02}"


def _text(screen, font, text, pos, colour, anchor="topleft") -> None:
    surf = font.render(text, True, colour)
    rect = surf.get_rect(**{anchor: pos})
    screen.blit(surf, rect)


def _wrap(screen, font, text, rect, colour) -> None:
    words, line, y = text.split(), "", rect.top
    for word in words:
        test = f"{line} {word}".strip()
        if font.size(test)[0] > rect.width and line:
            _text(screen, font, line, (rect.centerx, y), colour, anchor="midtop")
            y += font.get_linesize()
            line = word
        else:
            line = test
    if line:
        _text(screen, font, line, (rect.centerx, y), colour, anchor="midtop")
