"use strict";
// The room screen. Everything a normal day needs happens here: start the booked
// meeting, ask Bix, mark a moment, pause, end; the screen always says what comes next.
const $ = (id) => document.getElementById(id);
let st = null;
let uiError = ""; // last failed action, shown until the next one succeeds
let tick = null;
let sayT = null;
let doneT = null;
let done = null; // the meeting that just ended: shown until "Добре" or a timeout
let endingAt = 0;

const face = new BixFace($("faceCanvas"), {
  style: "pixel",
  onSay: (text, ms) => {
    const el = $("say");
    el.textContent = text;
    el.classList.add("on");
    clearTimeout(sayT);
    sayT = setTimeout(() => el.classList.remove("on"), ms);
  },
});

// Where the screen gets its state from: the device agent on this machine (hardware, laptop
// with `bix device`), or the agent running in this page (any laptop at https://<server>/room).
const EVENTS = ["state", "level", "live", "uploaded", "marked", "presence", "radar", "sound", "identify", "meeting_ended", "answer"];
window.bixFace = face; // the simulator's motion buttons talk to it
const backend = window.BixBackend || {
  web: false,
  async post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "X-Bix": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      const msg = typeof d.detail === "string" ? d.detail : (Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join("; ") : r.statusText);
      throw new Error(msg);
    }
    return r.json();
  },
  async status() { return (await fetch("/api/status")).json(); },
  subscribe(on, reconnected) {
    const es = new EventSource("/api/events");
    for (const t of EVENTS) es.addEventListener(t, (e) => on(t, JSON.parse(e.data)));
    es.onerror = () => { es.close(); setTimeout(() => { reconnected(); this.subscribe(on, reconnected); }, 2000); };
  },
};
const post = (path, body) => backend.post(path, body);

const pad = (n) => String(n).padStart(2, "0");
function fmt(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${pad(m)}:${pad(x)}`;
}
const hm = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const minutes = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${m % 60} мин`; };
function people(names, n) {
  if (!names || !names.length) return "";
  const shown = names.slice(0, 3).join(", ");
  const more = (n || names.length) - Math.min(3, names.length);
  return more > 0 ? `${shown} и още ${more}` : shown;
}

// --- render ----------------------------------------------------------------------
function render() {
  if (!st) return;
  const rec = st.state === "recording";
  face.setStyle(st.policy.face_style || "pixel");
  face.set(st);
  $("room").textContent = st.policy.room || "Bix";
  $("identifyRoom").textContent = st.policy.room || "";
  $("org").textContent = st.policy.org ? "· " + st.policy.org : "";
  const net = $("net");
  net.textContent = !st.enrolled ? "не е регистрирано" : st.online ? "онлайн" : "без връзка";
  net.className = "chip " + (!st.enrolled ? "warn" : st.online ? "ok" : "bad");
  $("backlog").hidden = !st.backlog_s;
  $("backlog").textContent = `за качване: ${fmt(st.backlog_s)}`;
  $("recdot").hidden = !rec;
  $("recdot").textContent = st.muted ? "ПАУЗА" : "ЗАПИСВАМ";
  $("recdot").classList.toggle("paused", !!st.muted);

  $("vEnroll").hidden = st.enrolled;
  $("vDone").hidden = !(st.enrolled && !rec && done);
  $("vIdle").hidden = !(st.enrolled && !rec && !done);
  $("vRec").hidden = !rec;
  if (!st.enrolled) return renderFoot();
  if (rec) renderRec(); else if (done) renderDone(); else renderIdle();
  const err = uiError || st.error;
  $("err").hidden = !err;
  $("err").textContent = err || "";
  renderFoot();
}

function renderFoot() {
  $("mic").textContent = st.mic ? `Микрофон: ${st.mic}` : "";
  $("ver").textContent = `Bix ${st.version} · ${st.server || "без сървър"}`;
}

function currentEvent() {
  const now = Date.now() / 1000;
  const done_ = st.recorded_uids || [];
  return (st.agenda || []).find((e) => e.start - 10 * 60 <= now && now < e.end && !done_.includes(e.uid)) || null;
}

function renderIdle() {
  const now = Date.now() / 1000;
  const ev = currentEvent();
  // a booked meeting now (or in the next 10 minutes): one tap starts exactly that one
  $("nowCard").hidden = !ev;
  $("freeCard").hidden = !!ev;
  if (ev) {
    $("nowEyebrow").textContent = ev.start > now ? `Започва в ${hm(ev.start)}` : "Сега в залата";
    $("nowTitle").textContent = ev.title;
    $("nowSub").textContent = `${hm(ev.start)}–${hm(ev.end)}` + (ev.organizer ? ` · ${ev.organizer}` : "") + (ev.n_attendees ? ` · ${people(ev.attendees, ev.n_attendees)}` : "");
    $("btnStartEvent").textContent = `Започни „${ev.title}“`;
    $("btnStartEvent").dataset.uid = ev.uid;
  }
  const next = (st.agenda || []).filter((e) => e.start > now && e !== ev).slice(0, 3);
  $("agenda").innerHTML = "";
  if (!ev) {
    const recorded = (st.agenda || []).find((e) => e.start <= now && now < e.end && (st.recorded_uids || []).includes(e.uid));
    const free = recorded ? `„${recorded.title}“ е записана ✓` : next.length ? `Свободна до ${hm(next[0].start)}` : "Свободна до края на деня";
    const head = document.createElement("div");
    head.innerHTML = `<span class="eyebrow"></span>`;
    head.firstChild.textContent = free;
    $("agenda").append(head);
  }
  for (const e of next) {
    const row = document.createElement("div");
    const t = document.createElement("time");
    t.textContent = hm(e.start);
    const span = document.createElement("span");
    span.textContent = e.title + (e.organizer ? ` · ${e.organizer}` : "");
    row.append(t, span);
    $("agenda").append(row);
  }
  // "Is a meeting starting?" (people talk, nothing records)
  const sg = st.suggest;
  $("suggest").hidden = !sg;
  if (sg) {
    $("suggestText").textContent = sg.event ? `Започва ли „${sg.event.title}“?` : "Започва ли среща?";
    $("btnSuggestYes").dataset.uid = sg.event ? sg.event.uid : "";
  }
  // what happened to the last meeting: did the minutes reach people?
  const lm = st.last_meeting;
  const line = $("lastLine");
  line.hidden = !lm;
  if (lm) {
    let what, cls = "";
    if (lm.status === "done" && lm.sent) { what = `минутите са изпратени на ${lm.sent} ${lm.sent === 1 ? "човек" : "души"} ✓`; cls = "ok"; }
    else if (lm.status === "done") { what = "минутите са готови, но не са изпратени (няма имейл или получатели)"; cls = "warn"; }
    else if (lm.status === "failed") { what = "обработката не успя, ИТ е уведомен"; cls = "warn"; }
    else what = "обработва се…";
    line.innerHTML = "<span>Последна среща</span> <b></b> <span></span>";
    line.children[1].textContent = `${hm(lm.started_at)} ${lm.title || ""}`.trim();
    line.children[2].textContent = "· " + what;
    line.children[2].className = cls;
  }
}

function renderRec() {
  const m = st.meeting || {};
  $("recTitle").textContent = m.title || "Среща";
  const bits = [];
  if (m.scheduled_end) bits.push(`до ${hm(m.scheduled_end)}`);
  if (m.attendees && m.attendees.length) bits.push(people(m.attendees));
  if (m.stars) bits.push(`⭐ ${m.stars}`);
  $("recSub").textContent = bits.join(" · ") || "Без резервация. Заглавието ще дойде от минутите.";
  if (backend.web) $("recSub").textContent += " · не затваряйте този прозорец";
  $("pausedCard").hidden = !st.muted;
  $("recCard").hidden = !!st.muted;
  $("btnMute").textContent = st.muted ? "Продължи" : "Пауза";
  $("btnAsk").disabled = !!st.asking || !st.online;
  $("btnAsk").textContent = st.asking ? "Мисля…" : st.online ? "Попитай Bix" : "Попитай Bix (няма връзка)";
  // automatic stop coming: always a countdown and a way to keep going
  const en = st.ending;
  $("ending").hidden = !en;
  if (en) endingAt = Date.now() / 1000 + en.left_s;
  clearInterval(tick);
  const t0 = Date.now() / 1000 - st.elapsed_s;
  const upd = () => {
    $("timer").textContent = fmt(Date.now() / 1000 - t0);
    if (en) $("endingText").textContent = endingText(en, Math.max(0, endingAt - Date.now() / 1000));
  };
  upd();
  tick = setInterval(upd, 500);
  const live = !!st.policy.live_transcription;
  $("live").hidden = !live;
}

function endingText(en, left) {
  const when = `Спирам записа след ${fmt(left)}`;
  return {
    silence: `${when}: никой не е говорил ${en.minutes || 10} минути.`,
    room_empty: `${when}: залата изглежда празна.`,
    next_meeting: `${when}: в ${hm(en.next_start)} започва „${en.next}“.`,
    calendar: `${when}: срещата по календар свърши в ${hm(en.booked_end)}.`,
    max_duration: `${when}: срещата е много дълга.`,
  }[en.reason] || when;
}

function renderDone() {
  $("doneTitle").textContent = done.title || "Срещата е записана";
  const bits = [minutes(done.duration_s || 0)];
  if (done.stars) bits.push(`⭐ ${done.stars}`);
  $("doneSub").textContent = bits.join(" · ");
  const to = [];
  if (done.organizer) to.push(`${done.organizer} (организатор)`);
  if (done.attendees && done.attendees.length) to.push(`участниците: ${people(done.attendees)}`);
  $("doneTo").textContent = done.reason === "discarded"
    ? "Записът е изтрит. Нищо не остана."
    : (to.length ? `Минутите ще дойдат по имейл след няколко минути на ${to.join(", ")} и получателите на залата.`
                 : "Минутите ще дойдат по имейл след няколко минути на получателите на залата и на разпознатите по глас хора.");
}

// --- live text -----------------------------------------------------------------------
function addLive(seg) {
  const p = document.createElement("p");
  const t = document.createElement("time");
  t.textContent = fmt(seg.start);
  p.append(t, document.createTextNode(seg.text));
  $("live").append(p);
  while ($("live").children.length > 60) $("live").firstChild.remove();
  $("live").scrollTop = $("live").scrollHeight;
}

// --- Ask Bix ---------------------------------------------------------------------------
let askCloseT = null;
function askView(v) {
  $("askChoose").hidden = v !== "choose";
  $("askBusy").hidden = v !== "busy";
  $("askAnswer").hidden = v !== "answer";
  if (v !== "answer") clearTimeout(askCloseT);
}
function openAsk() { $("askErr").hidden = true; askView("choose"); $("askSheet").hidden = false; }
function closeAsk() { $("askSheet").hidden = true; clearTimeout(askCloseT); }
async function runAsk(fn, label) {
  $("askErr").hidden = true;
  askView("busy");
  $("askBusy").textContent = label || "Мисля";
  face.play([{ mood: "thinking", ms: 60000 }]);
  try {
    showAnswer(await fn());
  } catch (e) {
    askView("choose");
    $("askErr").textContent = e.message;
    $("askErr").hidden = false;
    face.play([{ mood: "confused", ms: 1500 }]);
  }
}
function showAnswer(a) {
  face.play([{ mood: a.kind === "answer" ? "happy" : "proud", ms: 1200, sparkle: a.kind === "answer" ? 0 : 8 }]);
  $("ansQ").textContent = a.question ? `„${a.question}“` : "";
  const noted = { task: "✓ Записах задача", decision: "✓ Записах решение", note: "✓ Записах бележка" }[a.kind];
  $("ansNoted").hidden = !noted;
  $("ansNoted").textContent = noted ? `${noted}: ${a.note}${a.owner ? ` (${a.owner}${a.due ? ", " + a.due : ""})` : ""}` : "";
  $("ansText").textContent = a.answer;
  $("ansPoints").innerHTML = "";
  for (const p of a.points || []) { const li = document.createElement("li"); li.textContent = p; $("ansPoints").append(li); }
  askView("answer");
  askCloseT = setTimeout(closeAsk, 60000); // nobody has to close it
}

// hold to ask out loud
const hold = $("holdAsk");
let holding = false;
hold.addEventListener("pointerdown", async (e) => {
  e.preventDefault();
  try { await post("/api/ask/listen"); holding = true; hold.classList.add("listening"); hold.textContent = "Слушам… пуснете, когато свършите"; }
  catch (err) { $("askErr").textContent = err.message; $("askErr").hidden = false; }
});
const release = () => {
  if (!holding) return;
  holding = false;
  hold.classList.remove("listening");
  hold.textContent = "🎤 Задръжте и питайте на глас";
  runAsk(() => post("/api/ask/voice"), "Слушах. Мисля");
};
hold.addEventListener("pointerup", release);
hold.addEventListener("pointerleave", release);
hold.addEventListener("pointercancel", release);

// --- confirm (in-page: kiosks have no dialogs) ------------------------------------------
function confirmIn(text, sub) {
  return new Promise((resolve) => {
    $("confirmText").textContent = text;
    $("confirmSub").textContent = sub || "";
    $("confirm").hidden = false;
    const done_ = (v) => { $("confirm").hidden = true; resolve(v); };
    $("confirmYes").onclick = () => done_(true);
    $("confirmNo").onclick = () => done_(false);
  });
}

// --- data ----------------------------------------------------------------------------------
async function refresh() {
  st = await backend.status();
  $("live").innerHTML = "";
  (st.live || []).forEach(addLive);
  render();
}

const handlers = {
  state: (d) => { st = d; render(); },
  level: (d) => face.setLevel(d),
  live: (d) => { addLive(d); face.event("live"); },
  uploaded: () => face.event("uploaded"),
  marked: () => face.play([{ mood: "happy", ms: 900, sparkle: 6, say: "Отбелязах ⭐" }]),
  presence: (d) => { if (st) { st.present = d.present; face.set(st); } face.presence(d.present); },
  radar: (d) => { if (st) st.distance_cm = d.cm; face.setDistance(d.cm); },
  sound: (d) => face.sound(d),
  identify: () => {
    const f = $("identify");
    f.hidden = false;
    clearTimeout(f._t);
    f._t = setTimeout(() => (f.hidden = true), 5000);
    face.event("identify");
  },
  meeting_ended: (d) => {
    face.event("ended", d);
    $("live").innerHTML = "";
    closeAsk();
    done = d;
    clearTimeout(doneT);
    doneT = setTimeout(() => { done = null; render(); }, 45000);
    render();
  },
};

function connect() {
  backend.subscribe((type, d) => handlers[type] && handlers[type](d), () => refresh().catch(() => {}));
}

function act(fn) {
  return async () => {
    try { await fn(); uiError = ""; } catch (e) { uiError = e.message; }
    await refresh();
  };
}

$("btnStart").onclick = act(() => post("/api/start"));
$("btnStartAdhoc").onclick = act(() => post("/api/start"));
$("btnStartEvent").onclick = act(() => post("/api/start", { event_uid: $("btnStartEvent").dataset.uid || "" }));
$("btnSuggestYes").onclick = act(() => post("/api/start", { event_uid: $("btnSuggestYes").dataset.uid || "" }));
$("btnSuggestNo").onclick = act(() => post("/api/suggest/dismiss"));
$("btnStop").onclick = act(() => post("/api/stop"));
$("btnEndNow").onclick = act(() => post("/api/stop"));
$("btnKeep").onclick = act(() => post("/api/keep"));
$("btnMute").onclick = act(() => post(st.muted ? "/api/unmute" : "/api/mute"));
$("btnResume").onclick = act(() => post("/api/unmute"));
$("btnStar").onclick = act(() => post("/api/mark"));
$("btnDiscard").onclick = act(async () => {
  if (await confirmIn("Да изтрия ли записа на тази среща?", "Всичко записано досега се изтрива веднага, тук и на сървъра. Нищо няма да остане.")) await post("/api/discard");
});
$("btnDone").onclick = () => { done = null; clearTimeout(doneT); render(); };
$("btnAsk").onclick = openAsk;
$("btnAskClose").onclick = closeAsk;
$("btnAskOk").onclick = closeAsk;
$("btnAskAgain").onclick = () => askView("choose");
document.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => runAsk(() => post("/api/ask", { preset: b.dataset.preset }), b.textContent)));
$("enroll").onsubmit = (e) => { e.preventDefault(); act(() => post("/api/enroll", { server: $("enServer").value.trim(), code: $("enCode").value.trim() }))(); };
if (backend.web) {
  // in the browser the server is this site: only the code is needed
  $("enServer").hidden = true;
  $("enServer").required = false;
  document.querySelector("#vEnroll .sub").textContent = "Администраторът дава код от портала: Устройства → Регистрирай устройство (с избрана зала). Кодът е еднократен.";
  document.querySelector("#vEnroll .title").textContent = "Свържете този лаптоп със залата";
}

// --- motion: a phone or tablet running the screen has a motion sensor (the Bix CRT has none) ---
(function motionSensor() {
  if (!("DeviceMotionEvent" in window)) return;
  let g = null, peaks = [], lastBump = 0, asked = false;
  const SHAKE = 12, BUMP = 18; // m/s² beyond gravity
  // iOS asks for permission, and only from a tap
  document.addEventListener("pointerdown", () => {
    if (asked || typeof DeviceMotionEvent.requestPermission !== "function") return;
    asked = true;
    DeviceMotionEvent.requestPermission().catch(() => {});
  });
  window.addEventListener("devicemotion", (e) => {
    const a = e.accelerationIncludingGravity;
    if (!a || a.x == null) return;
    const v = [a.x, a.y, a.z];
    g = g ? g.map((x, i) => x + (v[i] - x) * .08) : v.slice(); // gravity: the slow part
    const jolt = Math.hypot(v[0] - g[0], v[1] - g[1], v[2] - g[2]), t = performance.now() / 1000;
    if (jolt > SHAKE) {
      peaks = peaks.filter((p) => t - p < 1.2);
      if (!peaks.length || t - peaks[peaks.length - 1] > .12) peaks.push(t);
      if (peaks.length >= 4) { peaks = []; face.motion("shake"); }
      else if (jolt > BUMP && t - lastBump > 1.5) { lastBump = t; face.motion("bump"); }
    }
    // tilt across the screen (in screen coordinates, whatever the rotation)
    const ang = (((screen.orientation && screen.orientation.angle) || window.orientation || 0) * Math.PI) / 180;
    const across = -(g[0] * Math.cos(ang) - g[1] * Math.sin(ang)) / 9.81;
    face.motion("tilt", Math.abs(across) < .15 ? 0 : across);
  });
})();

function clock() {
  const d = new Date();
  $("clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (st && st.state !== "recording" && !done) renderIdle(); // "starts in…", the next booking
}
clock();
setInterval(clock, 15000);
refresh().then(connect);
