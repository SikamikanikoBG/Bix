"use strict";
/* Bix in a browser: any laptop in a meeting room becomes a Bix device by opening
   https://<server>/room. Same room screen as the hardware (panel.js), but instead of the
   local agent this file does the agent's work in the page:
     microphone (only while a meeting runs) → 16 kHz → voice activity → chunks cut in pauses
     chunks kept in IndexedDB until the server has them (Wi-Fi drops, the tab reloads)
     heartbeat: policy, today's bookings, the last meeting, commands from the portal
     automatic stops with a countdown, questions to Bix, stars.
   It talks to the same device API as the hardware, with its own device token. */
(function () {
  const SR = 16000, FRAME = 320, COUNTDOWN_S = 60;
  const VERSION = "web-0.2";
  const LS = "bix.room.v1";
  const DEFAULT_POLICY = {
    org: "", room: "", language: "bg", live_transcription: false, auto_stop_silence_min: 10, max_meeting_hours: 4,
    notice_text: "Тази среща се записва. Bix е ИИ асистент, който прави транскрипция и минути.", speaker_mode: "voice_profiles", face_style: "pixel",
  };
  const now = () => Date.now() / 1000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/-/g, "").slice(0, 32).padEnd(32, "0");

  // --- IndexedDB: meetings (manifests) and their chunks --------------------------------
  function idb() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("bix-room", 1);
      r.onupgradeneeded = () => { r.result.createObjectStore("meetings", { keyPath: "id" }); r.result.createObjectStore("chunks"); };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  function tx(db, store, mode, fn) {
    return new Promise((resolve, reject) => {
      const t = db.transaction(store, mode);
      const out = fn(t.objectStore(store));
      t.oncomplete = () => resolve(out && "result" in out ? out.result : undefined);
      t.onerror = () => reject(t.error);
    });
  }

  function wav(samples) {
    const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, SR, true);
    v.setUint32(28, SR * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
    return new Blob([buf], { type: "audio/wav" });
  }
  function concat(frames) {
    const out = new Float32Array(frames.reduce((n, f) => n + f.length, 0));
    let o = 0;
    for (const f of frames) { out.set(f, o); o += f.length; }
    return out;
  }

  class WebAgent {
    constructor() {
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem(LS) || "{}"); } catch { saved = {}; }
      this.creds = saved.creds || null; // {token, device_id}
      this.serial = saved.serial || uuid();
      this.policy = Object.assign({}, DEFAULT_POLICY, saved.policy || {});
      this.agenda = saved.agenda || [];
      this.last_meeting = saved.last || null;
      this.listeners = [];
      this.state = "idle"; this.muted = false; this.online = false; this.error = "";
      this.meeting = null; // manifest of the running meeting
      this.meetingEvent = null; this.ending = null; this.graceUntil = 0; this.extraS = 0;
      this.asking = false; this.recorded_uids = [];
      this.level = -90; this.speaking = false; this.lastSpeech = now(); this.silentFrames = 0;
      this.noise = -60; this.chunk = []; this.chunkStart = 0; this.qFrames = null;
      this.liveAfter = 0; this.acked = []; this.backlogS = 0; this.micName = "";
      this.lastLevelAt = 0;
      this.save();
      this.dbp = idb().catch(() => null);
      this.recover().finally(() => { this.uplinkLoop(); this.heartbeatLoop(); });
      setInterval(() => this.checkAutoStop(), 1000);
      setInterval(() => this.pollLive(), 3000);
      window.addEventListener("beforeunload", (e) => { if (this.state === "recording") { e.preventDefault(); e.returnValue = ""; } });
      window.addEventListener("online", () => this.kick());
      // the tab closes or reloads mid-meeting: save the last seconds too (best effort)
      window.addEventListener("pagehide", () => { if (this.state === "recording") this.flushChunk(); });
    }

    // --- the panel's view ----------------------------------------------------------------
    save() {
      try { localStorage.setItem(LS, JSON.stringify({ creds: this.creds, serial: this.serial, policy: this.policy, agenda: this.agenda, last: this.last_meeting })); } catch { /* private window */ }
    }
    get enrolled() { return !!(this.creds && this.creds.token); }
    status() {
      const t = now(), m = this.meeting, ev = this.meetingEvent || {};
      return {
        state: this.state, muted: this.muted, online: this.online, enrolled: this.enrolled, error: this.error,
        meeting_id: m ? m.id : null, elapsed_s: m ? Math.round(t - m.started_at) : 0, recorded_s: 0,
        level_db: Math.round(this.level * 10) / 10, speaking: this.speaking, present: null, backlog_s: Math.round(this.backlogS),
        mic: this.micName, vad: "energy", leds: "none", policy: this.policy, agenda: this.agenda, last_meeting: this.last_meeting,
        meeting: m ? { id: m.id, title: m.title || ev.title || "", event_uid: ev.uid || "", scheduled_end: ev.end || null, attendees: ev.attendees || [], organizer: ev.organizer || "", stars: m.notes.filter((n) => n.kind === "star").length } : null,
        ending: this.ending ? Object.assign({}, this.ending, { left_s: Math.max(0, Math.round(this.ending.at - t)) }) : null,
        suggest: null, asking: this.asking, recorded_uids: this.recorded_uids, server: location.origin, version: VERSION, web: true,
      };
    }
    emit(type, data) { for (const f of this.listeners) { try { f(type, data); } catch (e) { console.error(e); } } }
    publish() { this.emit("state", this.status()); }

    // --- server calls ------------------------------------------------------------------------
    async api(method, path, body, headers = {}) {
      const h = Object.assign({}, headers);
      if (this.creds) h.Authorization = `Bearer ${this.creds.token}`;
      if (body && !(body instanceof Blob)) { h["Content-Type"] = "application/json"; body = JSON.stringify(body); }
      const r = await fetch(path, { method, headers: h, body, cache: "no-store" });
      if (r.status === 401 && this.creds) { // revoked in the portal
        this.creds = null; this.save(); this.setError("Устройството е премахнато от портала. Регистрирайте го отново с нов код.");
      }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const err = new Error(typeof d.detail === "string" ? d.detail : `Сървърът върна ${r.status}.`);
        err.status = r.status;
        throw err;
      }
      return r.json();
    }
    setOnline(v) { if (v !== this.online) { this.online = v; this.publish(); } }
    setError(msg) { this.error = msg; this.publish(); }

    async enroll(code) {
      const ua = navigator.userAgent.match(/(Edg|Chrome|Firefox|Safari)\/[\d.]+/);
      const d = await this.api("POST", "/api/device/enroll", { code, serial: this.serial, name: "Браузър", platform: `browser ${ua ? ua[0] : ""}`.trim(), fw_version: VERSION });
      this.creds = { token: d.token, device_id: d.device_id };
      this.policy.org = d.org; this.policy.room = d.room; this.error = "";
      this.save(); this.publish(); this.kick();
      return { ok: true, org: d.org, room: d.room };
    }

    kick() { this.hbDirty = true; this.hbWake && this.hbWake(); this.upWake && this.upWake(); }

    async heartbeatLoop() {
      for (;;) {
        if (this.enrolled) {
          this.hbDirty = false;
          try {
            const r = await this.api("POST", "/api/device/heartbeat", {
              state: this.muted ? "muted" : this.state, fw_version: VERSION, platform: "browser", meeting_id: this.meeting ? this.meeting.id : null,
              telemetry: { backlog_s: Math.round(this.backlogS), mic: this.micName, level_db: Math.round(this.level), browser: navigator.userAgent.slice(0, 120) },
              acked_commands: this.acked.slice(-50),
            });
            this.setOnline(true);
            const pol = Object.assign({}, this.policy, r.config || {});
            const changed = JSON.stringify(pol) !== JSON.stringify(this.policy) || JSON.stringify(r.agenda) !== JSON.stringify(this.agenda) || JSON.stringify(r.last_meeting) !== JSON.stringify(this.last_meeting);
            this.policy = pol; this.agenda = r.agenda || []; this.last_meeting = r.last_meeting || null;
            if (this.meetingEvent) this.meetingEvent = this.agenda.find((e) => e.uid === this.meetingEvent.uid) || this.meetingEvent;
            if (changed) { this.save(); this.publish(); }
            for (const c of r.commands || []) this.command(c);
          } catch (e) {
            if (e.status !== 401) this.setOnline(false);
          }
        }
        if (this.hbDirty) { this.hbDirty = false; continue; } // state changed during the call: tell the server now
        await new Promise((res) => { this.hbWake = res; setTimeout(res, this.state === "recording" ? 10000 : 15000); });
      }
    }

    command(c) {
      if (this.acked.includes(c.id)) return;
      this.acked.push(c.id);
      if (c.type === "start") this.start("remote").catch((e) => this.setError(e.message));
      else if (c.type === "stop") this.stop("remote");
      else if (c.type === "mute") this.setMuted(true);
      else if (c.type === "unmute") this.setMuted(false);
      else if (c.type === "identify") this.emit("identify", {});
      else if (c.type === "restart") setTimeout(() => location.reload(), 500);
    }

    // --- meetings in IndexedDB ------------------------------------------------------------------
    async saveManifest(m) { const db = await this.dbp; if (db) await tx(db, "meetings", "readwrite", (s) => s.put(JSON.parse(JSON.stringify(m)))); }
    async manifests() { const db = await this.dbp; return db ? (await tx(db, "meetings", "readonly", (s) => s.getAll())) || [] : []; }
    async recover() {
      // a meeting that was running when the tab closed: keep what was recorded and upload it
      for (const m of await this.manifests()) {
        if (!m.ended_at) {
          m.ended_at = m.started_at + m.chunks.reduce((n, c) => n + c.duration_s, 0);
          await this.saveManifest(m);
        }
      }
    }

    async uplinkLoop() {
      let backoff = 2000;
      for (;;) {
        let worked = false;
        if (this.enrolled) {
          try { worked = await this.uploadSome(); backoff = 2000; }
          catch (e) { this.setOnline(false); await sleep(backoff); backoff = Math.min(backoff * 2, 60000); continue; }
        }
        if (!worked) await new Promise((res) => { this.upWake = res; setTimeout(res, 1000); });
      }
    }

    async uploadSome() {
      const db = await this.dbp;
      const ms = (await this.manifests()).sort((a, b) => a.started_at - b.started_at);
      this.backlogS = ms.reduce((n, m) => n + m.chunks.filter((c) => !c.uploaded).reduce((k, c) => k + c.duration_s, 0), 0);
      for (const m of ms) {
        if (m.discard) {
          if (m.registered) await this.api("POST", `/api/device/meetings/${m.id}/discard`).catch((e) => { if (e.status !== 404) throw e; });
          await this.dropMeeting(m.id);
          return true;
        }
        if (!m.registered) {
          await this.api("POST", "/api/device/meetings", { id: m.id, started_at: m.started_at, title: m.title || "", calendar_uid: m.calendar_uid || "" });
          m.registered = true;
          await this.saveManifest(m);
          if (this.meeting && this.meeting.id === m.id) this.meeting.registered = true;
          this.setOnline(true);
          return true;
        }
        for (const c of m.chunks) {
          if (c.uploaded) continue;
          const blob = db ? await tx(db, "chunks", "readonly", (s) => s.get(`${m.id}:${c.seq}`)) : null;
          if (blob) {
            const q = new URLSearchParams({ start_s: c.start_s, duration_s: c.duration_s });
            await this.api("PUT", `/api/device/meetings/${m.id}/chunks/${c.seq}?${q}`, blob, { "Content-Type": "audio/wav" });
          }
          c.uploaded = true;
          await this.saveManifest(m);
          if (this.meeting && this.meeting.id === m.id) { const mc = this.meeting.chunks.find((x) => x.seq === c.seq); if (mc) mc.uploaded = true; }
          if (db) await tx(db, "chunks", "readwrite", (s) => s.delete(`${m.id}:${c.seq}`));
          this.setOnline(true);
          return true;
        }
        if (m.ended_at && (!this.meeting || this.meeting.id !== m.id)) {
          await this.api("POST", `/api/device/meetings/${m.id}/end`, { ended_at: m.ended_at, chunks: m.chunks.length, duration_s: m.chunks.reduce((n, c) => n + c.duration_s, 0), notes: m.notes || [] });
          await this.dropMeeting(m.id);
          this.emit("uploaded", { id: m.id });
          return true;
        }
      }
      return false;
    }

    async dropMeeting(id) {
      const db = await this.dbp;
      if (!db) return;
      await tx(db, "meetings", "readwrite", (s) => s.delete(id));
      await tx(db, "chunks", "readwrite", (s) => s.delete(IDBKeyRange.bound(`${id}:`, `${id}:￿`)));
    }

    // --- microphone (only during a meeting) --------------------------------------------------------
    async micOn() {
      if (!window.isSecureContext || !navigator.mediaDevices) throw new Error("Браузърът дава микрофона само на защитен адрес (https://).");
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (e) {
        throw new Error(e.name === "NotAllowedError" ? "Браузърът не позволи микрофона. Разрешете го от иконата в адресната лента и опитайте пак." : `Микрофон: ${e.message}`);
      }
      this.micName = (this.stream.getAudioTracks()[0] || {}).label || "микрофон на браузъра";
      this.ctx = new AudioContext();
      await this.ctx.audioWorklet.addModule("/room/worklet.js");
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "bix-capture");
      this.node.port.onmessage = (e) => this.onFrame(e.data);
      const mute = this.ctx.createGain(); mute.gain.value = 0; // keeps the graph running, plays nothing
      src.connect(this.node); this.node.connect(mute); mute.connect(this.ctx.destination);
      this.stream.getAudioTracks()[0].onended = () => { if (this.state === "recording") this.setError("Микрофонът изчезна (изключен ли е?). Записът продължава, когато се върне."); };
      try { this.wake = await navigator.wakeLock.request("screen"); } catch { this.wake = null; } // the laptop must not sleep mid-meeting
    }
    micOff() {
      try { this.stream && this.stream.getTracks().forEach((t) => t.stop()); } catch { /* already gone */ }
      try { this.ctx && this.ctx.close(); } catch { /* closed */ }
      try { this.wake && this.wake.release(); } catch { /* released */ }
      this.stream = this.ctx = this.node = this.wake = null;
      this.level = -90; this.speaking = false;
    }

    onFrame(f) {
      let e = 0;
      for (let i = 0; i < f.length; i++) e += f[i] * f[i];
      const db = 10 * Math.log10(e / f.length + 1e-10);
      this.level = db;
      // energy voice detection with a noise floor that follows the room
      this.noise = db < this.noise ? this.noise * 0.9 + db * 0.1 : this.noise * 0.999 + db * 0.001;
      const voiced = db > Math.max(this.noise + 9, -52);
      this.silentFrames = voiced ? 0 : this.silentFrames + 1;
      this.speaking = this.silentFrames < 15; // 300 ms hangover
      const t = now();
      if (this.speaking) this.lastSpeech = t;
      if (t - this.lastLevelAt > 0.1) { this.lastLevelAt = t; this.emit("level", { db: Math.round(db * 10) / 10, speaking: this.speaking, doa: null }); }
      if (this.qFrames && this.qFrames.length < 50 * 30) this.qFrames.push(f);
      if (this.state !== "recording" || this.muted) return;
      this.chunk.push(f);
      const sec = (this.chunk.length * FRAME) / SR;
      if ((sec >= 20 && this.silentFrames >= 15) || sec >= 45) this.flushChunk();
    }

    async flushChunk() {
      if (!this.chunk.length || !this.meeting) return;
      const m = this.meeting, audio = concat(this.chunk);
      this.chunk = [];
      const c = { seq: m.chunks.length, start_s: Math.round(this.chunkStart * 1000) / 1000, duration_s: Math.round((audio.length / SR) * 1000) / 1000, uploaded: false };
      this.chunkStart += audio.length / SR;
      m.chunks.push(c);
      const db = await this.dbp;
      if (db) await tx(db, "chunks", "readwrite", (s) => s.put(wav(audio), `${m.id}:${c.seq}`));
      await this.saveManifest(m);
      this.kick();
    }

    // --- controls -----------------------------------------------------------------------------------------
    async start(source, eventUid = "") {
      if (this.state === "recording") return { meeting_id: this.meeting.id };
      if (!this.enrolled) throw new Error("Устройството не е регистрирано.");
      await this.micOn();
      const ev = eventUid ? this.agenda.find((e) => e.uid === eventUid) || null : null;
      const m = { id: uuid(), started_at: now(), title: ev ? ev.title : "", calendar_uid: ev ? ev.uid : "", registered: false, ended_at: null, discard: false, notes: [], chunks: [] };
      this.meeting = m; this.meetingEvent = ev; this.ending = null; this.graceUntil = 0; this.extraS = 0;
      this.state = "recording"; this.muted = false; this.error = ""; this.chunk = []; this.chunkStart = 0; this.liveAfter = 0;
      this.lastSpeech = now();
      await this.saveManifest(m);
      this.publish(); this.kick();
      return { meeting_id: m.id };
    }

    async stop(reason = "button") {
      if (this.state !== "recording") return;
      await this.flushChunk();
      const m = this.meeting, ev = this.meetingEvent;
      m.ended_at = now();
      this.state = "idle"; this.muted = false; this.meeting = null; this.meetingEvent = null; this.ending = null;
      if (ev) this.recorded_uids = this.recorded_uids.concat(ev.uid).slice(-30);
      this.micOff();
      await this.saveManifest(m);
      this.emit("meeting_ended", { id: m.id, reason, title: m.title || (ev && ev.title) || "", duration_s: Math.round(m.ended_at - m.started_at), attendees: ev ? ev.attendees : [], organizer: ev ? ev.organizer : "", stars: m.notes.filter((n) => n.kind === "star").length });
      this.publish(); this.kick();
    }

    async discard() {
      if (this.state !== "recording") return;
      const m = this.meeting;
      this.chunk = []; m.discard = true; m.ended_at = now();
      this.state = "idle"; this.muted = false; this.meeting = null; this.meetingEvent = null; this.ending = null;
      this.micOff();
      await this.saveManifest(m);
      const db = await this.dbp;
      if (db) await tx(db, "chunks", "readwrite", (s) => s.delete(IDBKeyRange.bound(`${m.id}:`, `${m.id}:￿`)));
      this.emit("meeting_ended", { id: m.id, reason: "discarded" });
      this.publish(); this.kick();
    }

    async setMuted(v) {
      if (this.state !== "recording" || v === this.muted) return;
      if (v) await this.flushChunk();
      this.muted = v;
      this.publish();
    }

    async mark() {
      if (this.state !== "recording") throw new Error("Няма среща в момента.");
      const n = { t: Math.round((now() - this.meeting.started_at) * 10) / 10, kind: "star", text: "" };
      this.meeting.notes.push(n);
      await this.saveManifest(this.meeting);
      this.emit("marked", n); this.publish();
      return n;
    }

    keep() {
      if (!this.ending) return;
      const r = this.ending.reason, t = now();
      if (r === "silence") this.lastSpeech = t;
      else if (r === "calendar" || r === "next_meeting") this.graceUntil = t + 15 * 60;
      else if (r === "max_duration") this.extraS += 30 * 60;
      this.ending = null;
      this.publish();
    }

    stopReason(t) {
      const m = this.meeting, p = this.policy;
      const silenceMin = +p.auto_stop_silence_min || 0;
      if (silenceMin && !this.muted && t - this.lastSpeech > silenceMin * 60) return ["silence", { minutes: silenceMin }];
      if (t - m.started_at > (+p.max_meeting_hours || 4) * 3600 + this.extraS) return ["max_duration", {}];
      if (t < this.graceUntil) return null;
      const ev = this.meetingEvent;
      const nxt = this.agenda.find((e) => e.start > m.started_at + 60 && (!ev || e.uid !== ev.uid) && e.start - t >= 0 && e.start - t <= 300);
      if (nxt && (!ev || t >= ev.end - 300)) return ["next_meeting", { next: nxt.title, next_start: nxt.start }];
      if (ev && ev.end && t >= ev.end + 600) return ["calendar", { booked_end: ev.end }];
      return null;
    }

    checkAutoStop() {
      if (this.state !== "recording") return;
      const t = now(), why = this.stopReason(t);
      if (!why) { if (this.ending) { this.ending = null; this.publish(); } return; }
      const [reason, info] = why;
      if (!this.ending || this.ending.reason !== reason) { this.ending = Object.assign({ reason, at: t + COUNTDOWN_S }, info); this.publish(); }
      else if (t >= this.ending.at) this.stop(reason);
    }

    async pollLive() {
      if (this.state !== "recording" || !this.policy.live_transcription || !this.online || !this.meeting || !this.meeting.registered) return;
      try {
        const d = await this.api("GET", `/api/device/meetings/${this.meeting.id}/live?after=${this.liveAfter}`);
        for (const s of d.segments) { this.liveAfter = Math.max(this.liveAfter, s.id); this.emit("live", s); }
      } catch { /* next time */ }
    }

    async ask(body, audio) {
      if (this.state !== "recording") throw new Error("Няма среща в момента.");
      if (this.asking) throw new Error("Още мисля по предишния въпрос.");
      const id = this.meeting.id;
      this.asking = true; this.publish();
      try {
        await this.flushChunk(); // the last seconds count too
        const until = now() + 30;
        while (!(this.meeting && this.meeting.registered && this.meeting.chunks.every((c) => c.uploaded))) {
          if (now() > until) throw new Error("Записът още не е качен на сървъра (връзката?). Опитайте след малко.");
          this.kick();
          await sleep(300);
        }
        const r = audio
          ? await this.api("POST", `/api/device/meetings/${id}/ask-voice`, audio, { "Content-Type": "audio/wav" })
          : await this.api("POST", `/api/device/meetings/${id}/ask`, body);
        this.emit("answer", r);
        return r;
      } finally {
        this.asking = false; this.publish();
      }
    }

    async route(path, body) {
      switch (path) {
        case "/api/enroll": return this.enroll((body && body.code) || "");
        case "/api/start": return this.start("panel", (body && body.event_uid) || "");
        case "/api/stop": await this.stop("panel"); return { ok: true };
        case "/api/mute": await this.setMuted(true); return { ok: true };
        case "/api/unmute": await this.setMuted(false); return { ok: true };
        case "/api/discard": await this.discard(); return { ok: true };
        case "/api/mark": return this.mark();
        case "/api/keep": this.keep(); return { ok: true };
        case "/api/suggest/dismiss": return { ok: true };
        case "/api/ask": return this.ask(body || {});
        case "/api/ask/listen":
          if (this.state !== "recording") throw new Error("Няма среща в момента.");
          this.qFrames = []; return { ok: true };
        case "/api/ask/voice": {
          const fr = this.qFrames; this.qFrames = null;
          if (!fr || fr.length < 40) throw new Error("Задръжте бутона, докато говорите.");
          return this.ask(null, wav(concat(fr)));
        }
        default: throw new Error(`Непознато действие ${path}`);
      }
    }
  }

  const agent = new WebAgent();
  window.BixBackend = {
    web: true,
    agent,
    post: (path, body) => agent.route(path, body),
    status: async () => Object.assign(agent.status(), { live: [] }),
    subscribe(on) { agent.listeners.push(on); },
  };
})();
