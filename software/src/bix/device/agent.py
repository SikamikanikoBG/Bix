"""The device agent: listens, records, buffers, uploads, obeys the server.

Threads (each can fail without stopping the others):
  audio      microphone → VAD → chunks in the spool while a meeting runs
  uplink     spool → server, in order, with retries (store-and-forward)
  heartbeat  state/telemetry up, policy and commands down
  live       live transcript back from the server, if the org enabled it
  presence   LD2410C radar, if fitted
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from collections import deque
from typing import Any

import numpy as np

from bix import __version__
from bix.common import models
from bix.common.events import Bus
from bix.common.vad import Vad, level_db
from bix.device.capture import FRAME, Capture
from bix.device.client import NotEnrolled, ServerClient, platform_string
from bix.device.config import DeviceConfig
from bix.device.hw import make_leds
from bix.device.sounds import SoundEvents
from bix.device.spool import Spool, recover_open

log = logging.getLogger(__name__)
SR = 16000

DEFAULT_POLICY = {
    "org": "",
    "room": "",
    "language": "bg",
    "live_transcription": False,
    "notice_text": "Тази среща се записва. Bix е ИИ асистент, който прави транскрипция и минути.",
    "auto_stop_silence_min": 10,
    "max_meeting_hours": 4,
    "speaker_mode": "voice_profiles",
    "face_style": "pixel",
}
# Before an automatic stop the screen counts down this long, with a "keep going" button.
COUNTDOWN_S = 60


class Agent:
    def __init__(self, cfg: DeviceConfig) -> None:
        self.cfg = cfg
        self.bus = Bus()
        self.client = ServerClient(cfg)
        self.spool = Spool(cfg.spool_dir)
        self.capture = Capture(cfg.audio.input_device)
        self.leds = make_leds(cfg.hardware.leds, cfg.hardware.led_brightness_pct)
        self.policy: dict[str, Any] = dict(DEFAULT_POLICY)
        self._load_policy()
        self._load_agenda()

        self.state = "idle"  # idle | recording | error
        self.muted = False
        self.online = False
        self.error = ""
        self.meeting_id: str | None = None
        self.meeting_started = 0.0
        self.recorded_s = 0.0
        self.speech_s = 0.0
        self.last_speech = 0.0
        self.level = -90.0
        self.speaking = False
        self.present: bool | None = None
        self.distance_cm: int | None = None  # radar: how far the nearest person is
        self._sounds = SoundEvents()
        self._doa: int | None = None
        self.sim_doa: int | None = None
        self._doa_at = 0.0
        self.absent_since: float | None = None
        self.agenda: list[dict] = []  # today's bookings of this room, from the server
        self.last_meeting: dict | None = None
        self.meeting_event: dict | None = None  # the booking the running meeting was started for
        self.ending: dict | None = None  # automatic stop coming: {reason, at, ...}
        self._grace_until = 0.0  # "keep going" after the booked end
        self._extra_s = 0.0  # "keep going" after the maximum length
        self.suggest: dict | None = None  # "Is a meeting starting?" (never records by itself)
        self._suggest_snooze = 0.0
        self._idle_speech: deque[tuple[float, float]] = deque()
        self.recorded_uids: list[str] = []  # bookings already recorded today: not offered again
        self.asking = False
        self._q_frames: list[np.ndarray] | None = None  # a question being asked out loud
        self.live_lines: list[dict] = []
        self._live_after = 0
        self._acked: list[str] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._hb_wake = threading.Event()
        self._chunk: list[np.ndarray] = []
        self._chunk_start = 0.0
        self._vad: Vad | None = None
        self._wake = None

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.cfg.dir.mkdir(parents=True, exist_ok=True)
        for mid in recover_open(self.spool):
            log.warning("meeting %s was interrupted; it will be uploaded as is", mid)
        try:
            models.ensure_vad(self.cfg.models_dir, progress=log.info)
        except Exception as e:
            log.warning("Silero VAD model not available (%s); using energy VAD", e)
        self._vad = Vad(self.cfg.models_dir / models.VAD, self.cfg.audio.energy_threshold_db, max_utterance_s=self.cfg.audio.chunk_max_s)
        self._setup_wake()
        try:
            self.capture.start()
        except Exception as e:
            self._set_error(f"Микрофон: {e}")
        for name, target in (("audio", self._audio_loop), ("uplink", self._uplink_loop), ("heartbeat", self._heartbeat_loop), ("live", self._live_loop)):
            threading.Thread(target=self._guard(target), name=f"bix-{name}", daemon=True).start()
        if self.cfg.hardware.presence == "ld2410":
            try:
                from bix.device.hw.ld2410 import Presence

                Presence(self.cfg.hardware.presence_port, self._on_presence, on_distance=self._on_distance).start()
            except Exception as e:
                log.warning("presence radar: %s", e)
        self.leds.show("idle")
        self._publish_state()

    def sync_now(self) -> None:
        """Heartbeat right away (after enrollment: show the room and online state at once)."""
        self._hb_wake.set()

    def stop_all(self) -> None:
        if self.state == "recording":
            self.stop_meeting("shutdown")
        self._stop.set()
        self._hb_wake.set()
        self.capture.stop()

    def _guard(self, fn):
        def run():
            while not self._stop.is_set():
                try:
                    fn()
                    return
                except Exception:
                    log.exception("%s crashed; restarting in 5 s", fn.__name__)
                    self._stop.wait(5)

        return run

    # --- controls (UI, server commands, wake word, presence) ---------------
    def start_meeting(self, source: str = "button", title: str = "", event_uid: str = "") -> str:
        event = next((e for e in self.agenda if e.get("uid") == event_uid), None) if event_uid else None
        with self._lock:
            if self.state == "recording":
                return self.meeting_id or ""
            if not self.capture.running:
                try:
                    self.capture.start()
                except Exception as e:
                    self._set_error(f"Микрофон: {e}")
                    raise
            mid = uuid.uuid4().hex
            now = time.time()
            if event and not title:
                title = event.get("title", "")
            self.spool.create(mid, now, title, calendar_uid=event["uid"] if event else "")
            self.meeting_event = event
            self.ending, self.suggest = None, None
            self._grace_until, self._extra_s = 0.0, 0.0
            self.meeting_id, self.meeting_started = mid, now
            self.recorded_s = self.speech_s = 0.0
            self.last_speech = now
            self.muted = False
            self.absent_since = None
            self.live_lines, self._live_after = [], 0
            self._chunk, self._chunk_start = [], 0.0
            self.state = "recording"
            self.error = ""
        log.info("meeting %s started (%s)", mid, source)
        self.leds.show("recording")
        self._publish_state()
        return mid

    def stop_meeting(self, reason: str = "button") -> None:
        with self._lock:
            if self.state != "recording" or not self.meeting_id:
                return
            mid = self.meeting_id
            self._flush_chunk()
            self.spool.update(mid, ended_at=time.time())
            self.state = "idle"
            self.meeting_id = None
            self.muted = False
            ev, self.meeting_event, self.ending = self.meeting_event, None, None
            if ev:
                self.recorded_uids = [*self.recorded_uids, ev["uid"]][-30:]
            sm = self.spool.load(mid)
            info = {
                "title": (sm.title if sm else "") or (ev or {}).get("title", ""),
                "duration_s": round(time.time() - self.meeting_started),
                "attendees": (ev or {}).get("attendees", []),
                "organizer": (ev or {}).get("organizer", ""),
                "stars": sum(1 for n in (sm.notes if sm else []) if n.get("kind") == "star"),
            }
        log.info("meeting %s stopped (%s)", mid, reason)
        self.leds.show("idle")
        self.bus.publish("meeting_ended", id=mid, reason=reason, **info)
        self._publish_state()

    def discard_meeting(self) -> None:
        """Somebody objects to the recording: delete it here and on the server."""
        with self._lock:
            if self.state != "recording" or not self.meeting_id:
                return
            mid = self.meeting_id
            self._chunk = []
            self.spool.update(mid, discard=True, ended_at=time.time())
            self.state = "idle"
            self.meeting_id = None
            self.meeting_event, self.ending = None, None
        log.info("meeting %s discarded", mid)
        self.leds.show("idle")
        self.bus.publish("meeting_ended", id=mid, reason="discarded")
        self._publish_state()

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            if self.state != "recording":
                return
            if muted and not self.muted:
                self._flush_chunk()
            self.muted = muted
        self.leds.show("muted" if muted else "recording")
        self._publish_state()

    def mark(self, kind: str = "star", text: str = "") -> dict:
        """⭐ on the screen: this moment matters. Goes into the minutes."""
        with self._lock:
            if self.state != "recording" or not self.meeting_id:
                raise RuntimeError("Няма среща в момента.")
            note = {"t": round(time.time() - self.meeting_started, 1), "kind": kind, "text": text[:500]}
            self.spool.add_note(self.meeting_id, note)
        self.bus.publish("marked", **note)
        return note

    def keep_going(self) -> None:
        """The answer to an automatic stop countdown: the meeting goes on."""
        now = time.time()
        with self._lock:
            if not self.ending:
                return
            reason = self.ending["reason"]
            if reason == "silence":
                self.last_speech = now
            elif reason == "room_empty":
                self.absent_since = now if self.absent_since else None
            elif reason in ("calendar", "next_meeting"):
                self._grace_until = now + 15 * 60
            elif reason == "max_duration":
                self._extra_s += 30 * 60
            self.ending = None
        log.info("automatic stop (%s) cancelled from the room", reason)
        self._publish_state()

    def dismiss_suggest(self) -> None:
        self.suggest = None
        self._suggest_snooze = time.time() + 20 * 60
        self._publish_state()

    # --- questions ("Попитай Bix") -------------------------------------------
    def ask(self, preset: str = "", question: str = "", audio: bytes | None = None) -> dict:
        with self._lock:
            if self.state != "recording" or not self.meeting_id:
                raise RuntimeError("Няма среща в момента.")
            if self.asking:
                raise RuntimeError("Още мисля по предишния въпрос.")
            mid = self.meeting_id
            self._flush_chunk()  # the last seconds count too
            self.asking = True
        self._publish_state()
        try:
            deadline = time.time() + 30
            while True:
                m = self.spool.load(mid)
                if m and m.registered and all(c.uploaded for c in m.chunks):
                    break
                if time.time() > deadline:
                    raise RuntimeError("Записът още не е качен на сървъра (връзката?). Опитайте след малко.")
                self._stop.wait(0.3)
            res = self.client.ask(mid, preset=preset, question=question, audio=audio)
            self.bus.publish("answer", **res)
            return res
        finally:
            self.asking = False
            self._publish_state()

    def listen_start(self) -> None:
        """Hold-to-ask: collect the question from the microphone until listen_stop()."""
        if self.state != "recording":
            raise RuntimeError("Няма среща в момента.")
        self._q_frames = []

    def listen_stop(self) -> dict:
        from bix.common.audio import encode_flac

        frames, self._q_frames = self._q_frames, None
        if not frames or len(frames) * FRAME / SR < 0.8:
            raise RuntimeError("Задръжте бутона, докато говорите.")
        return self.ask(audio=encode_flac(np.concatenate(frames)))

    # --- status ---------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "state": self.state,
            "muted": self.muted,
            "online": self.online,
            "enrolled": self.client.enrolled,
            "error": self.error,
            "meeting_id": self.meeting_id,
            "elapsed_s": round(now - self.meeting_started) if self.state == "recording" else 0,
            "recorded_s": round(self.recorded_s),
            "level_db": round(self.level, 1),
            "speaking": self.speaking,
            "present": self.present,
            "distance_cm": self.distance_cm,
            "backlog_s": round(self.spool.backlog_seconds()),
            "mic": self.capture.device_name,
            "vad": self._vad.kind if self._vad else "",
            "leds": self.leds.kind,
            "policy": self.policy,
            "agenda": self.agenda,
            "last_meeting": self.last_meeting,
            "meeting": self._meeting_info(now),
            "ending": {**self.ending, "left_s": max(0, round(self.ending["at"] - now))} if self.ending else None,
            "suggest": self.suggest,
            "recorded_uids": self.recorded_uids,
            "asking": self.asking,
            "server": self.client.base,
            "version": __version__,
        }

    def _meeting_info(self, now: float) -> dict | None:
        if self.state != "recording" or not self.meeting_id:
            return None
        ev = self.meeting_event or {}
        sm = self.spool.load(self.meeting_id)
        return {
            "id": self.meeting_id, "title": (sm.title if sm else "") or ev.get("title", ""),
            "event_uid": ev.get("uid", ""), "scheduled_end": ev.get("end"), "attendees": ev.get("attendees", []),
            "organizer": ev.get("organizer", ""),
            "stars": sum(1 for n in (sm.notes if sm else []) if n.get("kind") == "star"),
        }

    def _publish_state(self) -> None:
        self.bus.publish("state", **self.status())
        self.sync_now()  # tell the server too, so the portal isn't a heartbeat behind

    def _set_error(self, msg: str) -> None:
        log.error(msg)
        self.error = msg
        self.leds.show("error")
        self._publish_state()

    # --- audio ----------------------------------------------------------------
    def _audio_loop(self) -> None:
        last_level = 0.0
        while not self._stop.is_set():
            try:
                frame = self.capture.frames.get(timeout=0.5)
            except Exception:
                self._check_auto_stop()
                continue
            self.level = level_db(frame)
            speech, _ = self._vad.feed(frame)
            self.speaking = speech
            now = time.time()
            if self._wake and self.state == "idle":
                self._feed_wake(frame)
            if self._q_frames is not None and len(self._q_frames) * FRAME / SR < 30:
                self._q_frames.append(frame)
            if self.state == "idle":
                self._watch_for_meeting(now, speech)
            with self._lock:
                if self.state == "recording" and not self.muted:
                    self._chunk.append(frame)
                    self.recorded_s += FRAME / SR
                    if speech:
                        self.speech_s += FRAME / SR
                        self.last_speech = now
                    chunk_s = len(self._chunk) * FRAME / SR
                    if (chunk_s >= self.cfg.audio.chunk_target_s and not speech) or chunk_s >= self.cfg.audio.chunk_max_s:
                        self._flush_chunk()
            for snd in self._sounds.feed(frame, self.level, speech):
                self.bus.publish("sound", kind=snd.kind, count=snd.count, db=snd.db, doa=self._sound_angle())
            if now - last_level > 0.1:
                last_level = now
                self.bus.publish("level", db=round(self.level, 1), speaking=speech, doa=self._doa_angle(now))
            self._check_auto_stop()

    def _doa_angle(self, now: float) -> int | None:
        """Where the voice comes from (mic array), relative to the screen; the face looks there."""
        if not self.speaking:
            return None
        if self.leds.kind == "none":
            return self.sim_doa  # the simulator's "where the speaker sits", without the mic array
        if now - self._doa_at > 0.3:
            self._doa_at = now
            d = self.leds.doa()
            self._doa = (d[0] - self.cfg.hardware.doa_front_deg + 180) % 360 - 180 if d else None
        return self._doa

    def _sound_angle(self) -> int | None:
        """Where a knock or a bang came from (the mic array tracks any sound, not only voices)."""
        if self.leds.kind == "none":
            return self.sim_doa
        d = self.leds.doa()
        return (d[0] - self.cfg.hardware.doa_front_deg + 180) % 360 - 180 if d else None

    def _flush_chunk(self) -> None:
        if not self._chunk or not self.meeting_id:
            return
        audio = np.concatenate(self._chunk)
        self.spool.add_chunk(self.meeting_id, audio, self._chunk_start)
        self._chunk_start += len(audio) / SR
        self._chunk = []

    def _check_auto_stop(self) -> None:
        """Automatic stops never happen silently: the screen counts down first, and
        "Продължи" on the screen keeps the meeting going."""
        if self.state != "recording":
            return
        now = time.time()
        why = self._stop_reason(now)
        if why is None:
            if self.ending:  # speech or people came back by themselves
                self.ending = None
                self._publish_state()
            return
        reason, info = why
        if not self.ending or self.ending["reason"] != reason:
            self.ending = {"reason": reason, "at": now + COUNTDOWN_S, **info}
            log.info("automatic stop in %d s (%s)", COUNTDOWN_S, reason)
            self._publish_state()
        elif now >= self.ending["at"]:
            self.stop_meeting(reason)

    def _stop_reason(self, now: float) -> tuple[str, dict] | None:
        silence_min = float(self.policy.get("auto_stop_silence_min") or 0)
        if silence_min and not self.muted and now - self.last_speech > silence_min * 60:
            return "silence", {"minutes": silence_min}
        if self.absent_since and now - self.absent_since > self.cfg.hardware.presence_empty_minutes * 60:
            return "room_empty", {}
        if now - self.meeting_started > float(self.policy.get("max_meeting_hours") or 4) * 3600 + self._extra_s:
            return "max_duration", {}
        if now < self._grace_until:
            return None
        ev = self.meeting_event
        # the next booking in this room is about to start: this meeting has to end
        nxt = next(
            (e for e in self.agenda if e.get("start", 0) > self.meeting_started + 60 and (not ev or e.get("uid") != ev.get("uid")) and 0 <= e["start"] - now <= 5 * 60),
            None,
        )
        if nxt and (not ev or now >= ev.get("end", 0) - 5 * 60):
            return "next_meeting", {"next": nxt.get("title", ""), "next_start": nxt["start"]}
        if ev and ev.get("end") and now >= ev["end"] + 10 * 60:
            return "calendar", {"booked_end": ev["end"]}
        return None

    def _watch_for_meeting(self, now: float, speech: bool) -> None:
        """People talk in the room but nobody pressed Start: ask (never record by itself)."""
        if speech:
            self._idle_speech.append((now, FRAME / SR))
        while self._idle_speech and now - self._idle_speech[0][0] > 120:
            self._idle_speech.popleft()
        talked = sum(d for _, d in self._idle_speech)
        if self.suggest:
            if talked < 5 and now - self.suggest["since"] > 120:
                self.suggest = None  # they stopped talking
                self._publish_state()
            return
        if now < self._suggest_snooze or self.present is False or not self.client.enrolled:
            return
        booked = next((e for e in self.agenda if e.get("start", 0) - 300 <= now <= e.get("end", 0)), None)
        if talked >= 45 or (booked and self.present and now - booked["start"] < 15 * 60 and talked >= 10):
            self.suggest = {"since": now, "event": booked}
            log.info("people are talking and nothing records: asking on the screen")
            self._publish_state()

    def _on_presence(self, present: bool) -> None:
        self.present = present
        self.absent_since = None if present else time.time()
        self.bus.publish("presence", present=present)

    def _on_distance(self, cm: int | None) -> None:
        self.distance_cm = cm
        self.bus.publish("radar", cm=cm)

    # --- wake word (optional) ---------------------------------------------
    def _setup_wake(self) -> None:
        if not (self.cfg.wake.enabled and self.cfg.wake.model_path):
            return
        try:
            from openwakeword.model import Model

            self._wake = Model(wakeword_models=[self.cfg.wake.model_path])
            self._wake_buf: list[np.ndarray] = []
            log.info("wake word enabled: %s", self.cfg.wake.model_path)
        except Exception as e:
            log.warning("wake word unavailable: %s", e)

    def _feed_wake(self, frame: np.ndarray) -> None:
        self._wake_buf.append(frame)
        if len(self._wake_buf) < 4:  # openWakeWord wants 80 ms
            return
        pcm = (np.concatenate(self._wake_buf) * 32767).astype(np.int16)
        self._wake_buf = []
        scores = self._wake.predict(pcm)
        if max(scores.values(), default=0) >= self.cfg.wake.threshold:
            self._wake.reset()
            self.start_meeting("wake_word")

    # --- network ----------------------------------------------------------
    def _uplink_loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            if not self.client.enrolled:
                self._stop.wait(5)
                continue
            try:
                worked = self._upload_some()
                backoff = 2.0
                if not worked:
                    self._stop.wait(1.0)
            except NotEnrolled:
                self._stop.wait(5)
            except Exception as e:
                log.warning("upload: %s", e)
                self._set_online(False)
                dropped = self.spool.enforce_limit(self.cfg.spool.max_hours)
                if dropped:
                    log.error("offline buffer full; dropped meetings %s", dropped)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _upload_some(self) -> bool:
        for m in self.spool.meetings():
            if m.discard:
                if m.registered:
                    self.client.discard(m.id)
                self.spool.remove(m.id)
                return True
            if not m.registered:
                self.client.start_meeting(m.id, m.started_at, m.title, m.calendar_uid)
                self.spool.update(m.id, registered=True)
                self._set_online(True)
                return True
            for c in m.chunks:
                if not c.uploaded:
                    path = self.spool.chunk_path(m.id, c.seq)
                    if path.exists():
                        self.client.upload_chunk(m.id, c.seq, c.start_s, c.duration_s, path)
                    self.spool.mark_uploaded(m.id, c.seq)
                    self._set_online(True)
                    return True
            if m.ended_at and not m.ended_sent and m.id != self.meeting_id:
                self.client.end_meeting(m.id, m.ended_at, len(m.chunks), m.duration_s, m.notes)
                self.spool.remove(m.id)
                self.bus.publish("uploaded", id=m.id)
                return True
        return False

    def _set_online(self, online: bool) -> None:
        if online != self.online:
            self.online = online
            self._publish_state()

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            if self.client.enrolled:
                try:
                    resp = self.client.heartbeat(
                        {
                            "state": ("muted" if self.muted else self.state),
                            "fw_version": __version__,
                            "platform": platform_string(),
                            "meeting_id": self.meeting_id,
                            "telemetry": self._telemetry(),
                            "acked_commands": self._acked[-50:],
                        }
                    )
                    self._set_online(True)
                    self._apply_policy(resp.get("config", {}))
                    self._apply_agenda(resp.get("agenda"), resp.get("last_meeting"))
                    for cmd in resp.get("commands", []):
                        self._run_command(cmd)
                except NotEnrolled:
                    pass
                except Exception as e:
                    log.debug("heartbeat: %s", e)
                    self._set_online(False)
            self._hb_wake.wait(self.cfg.heartbeat_s if self.state != "recording" else min(self.cfg.heartbeat_s, 10))
            self._hb_wake.clear()

    def _run_command(self, cmd: dict) -> None:
        cid, typ = cmd.get("id", ""), cmd.get("type", "")
        if cid in self._acked:
            return
        self._acked.append(cid)
        log.info("server command: %s (by %s)", typ, cmd.get("by"))
        if typ == "start":
            self.start_meeting("remote")
        elif typ == "stop":
            self.stop_meeting("remote")
        elif typ == "mute":
            self.set_muted(True)
        elif typ == "unmute":
            self.set_muted(False)
        elif typ == "identify":
            self.leds.show("identify")
            self.bus.publish("identify")
            threading.Timer(5, lambda: self.leds.show("recording" if self.state == "recording" else "idle")).start()
        elif typ == "restart":
            self.bus.publish("restart")
        elif typ == "sync":
            pass  # the heartbeat that delivered it already synced policy

    def _apply_policy(self, cfg: dict) -> None:
        if cfg and cfg != {k: self.policy.get(k) for k in cfg}:
            self.policy.update(cfg)
            try:
                (self.cfg.dir / "policy.json").write_text(json.dumps(self.policy, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            self._publish_state()

    def _apply_agenda(self, agenda: list | None, last: dict | None) -> None:
        if agenda is None:
            return
        if agenda != self.agenda or last != self.last_meeting:
            self.agenda, self.last_meeting = agenda, last
            if self.meeting_event:  # the booking may have moved or been extended
                self.meeting_event = next((e for e in agenda if e.get("uid") == self.meeting_event.get("uid")), self.meeting_event)
            try:
                (self.cfg.dir / "agenda.json").write_text(json.dumps({"agenda": agenda, "last": last}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            self._publish_state()

    def _load_agenda(self) -> None:
        p = self.cfg.dir / "agenda.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                self.agenda = [e for e in d.get("agenda", []) if e.get("end", 0) > time.time()]
                self.last_meeting = d.get("last")
            except Exception:
                pass

    def _load_policy(self) -> None:
        p = self.cfg.dir / "policy.json"
        if p.exists():
            try:
                self.policy.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass

    def _telemetry(self) -> dict:
        t: dict[str, Any] = {
            "backlog_s": round(self.spool.backlog_seconds()),
            "mic": self.capture.device_name,
            "mic_ok": self.capture.running,
            "dropped_frames": self.capture.dropped,
            "vad": self._vad.kind if self._vad else "",
            "level_db": round(self.level, 1),
            "present": self.present,
        }
        try:
            t["disk_free_gb"] = round(shutil.disk_usage(self.cfg.dir).free / 1e9, 1)
        except OSError:
            pass
        try:
            from pathlib import Path

            t["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            pass
        return t

    def _live_loop(self) -> None:
        while not self._stop.is_set():
            mid = self.meeting_id
            if mid and self.state == "recording" and self.policy.get("live_transcription") and self.online:
                try:
                    for seg in self.client.live(mid, self._live_after):
                        self._live_after = max(self._live_after, seg["id"])
                        self.live_lines.append(seg)
                        self.bus.publish("live", **seg)
                    self.live_lines = self.live_lines[-200:]
                except Exception as e:
                    log.debug("live: %s", e)
            self._stop.wait(3)
