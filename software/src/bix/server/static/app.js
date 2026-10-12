"use strict";
// Bix portal. No framework, no external scripts (CSP: script-src 'self').

const $ = (sel, root = document) => root.querySelector(sel);
const view = () => $("#view");
let me = null;
let es = null;

// --- helpers ------------------------------------------------------------------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtTs = (s) => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60, p = (n) => String(n).padStart(2, "0"); return h ? `${h}:${p(m)}:${p(x)}` : `${p(m)}:${p(x)}`; };
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString("bg-BG", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
const ago = (t) => { if (!t) return "никога"; const d = Date.now() / 1000 - t; if (d < 60) return "току-що"; if (d < 3600) return `преди ${Math.floor(d / 60)} мин`; if (d < 86400) return `преди ${Math.floor(d / 3600)} ч`; return fmtDate(t); };
const STATUS = { recording: ["записва", "rec"], uploading: ["качва се", "warn"], queued: ["в опашка", "warn"], processing: ["обработва се", "warn"], done: ["готово", "ok"], failed: ["грешка", "bad"] };
const statusBadge = (s) => { const [t, c] = STATUS[s] || [s, ""]; return `<span class="badge ${c}">${esc(t)}</span>`; };
const STEP_NAMES = { assemble: "звук", calendar: "календар", stt: "реч → текст", speakers: "говорещи", minutes: "минути", deliver: "изпращане" };
const MODEL_NAMES = { vad: "VAD", segmentation: "сегментиране", embedding: "гласове" };
const ROLE_NAMES = { admin: "администратор", member: "член", auditor: "одитор" };
const spkColor = (label) => `var(--s${((parseInt(String(label).replace(/\D/g, "")) || 1) - 1) % 8 + 1})`;
const can = (...roles) => me && roles.includes(me.role);

function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Bix": "1" }, credentials: "same-origin" };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  if (r.status === 401 && !path.startsWith("/auth/")) {
    me = null;
    try { sessionStorage.setItem("bix_return", location.hash); } catch {}
    location.hash = "#/login"; throw new Error("Нужен е вход.");
  }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}

function modal(html, onMount) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal">${html}</div>`;
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { bg.remove(); document.removeEventListener("keydown", onKey); };
  document.addEventListener("keydown", onKey);
  bg.addEventListener("click", (e) => { if (e.target === bg) close(); });
  bg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  document.body.append(bg);
  if (onMount) onMount(bg.firstElementChild, close);
  return close;
}

const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

// --- routing --------------------------------------------------------------------
const routes = [
  [/^#\/login/, pageLogin], [/^#\/meetings\/([0-9a-f]{32})/, pageMeeting], [/^#\/meetings/, pageMeetings], [/^#\/rooms\/([0-9a-f]{32})/, pageRoomCalendar], [/^#\/rooms/, pageRooms],
  [/^#\/devices/, pageDevices], [/^#\/people/, pagePeople], [/^#\/users/, pageUsers], [/^#\/settings/, pageSettings], [/^#\/audit/, pageAudit], [/.*/, pageHome],
];

async function route() {
  const h = location.hash || "#/";
  $("#toast").hidden = true; // messages and dialogs belong to the page they came from
  document.querySelectorAll(".modal-bg").forEach((m) => m.remove());
  if (!me && !h.startsWith("#/login")) {
    try { me = await api("/auth/me"); } catch {
      // Remember where the user was going (e.g. a line link from the minutes e-mail).
      if (h !== "#/" && h !== "#") { try { sessionStorage.setItem("bix_return", h); } catch {} }
      location.hash = "#/login"; return;
    }
    startEvents();
    let back = null;
    try { back = sessionStorage.getItem("bix_return"); sessionStorage.removeItem("bix_return"); } catch {}
    if (back && back !== h) { location.hash = back; return; }
  }
  $("#nav").hidden = !me;
  if (me) {
    $("#meName").textContent = `${me.name || me.email} · ${ROLE_NAMES[me.role] || me.role}`;
    document.querySelectorAll("#nav a").forEach((a) => {
      const roles = a.dataset.role; a.hidden = roles ? !roles.split(" ").includes(me.role) : false;
      a.classList.toggle("active", h.startsWith("#/" + a.dataset.route) || (a.dataset.route === "home" && (h === "#/" || h === "#")));
    });
  }
  for (const [re, fn] of routes) { const m = h.match(re); if (m) { try { await fn(...m.slice(1)); } catch (e) { view().innerHTML = `<div class="empty">${esc(e.message)}</div>`; } wrapTables(); return; } }
}
window.addEventListener("hashchange", route);

// Wide tables scroll inside their own box instead of widening the page (phones).
function wrapTables() {
  document.querySelectorAll("#view table").forEach((t) => {
    if (t.parentElement.classList.contains("tw")) return;
    const w = document.createElement("div");
    w.className = "tw";
    t.replaceWith(w);
    w.append(t);
  });
}

function startEvents() {
  if (es) es.close();
  es = new EventSource("/api/events");
  es.addEventListener("meeting", (e) => { const d = JSON.parse(e.data); document.dispatchEvent(new CustomEvent("bix:meeting", { detail: d })); });
  es.addEventListener("device", () => document.dispatchEvent(new CustomEvent("bix:device")));
  es.addEventListener("live", (e) => document.dispatchEvent(new CustomEvent("bix:live", { detail: JSON.parse(e.data) })));
}

$("#logout").addEventListener("click", guard(async () => { await api("/auth/logout", { method: "POST" }); me = null; if (es) es.close(); location.hash = "#/login"; }));

// --- login / first setup ------------------------------------------------------------
async function pageLogin() {
  me = null; $("#nav").hidden = true;
  const cfg = await api("/auth/config");
  const err = new URLSearchParams(location.hash.split("?")[1] || "").get("error");
  if (cfg.setup) {
    view().innerHTML = `<div class="login card"><h1>Първо пускане</h1><p class="muted">Създайте организацията и първия администратор.</p>
      <form id="f"><label class="f">Организация<input name="org_name" required></label><label class="f">Вашето име<input name="name"></label>
      <label class="f">Имейл<input name="email" type="email" required></label><label class="f">Парола (поне 12 знака)<input name="password" type="password" minlength="12" required></label>
      <button class="primary">Създай</button></form></div>`;
    $("#f").addEventListener("submit", guard(async (e) => { e.preventDefault(); await api("/auth/setup", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); location.hash = "#/"; }));
    return;
  }
  view().innerHTML = `<div class="login card"><h1>Вход в Bix</h1>
    ${err ? `<p class="badge bad">Входът не успя (${esc(err)})</p>` : ""}
    ${cfg.oidc ? `<p><a class="btn primary" href="/auth/oidc/login">Вход с фирмения акаунт (SSO)</a></p>` : ""}
    ${cfg.local ? `<form id="f"><label class="f">Имейл<input name="email" type="email" autocomplete="username" required></label>
    <label class="f">Парола<input name="password" type="password" autocomplete="current-password" required></label><button class="primary">Вход</button></form>` : ""}</div>`;
  const f = $("#f");
  if (f) f.addEventListener("submit", guard(async (e) => { e.preventDefault(); await api("/auth/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); location.hash = "#/"; }));
}

// --- home ---------------------------------------------------------------------------
async function pageHome() {
  const [o, list] = await Promise.all([api("/api/overview"), api("/api/meetings?limit=8")]);
  $("#orgName").textContent = o.org;
  const stat = (n, l) => `<div class="card stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`;
  const deviceStats = can("admin", "auditor") ? `${stat(`${o.devices_online} / ${o.devices_total}`, "устройства онлайн")}${stat(o.recording_now, "записват в момента")}` : "";
  view().innerHTML = `<h1>Табло</h1><div class="grid">
      ${deviceStats}
      ${stat(o.meetings_30d, can("member") ? "мои срещи за 30 дни" : "срещи за 30 дни")}${stat(o.seconds_30d < 3600 ? Math.round(o.seconds_30d / 60) + " мин" : o.hours_30d + " ч", "записано за 30 дни")}
      ${stat(o.meetings_30d ? Math.round((o.with_minutes_30d / o.meetings_30d) * 100) + "%" : "—", "срещи с минути")}${stat(o.failed_30d, "неуспешни")}
    </div>
    <div class="row spread"><h2>Последни срещи</h2>${can("admin", "member") ? `<button id="up">Качи запис</button>` : ""}</div>
    ${meetingsTable(list.items)}`;
  bindMeetingRows();
  const up = $("#up"); if (up) up.addEventListener("click", uploadDialog);
  onMeetingEvent(pageHome);
}

let meetingListener = null;
function onMeetingEvent(fn) {
  if (meetingListener) document.removeEventListener("bix:meeting", meetingListener);
  let t = null;
  const hash = location.hash;
  meetingListener = () => { clearTimeout(t); t = setTimeout(() => { if (location.hash === hash) fn(); }, 800); };
  document.addEventListener("bix:meeting", meetingListener);
}

function meetingsTable(items) {
  if (!items.length) return `<div class="card empty">Още няма срещи. Започнете среща от устройство или качете запис.</div>`;
  return `<table><thead><tr><th>Среща</th><th>Зала</th><th>Начало</th><th>Продълж.</th><th>Говорещи</th><th>Статус</th></tr></thead><tbody>
    ${items.map((m) => `<tr class="click" data-id="${esc(m.id)}"><td><a class="rowlink" href="#/meetings/${esc(m.id)}">${esc(m.title)}</a>${m.error ? `<div class="small" style="color:var(--bad)">${esc(m.error)}</div>` : ""}</td>
      <td>${esc(m.room || (m.source === "upload" ? "качен запис" : ""))}</td><td>${fmtDate(m.started_at)}</td><td>${fmtTs(m.duration_s)}</td>
      <td class="small">${esc((m.speakers || []).join(", "))}</td><td>${statusBadge(m.status)}${m.sent_at ? ' <span class="badge">изпратено</span>' : ""}</td></tr>`).join("")}
  </tbody></table>`;
}
function bindMeetingRows() {
  document.querySelectorAll("tr.click[data-id]").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/meetings/${tr.dataset.id}`)));
}

// --- meetings list -----------------------------------------------------------------
async function pageMeetings() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = params.get("q") || "", room = params.get("room") || "";
  const [rooms, list] = await Promise.all([api("/api/rooms"), api(`/api/meetings?limit=200&q=${encodeURIComponent(q)}&room_id=${encodeURIComponent(room)}`)]);
  view().innerHTML = `<div class="row spread"><h1>Срещи</h1>${can("admin", "member") ? `<button id="up" class="primary">Качи запис</button>` : ""}</div>
    <form class="row" id="filt" style="margin-bottom:12px"><input name="q" placeholder="Търсене в заглавия и текст" value="${esc(q)}" style="flex:1;min-width:200px">
      <select name="room"><option value="">Всички зали</option>${rooms.map((r) => `<option value="${esc(r.id)}" ${r.id === room ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>
      <button>Търси</button></form>
    <p class="muted small">${list.total} срещи</p>${meetingsTable(list.items)}`;
  bindMeetingRows();
  $("#filt").addEventListener("submit", (e) => { e.preventDefault(); const f = new FormData(e.target); location.hash = `#/meetings?q=${encodeURIComponent(f.get("q"))}&room=${encodeURIComponent(f.get("room"))}`; });
  const up = $("#up"); if (up) up.addEventListener("click", uploadDialog);
  onMeetingEvent(pageMeetings);
}

async function uploadDialog() {
  const rooms = await api("/api/rooms");
  modal(`<h2>Качи запис</h2><p class="muted">Запис от телефон, Teams или Zoom (WAV, MP3, M4A, OGG, FLAC). Обработва се като среща от залата.</p>
    <form id="uf"><label class="f">Файл<input type="file" name="file" accept="audio/*,video/*" required></label>
    <label class="f">Заглавие<input name="title"></label>
    <label class="f">Зала<select name="room_id"><option value="">—</option>${rooms.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("")}</select></label>
    <div class="row"><button class="primary">Качи</button><button type="button" data-close>Отказ</button><span id="upst" class="muted"></span></div></form>`,
  (m, close) => {
    $("#uf", m).addEventListener("submit", guard(async (e) => {
      e.preventDefault(); $("#upst", m).textContent = "Качва се…"; e.target.querySelector("button").disabled = true;
      try { const r = await api("/api/meetings/upload", { method: "POST", body: new FormData(e.target) }); close(); location.hash = `#/meetings/${r.id}`; }
      finally { $("#upst", m).textContent = ""; e.target.querySelector("button").disabled = false; }
    }));
  });
}

// --- one meeting ---------------------------------------------------------------------
let meetingTab = "minutes";
async function pageMeeting(id) {
  const [m, tr, people] = await Promise.all([api(`/api/meetings/${id}`), api(`/api/meetings/${id}/transcript`), can("admin", "member") ? api("/api/people") : []]);
  const names = Object.fromEntries((m.speakers || []).map((s) => [s.label, s.name || s.label]));
  const busy = ["recording", "uploading", "queued", "processing"].includes(m.status);
  const steps = Object.entries(m.steps || {});
  view().innerHTML = `
    <div class="row spread"><div><a href="#/meetings" class="muted small">← Срещи</a>
      <h1 id="title" title="Кликнете за редакция" style="cursor:text">${esc(m.title)}</h1></div>
      <div class="row">
        ${m.minutes ? `<button id="send">Изпрати</button><a class="btn" href="/api/meetings/${id}/export.md">Свали .md</a>` : ""}
        <button id="more">Още…</button></div></div>
    <div class="row muted">${statusBadge(m.status)} <span>${esc(m.room || "")}</span><span>${fmtDate(m.started_at)}</span><span>${fmtTs(m.duration_s)}</span>
      ${m.sent_to.length ? `<span>Изпратено до ${esc(m.sent_to.join(", "))}</span>` : ""}</div>
    ${m.error ? `<p style="color:var(--bad)">${esc(m.error)}</p>` : ""}
    ${steps.length ? `<div class="steps">${steps.map(([k, v]) => `<span class="badge ${v.state === "done" ? "ok" : v.state === "failed" ? "bad" : "warn"}" title="${esc(v.error || v.skipped || "")}">${esc(STEP_NAMES[k] || k)}${v.took_s ? ` · ${v.took_s} с` : ""}</span>`).join("")}</div>` : ""}
    ${m.has_audio ? `<audio id="audio" controls preload="none" src="/api/meetings/${id}/audio"></audio>` : (m.audio_deleted_at ? `<p class="muted small">Звукът е изтрит на ${fmtDate(m.audio_deleted_at)} по политиката за съхранение.</p>` : "")}
    <div class="tabs"><button data-tab="minutes">Минути</button><button data-tab="transcript">Транскрипция</button><button data-tab="speakers">Говорещи (${(m.speakers || []).length})</button></div>
    <div id="tab"></div>`;

  const showTab = (t) => {
    meetingTab = t;
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    const el = $("#tab");
    if (t === "minutes") {
      const unnamed = (m.speakers || []).filter((x) => !x.name).length;
      const hint = unnamed && can("admin", "member") && m.speaker_mode !== "anonymous"
        ? `<div class="card hint row spread"><span>${unnamed === 1 ? "1 говорещ е" : `${unnamed} говорещи са`} без име. Чуйте ги и им дайте имена — после Bix ще ги познава сам.</span><button data-goto-tab="speakers">Дай имена</button></div>` : "";
      el.innerHTML = hint + renderMinutes(m, busy);
    }
    if (t === "transcript") el.innerHTML = renderTranscript(tr, names, busy);
    if (t === "speakers") el.innerHTML = renderSpeakers(m, people, tr);
    bindMeetingTab(id, m, people);
  };
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  const deep = location.hash.match(/#L(\d+)$/); // links from the e-mail: #/meetings/<id>#L12
  showTab(deep ? "transcript" : meetingTab);
  if (deep) jumpToLine(deep[1]);

  $("#title").addEventListener("click", guard(async () => {
    const t = prompt("Заглавие", m.title); if (t && t !== m.title) { await api(`/api/meetings/${id}`, { method: "PATCH", body: { title: t } }); pageMeeting(id); }
  }));
  const send = $("#send"); if (send) send.addEventListener("click", () => sendDialog(id));
  $("#more").addEventListener("click", () => moreDialog(id, m));
  onMeetingEvent(() => pageMeeting(id));
  const onLive = (e) => { if (e.detail.meeting_id === id && meetingTab === "transcript") pageMeeting(id); };
  document.addEventListener("bix:live", onLive, { once: true });
}

function refsHtml(refs) {
  if (!refs || !refs.length) return "";
  return ` <span class="refs">(${refs.map((n) => `<a href="#" data-ref="${n}">${n}</a>`).join(", ")})</span>`;
}

function renderMinutes(m, busy) {
  const x = m.minutes;
  if (!x) return `<div class="card empty">${busy ? "Минутите се подготвят. Страницата ще се обнови сама." : "Няма минути за тази среща."}</div>`;
  const list = (items, f) => items.length ? `<ul>${items.map(f).join("")}</ul>` : `<p class="muted">Няма</p>`;
  return `<div class="card minutes">
    <h3>Обобщение</h3><p>${esc(x.summary || "—")}</p>
    <h3>Решения</h3>${list(x.decisions, (d) => `<li>${esc(d.text)}${refsHtml(d.refs)}</li>`)}
    <h3>Задачи</h3>${list(x.action_items, (a) => `<li>${esc(a.task)}${a.owner ? ` — <b>${esc(a.owner)}</b>` : ""}${a.due ? ` <span class="badge">${esc(a.due)}</span>` : ""}${refsHtml(a.refs)}</li>`)}
    <h3>Отворени въпроси</h3>${list(x.open_questions, (q) => `<li>${esc(q.text)}${refsHtml(q.refs)}</li>`)}
    ${x.topics && x.topics.length ? `<h3>Теми</h3>${list(x.topics, (t) => `<li><b>${esc(t.title)}</b>: ${esc(t.summary)}${refsHtml(t.refs)}</li>`)}` : ""}
    <p class="muted small">Направено автоматично от ИИ. Проверете важните неща в транскрипцията по номерата на редовете.</p></div>`;
}

function renderTranscript(tr, names, busy) {
  const segs = tr.segments || [];
  if (!segs.length) {
    if (tr.live && tr.live.length) return `<div class="card"><p class="muted small">Текст на живо (предварителен, без говорещи):</p><div class="transcript">${tr.live.map((s) => `<p><span class="n"></span><span class="t" data-seek="${s.start}">${fmtTs(s.start)}</span><span class="s"></span><span>${esc(s.text)}</span></p>`).join("")}</div></div>`;
    return `<div class="card empty">${busy ? "Транскрипцията се подготвя." : "Няма транскрипция."}</div>`;
  }
  return `<div class="card transcript">${segs.map((s, i) => `<p id="L${i + 1}"><span class="n">${i + 1}</span><span class="t" data-seek="${s.start}">${fmtTs(s.start)}</span>
    <span class="s" style="color:${spkColor(s.speaker)}">${esc(names[s.speaker] || s.speaker || "")}</span><span>${esc(s.text)}</span></p>`).join("")}</div>`;
}

function renderSpeakers(m, people, tr) {
  const sp = m.speakers || [];
  if (!sp.length) return `<div class="card empty">Говорещите се определят след транскрипцията.</div>`;
  const writable = can("admin", "member");
  const hasAudio = m.has_audio;
  // what each speaker said: their longest lines, so you can recognise who it is
  const quotes = {};
  for (const seg of (tr && tr.segments) || []) {
    (quotes[seg.speaker] = quotes[seg.speaker] || []).push(seg);
  }
  for (const k of Object.keys(quotes)) {
    quotes[k] = quotes[k].filter((q) => q.text.split(/\s+/).length >= 3).sort((a, b) => (b.end - b.start) - (a.end - a.start)).slice(0, 3).sort((a, b) => a.start - b.start);
  }
  const who = (s) => {
    if (s.source === "profile") return `<span class="badge ok">✓ разпознат автоматично по гласа${s.score ? ` · ${Math.round(s.score * 100)}%` : ""}</span>`;
    if (s.source === "llm") return `<span class="badge" title="${esc(s.evidence || "")}">името е от разговора — проверете</span>`;
    if (s.source === "user") return `<span class="badge">зададено ръчно</span>`;
    return `<span class="badge warn">без име</span>`;
  };
  return `<div class="card">
    <p class="small" style="margin-top:0"><b>Кой кой е?</b> Чуйте (▶) или прочетете какво е казал всеки говорещ, после изберете човек или въведете име.
    С отметка <b>„запомни гласа“</b> Bix пази <i>гласов профил</i> на човека — числов отпечатък на гласа, не запис — и <b>в следващите срещи го разпознава сам</b>.
    Отметнете я само ако човекът е съгласен. Профилите се виждат и трият в „Хора и гласове“.</p>
    ${sp.map((s) => `<div class="spk"><span class="dot" style="background:${spkColor(s.label)}"></span><b>${esc(s.label)}</b>
      <div><div class="row"><b>${esc(s.name || "—")}</b>${who(s)}<span class="muted small">${fmtTs(s.seconds)} реч</span></div>
      ${s.source === "llm" && s.evidence ? `<div class="muted small">${esc(s.evidence)}</div>` : ""}
      <div class="quotes">${(quotes[s.label] || []).map((q) => `<div class="quote">${hasAudio ? `<button class="play" data-play="${q.start}" data-until="${q.end}" title="Чуй този момент">▶ ${fmtTs(q.start)}</button>` : `<span class="muted small">${fmtTs(q.start)}</span>`}<span>„${esc(q.text)}“</span></div>`).join("") || `<div class="muted small">Няма достатъчно дълги реплики.</div>`}</div>
      ${writable ? `<form class="row spkform" data-label="${esc(s.label)}" style="margin-top:6px">
        <select name="person_id" aria-label="Човек"><option value="">— човек от списъка —</option>${people.map((p) => `<option value="${esc(p.id)}" ${p.id === s.person_id ? "selected" : ""}>${esc(p.name)}${p.has_voice ? " 🎙" : ""}</option>`).join("")}</select>
        <input name="name" placeholder="или ново име" aria-label="Ново име" value="">
        <input name="email" type="email" placeholder="имейл (по избор)" aria-label="Имейл">
        ${m.speaker_mode === "anonymous" ? "" : `<label class="small"><input type="checkbox" name="remember"> запомни гласа (човекът е съгласен)</label>`}
        <button>Запази</button></form>` : ""}</div></div>`).join("")}
    ${writable ? `<p class="row" style="margin-top:12px"><button id="regen">Обнови минутите с новите имена</button></p>` : ""}</div>`;
}

// play only [start, end] of the meeting audio
function playSpan(start, end) {
  const a = $("#audio");
  if (!a) return;
  const go = () => { a.currentTime = start; a._stopAt = end + 0.3; a.play(); };
  if (!a._spanHook) {
    a._spanHook = true;
    a.addEventListener("timeupdate", () => { if (a._stopAt && a.currentTime >= a._stopAt) { a.pause(); a._stopAt = null; } });
    a.addEventListener("seeking", () => { if (a._stopAt && (a.currentTime < start - 1 || a.currentTime > a._stopAt)) a._stopAt = null; });
  }
  if (a.readyState < 1) { a.addEventListener("loadedmetadata", go, { once: true }); a.load(); } else go();
}

function bindMeetingTab(id, m) {
  document.querySelectorAll("[data-seek]").forEach((el) => el.addEventListener("click", () => {
    const a = $("#audio"); if (!a) return; a.currentTime = parseFloat(el.dataset.seek); a.play();
  }));
  document.querySelectorAll("[data-play]").forEach((el) => el.addEventListener("click", () => playSpan(parseFloat(el.dataset.play), parseFloat(el.dataset.until))));
  document.querySelectorAll("[data-goto-tab]").forEach((el) => el.addEventListener("click", () => document.querySelector(`.tabs button[data-tab="${el.dataset.gotoTab}"]`).click()));
  document.querySelectorAll("[data-ref]").forEach((el) => el.addEventListener("click", (e) => {
    e.preventDefault();
    document.querySelector('.tabs button[data-tab="transcript"]').click();
    jumpToLine(el.dataset.ref);
  }));
  document.querySelectorAll(".spkform").forEach((f) => f.addEventListener("submit", guard(async (e) => {
    e.preventDefault();
    const d = new FormData(f);
    const remember = !!d.get("remember");
    const body = { label: f.dataset.label, person_id: d.get("person_id") || "", name: (d.get("name") || "").trim(), email: (d.get("email") || "").trim(), remember, consent: remember };
    if (!body.person_id && !body.name) throw new Error("Изберете човек или въведете име.");
    await api(`/api/meetings/${id}/speakers`, { method: "POST", body });
    const who = body.name || f.querySelector("select option:checked").textContent.replace(" 🎙", "");
    toast(remember ? `Запазено. Гласът на ${who} е запомнен — в следващите срещи Bix ще го разпознае сам.` : `Запазено: ${f.dataset.label} = ${who}.`, 6000);
    pageMeeting(id);
  })));
  const regen = $("#regen");
  if (regen) regen.addEventListener("click", guard(async () => { await api(`/api/meetings/${id}/reprocess`, { method: "POST", body: { step: "minutes" } }); toast("Минутите се обновяват…"); pageMeeting(id); }));
}

function jumpToLine(n) {
  const line = document.getElementById("L" + n);
  if (!line) return;
  document.querySelectorAll(".transcript p.hl").forEach((p) => p.classList.remove("hl"));
  line.classList.add("hl");
  line.scrollIntoView({ block: "center" });
}

function sendDialog(id) {
  modal(`<h2>Изпрати минутите</h2><p class="muted">Празно = на получателите по правилата (организатор, участници, зала).</p>
    <form id="sf"><label class="f">Допълнителни имейли (със запетая)<input name="to" placeholder="ime@firma.bg, ..."></label>
    <div class="row"><button class="primary">Изпрати</button><button type="button" data-close>Отказ</button></div></form>`, (m, close) => {
    $("#sf", m).addEventListener("submit", guard(async (e) => {
      e.preventDefault();
      const to = (new FormData(e.target).get("to") || "").split(/[,;\s]+/).filter(Boolean);
      const r = await api(`/api/meetings/${id}/send`, { method: "POST", body: { to } });
      close(); toast(`Изпратено до ${r.sent_to.join(", ")}`); pageMeeting(id);
    }));
  });
}

function moreDialog(id, m) {
  modal(`<h2>Действия</h2>
    <p><button data-step="minutes">Нови минути</button> <span class="muted small">без нова транскрипция</span></p>
    <p><button data-step="speakers">Наново: говорещи и минути</button></p>
    <p><button data-step="stt" ${m.has_audio ? "" : "disabled"}>Наново: всичко от звука</button> <span class="muted small">плаща се разпознаването на реч</span></p>
    ${can("admin") ? `<hr><p><button class="danger" id="del">Изтрий срещата</button> <span class="muted small">звук, текст и минути; не може да се върне</span></p>` : ""}
    <p><button data-close>Затвори</button></p>`, (el, close) => {
    el.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", guard(async () => {
      await api(`/api/meetings/${id}/reprocess`, { method: "POST", body: { step: b.dataset.step } }); close(); toast("Пуснато."); pageMeeting(id);
    })));
    const del = $("#del", el);
    if (del) del.addEventListener("click", guard(async () => {
      if (!confirm("Сигурни ли сте? Срещата ще бъде изтрита завинаги.")) return;
      await api(`/api/meetings/${id}`, { method: "DELETE" }); close(); location.hash = "#/meetings";
    }));
  });
}

// --- rooms ---------------------------------------------------------------------------
async function pageRooms() {
  const rooms = await api("/api/rooms");
  view().innerHTML = `<div class="row spread"><h1>Зали</h1><button class="primary" id="add">Нова зала</button></div>
    ${rooms.length ? `<table><thead><tr><th>Зала</th><th>Място</th><th>Календар</th><th>Получатели на минутите</th><th></th></tr></thead><tbody>
    ${rooms.map((r) => `<tr><td><b>${esc(r.name)}</b></td><td>${esc(r.location)}</td><td class="small">${esc([r.has_ics ? "iCal" : "", r.mailbox ? "Microsoft 365" : ""].filter(Boolean).join(" + ") || "само резервации в Bix")}</td><td class="small">${esc((r.recipients || []).join(", ") || "—")}</td>
      <td class="nowrap"><a class="link" href="#/rooms/${esc(r.id)}">Календар</a> ${can("admin") ? `<button class="link" data-edit="${esc(r.id)}">Редакция</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="card empty">Няма зали.</div>`}`;
  $("#add").hidden = !can("admin");
  $("#add").addEventListener("click", () => roomDialog());
  document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => roomDialog(rooms.find((r) => r.id === b.dataset.edit))));
}

function roomDialog(r) {
  r = r || { name: "", location: "", mailbox: "", recipients: [] };
  modal(`<h2>${r.id ? "Зала" : "Нова зала"}</h2><form id="rf">
    <label class="f">Име<input name="name" value="${esc(r.name)}" required></label>
    <label class="f">Място<input name="location" value="${esc(r.location)}" placeholder="София, ет. 3"></label>
    <label class="f">Таен iCal адрес на календара на залата (Google Calendar: Настройки → „Таен адрес в iCal формат“; Outlook: „Публикуване на календар“ → ICS)<input name="calendar_ics_url" value="${esc(r.calendar_ics_url || "")}" placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" autocomplete="off"></label>
    <label class="f">или пощенска кутия на залата в Microsoft 365 (нужна е настройка [graph] на сървъра)<input name="mailbox" value="${esc(r.mailbox)}" placeholder="zala.vitosha@firma.bg"></label>
    <label class="f">Минутите винаги и до (имейли със запетая)<input name="recipients" value="${esc((r.recipients || []).join(", "))}"></label>
    <div class="row"><button class="primary">Запази</button><button type="button" data-close>Отказ</button>${r.id ? `<button type="button" class="danger" id="rdel">Изтрий</button>` : ""}</div></form>`, (m, close) => {
    $("#rf", m).addEventListener("submit", guard(async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const body = { name: f.get("name"), location: f.get("location"), mailbox: f.get("mailbox"), calendar_ics_url: f.get("calendar_ics_url") || "", recipients: (f.get("recipients") || "").split(/[,;\s]+/).filter(Boolean) };
      await api(r.id ? `/api/rooms/${r.id}` : "/api/rooms", { method: r.id ? "PATCH" : "POST", body }); close(); pageRooms();
    }));
    const d = $("#rdel", m);
    if (d) d.addEventListener("click", guard(async () => { if (confirm("Да изтрия ли залата?")) { await api(`/api/rooms/${r.id}`, { method: "DELETE" }); close(); pageRooms(); } }));
  });
}

// --- room calendar ---------------------------------------------------------------------
const SOURCE_NAMES = { bix: "резервация в Bix", ics: "iCal", m365: "Microsoft 365" };
async function pageRoomCalendar(rid) {
  const [rooms, cal] = await Promise.all([api("/api/rooms"), api(`/api/rooms/${rid}/calendar?days=7`)]);
  const room = rooms.find((r) => r.id === rid);
  if (!room) { view().innerHTML = `<div class="card empty">Няма такава зала.</div>`; return; }
  const day = (t) => new Date(t * 1000).toLocaleDateString("bg-BG", { weekday: "long", day: "numeric", month: "long" });
  const hm = (t) => new Date(t * 1000).toLocaleTimeString("bg-BG", { hour: "2-digit", minute: "2-digit" });
  const byDay = {};
  for (const e of cal.events) (byDay[day(e.start)] = byDay[day(e.start)] || []).push(e);
  const src = Object.entries(cal.sources).filter(([, on]) => on).map(([k]) => ({ bookings: "резервации в Bix", ics: "iCal", m365: "Microsoft 365" }[k]));
  view().innerHTML = `<p><a href="#/rooms">← Зали</a></p><div class="row spread"><h1>${esc(room.name)}: календар</h1>${can("admin", "member") ? `<button class="primary" id="book">Нова резервация</button>` : ""}</div>
    <p class="muted">Източници: ${esc(src.join(", "))}. Екранът в залата показва срещата сега и следващите, и започва точно нея с едно докосване.</p>
    ${cal.problem ? `<div class="card hint">⚠ Календарът не се чете: ${esc(cal.problem)}</div>` : ""}
    ${cal.events.length ? Object.entries(byDay).map(([d, evs]) => `<h2>${esc(d)}</h2><table><tbody>${evs.map((e) => `<tr>
      <td class="nowrap">${hm(e.start)}–${hm(e.end)}</td><td><b>${esc(e.title || "Среща")}</b><div class="small muted">${esc([e.organizer.name || e.organizer.email, ...(e.attendees || []).map((a) => a.name || a.email)].filter(Boolean).join(", "))}</div></td>
      <td class="small muted">${esc(SOURCE_NAMES[e.source] || e.source)}</td>
      <td>${e.booking_id && can("admin", "member") ? `<button class="link" data-del="${esc(e.booking_id)}">Изтрий</button>` : ""}</td></tr>`).join("")}</tbody></table>`).join("")
      : `<div class="card empty">Няма резервации за следващите 7 дни.</div>`}`;
  const b = $("#book");
  if (b) b.addEventListener("click", () => bookingDialog(room));
  document.querySelectorAll("[data-del]").forEach((x) => x.addEventListener("click", guard(async () => {
    if (!confirm("Да изтрия ли резервацията? Ако е седмична, изчезват всички повторения.")) return;
    await api(`/api/bookings/${x.dataset.del}`, { method: "DELETE" }); pageRoomCalendar(rid);
  })));
}

function bookingDialog(room) {
  const d = new Date(Date.now() + 5 * 60000);
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const from = `${pad(d.getHours())}:${pad(Math.floor(d.getMinutes() / 5) * 5)}`;
  modal(`<h2>Нова резервация: ${esc(room.name)}</h2><form id="bf">
    <label class="f">Тема<input name="title" required placeholder="Седмичен преглед"></label>
    <div class="row"><label class="f">Ден<input type="date" name="date" value="${date}" required></label>
      <label class="f">От<input type="time" name="from" value="${from}" required></label>
      <label class="f">Продължителност<select name="dur"><option value="30">30 мин</option><option value="60" selected>1 час</option><option value="90">1,5 часа</option><option value="120">2 часа</option></select></label></div>
    <div class="row"><label class="f">Организатор<input name="organizer_name" value="${esc(me.name || "")}"></label><label class="f">Имейл на организатора<input name="organizer_email" type="email" value="${esc(me.email || "")}"></label></div>
    <label class="f">Участници (по един на ред: „Име &lt;имейл&gt;“, имейл или само име)<textarea name="attendees" rows="3"></textarea></label>
    <label class="c"><input type="checkbox" name="weekly"> Всяка седмица по това време</label>
    <div class="row"><button class="primary">Запази</button><button type="button" data-close>Отказ</button></div></form>`, (m, close) => {
    $("#bf", m).addEventListener("submit", guard(async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const start = new Date(`${f.get("date")}T${f.get("from")}`).getTime() / 1000;
      await api(`/api/rooms/${room.id}/bookings`, { method: "POST", body: {
        title: f.get("title"), start, end: start + 60 * +f.get("dur"), organizer_name: f.get("organizer_name"), organizer_email: f.get("organizer_email"),
        attendees: (f.get("attendees") || "").split(/\n+/).map((x) => x.trim()).filter(Boolean), repeat: f.get("weekly") ? "weekly" : "",
      } });
      close(); pageRoomCalendar(room.id); toast("Резервацията е запазена. Екранът в залата ще я покаже до 15 секунди.");
    }));
  });
}

// --- devices ----------------------------------------------------------------------
async function pageDevices() {
  const [devs, rooms] = await Promise.all([api("/api/devices"), api("/api/rooms")]);
  const admin = can("admin");
  view().innerHTML = `<div class="row spread"><h1>Устройства</h1>${admin ? `<button class="primary" id="code">Регистрирай устройство</button>` : ""}</div>
    ${devs.length ? `<table><thead><tr><th>Устройство</th><th>Зала</th><th>Състояние</th><th>Версия</th><th>Последно</th><th>Данни</th>${admin ? "<th></th>" : ""}</tr></thead><tbody>
    ${devs.map((d) => `<tr><td><b>${esc(d.name)}</b><div class="muted small">${esc(d.serial)} · ${esc(d.platform)}</div></td>
      <td>${admin && !d.revoked ? `<select data-room="${esc(d.id)}"><option value="">—</option>${rooms.map((r) => `<option value="${esc(r.id)}" ${r.id === d.room_id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>` : esc(d.room)}</td>
      <td>${d.revoked ? '<span class="badge bad">отнето</span>' : d.online ? (d.state === "recording" ? '<span class="badge rec">записва</span>' : d.state === "muted" ? '<span class="badge warn">пауза</span>' : '<span class="badge ok">онлайн</span>') : '<span class="badge bad">без връзка</span>'}</td>
      <td class="small">${esc(d.fw_version)}</td><td class="small">${ago(d.last_seen)}<div class="muted">${esc(d.ip)}</div></td>
      <td class="small muted">${d.telemetry.backlog_s ? `за качване ${fmtTs(d.telemetry.backlog_s)}<br>` : ""}${d.telemetry.mic ? esc(d.telemetry.mic) + "<br>" : ""}${d.telemetry.cpu_temp_c ? d.telemetry.cpu_temp_c + " °C · " : ""}${d.telemetry.disk_free_gb != null ? d.telemetry.disk_free_gb + " GB свободни" : ""}</td>
      ${admin ? `<td>${d.revoked ? "" : `<select data-cmd="${esc(d.id)}"><option value="">Команда…</option><option value="start">Започни запис</option><option value="stop">Спри запис</option><option value="mute">Пауза</option><option value="unmute">Продължи</option><option value="identify">Покажи се (LED)</option><option value="restart">Рестарт на услугата</option></select>
        <button class="link danger" data-revoke="${esc(d.id)}">Отнеми достъпа</button>`}</td>` : ""}</tr>`).join("")}</tbody></table>`
      : `<div class="card empty">Няма регистрирани устройства.</div>`}`;
  const code = $("#code"); if (code) code.addEventListener("click", () => codeDialog(rooms));
  document.querySelectorAll("[data-room]").forEach((s) => s.addEventListener("change", guard(async () => { await api(`/api/devices/${s.dataset.room}`, { method: "PATCH", body: { room_id: s.value } }); toast("Залата е сменена."); })));
  document.querySelectorAll("[data-cmd]").forEach((s) => s.addEventListener("change", guard(async () => { if (!s.value) return; await api(`/api/devices/${s.dataset.cmd}/command`, { method: "POST", body: { type: s.value } }); toast("Командата е изпратена."); s.value = ""; })));
  document.querySelectorAll("[data-revoke]").forEach((b) => b.addEventListener("click", guard(async () => { if (confirm("Устройството няма да може да се свързва повече. Продължаване?")) { await api(`/api/devices/${b.dataset.revoke}/revoke`, { method: "POST" }); pageDevices(); } })));
  const h = () => { if (location.hash.startsWith("#/devices") && !document.querySelector(".modal-bg")) pageDevices(); };
  clearTimeout(pageDevices._t); pageDevices._t = setTimeout(h, 15000);
  // a device reports a state change (recording, paused...) → redraw soon
  if (!pageDevices._live) {
    pageDevices._live = true;
    document.addEventListener("bix:device", () => { clearTimeout(pageDevices._t); pageDevices._t = setTimeout(h, 400); });
  }
}

function codeDialog(rooms) {
  modal(`<h2>Регистрирай устройство</h2><form id="cf"><label class="f">Зала<select name="room_id"><option value="">—</option>${rooms.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("")}</select></label>
    <div class="row"><button class="primary">Създай код</button><button type="button" data-close>Отказ</button></div></form><div id="cres"></div>`, (m) => {
    $("#cf", m).addEventListener("submit", guard(async (e) => {
      e.preventDefault();
      const r = await api("/api/devices/enrollment-codes", { method: "POST", body: { room_id: new FormData(e.target).get("room_id") } });
      $("#cres", m).innerHTML = `<p>Кодът важи до ${fmtDate(r.expires_at)} и е за еднократна употреба:</p><div class="code">${esc(r.code)}</div>
        <p><b>Лаптоп в залата (без инсталация):</b> отворете <a href="${esc(r.server)}/room" target="_blank" rel="noopener">${esc(r.server)}/room</a> в Chrome или Edge и въведете кода.</p>
        <p class="muted small">Устройство Bix: въведете сървъра и кода на екрана му, или изпълнете:</p>
        <pre class="json">bix device enroll --server ${esc(r.server)} --code ${esc(r.code)}</pre>`;
      e.target.hidden = true;
    }));
  });
}

// --- people & voice profiles --------------------------------------------------------
async function pagePeople() {
  const people = await api("/api/people");
  const admin = can("admin");
  view().innerHTML = `<div class="row spread"><h1>Хора и гласове</h1>${admin ? `<button class="primary" id="add">Нов човек</button>` : ""}</div>
    <p class="muted">Гласов профил се пази само със съгласието на човека. Изтриването на човек маха профила и името му от всички срещи (право на изтриване по GDPR).</p>
    ${people.length ? `<table><thead><tr><th>Име</th><th>Имейл</th><th>Глас</th><th>Съгласие</th>${admin ? "<th></th>" : ""}</tr></thead><tbody>
    ${people.map((p) => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.email)}</td>
      <td>${p.has_voice ? `<span class="badge ok">има профил</span> <span class="muted small">${p.samples} проби · ${fmtTs(p.seconds)}</span>` : '<span class="badge">няма</span>'}</td>
      <td class="small">${p.consent_at ? `${fmtDate(p.consent_at)}<div class="muted">${esc(p.consent_by)}</div>` : "—"}</td>
      ${admin ? `<td class="row"><button class="link" data-voice="${esc(p.id)}">Проба на гласа</button>${p.has_voice ? `<button class="link" data-forget="${esc(p.id)}">Забрави гласа</button>` : ""}<button class="link danger" data-erase="${esc(p.id)}">Изтрий</button></td>` : ""}</tr>`).join("")}</tbody></table>`
      : `<div class="card empty">Няма хора. Добавете ги тук или им дайте имена в страницата на срещата.</div>`}`;
  const add = $("#add");
  if (add) add.addEventListener("click", () => modal(`<h2>Нов човек</h2><form id="pf"><label class="f">Име<input name="name" required></label><label class="f">Имейл<input name="email" type="email"></label>
    <div class="row"><button class="primary">Добави</button><button type="button" data-close>Отказ</button></div></form>`, (m, close) => {
    $("#pf", m).addEventListener("submit", guard(async (e) => { e.preventDefault(); await api("/api/people", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); close(); pagePeople(); }));
  }));
  document.querySelectorAll("[data-voice]").forEach((b) => b.addEventListener("click", () => modal(`<h2>Проба на гласа</h2>
    <p class="muted">Запис от 15–60 секунди, в който говори само този човек (например прочита абзац).</p>
    <form id="vf"><label class="f">Файл<input type="file" name="file" accept="audio/*" required></label>
    <label class="c"><input type="checkbox" name="consent" value="true" required> Човекът е съгласен Bix да пази профил на гласа му, за да го разпознава в срещите.</label>
    <div class="row"><button class="primary">Запази</button><button type="button" data-close>Отказ</button></div></form>`, (m, close) => {
    $("#vf", m).addEventListener("submit", guard(async (e) => { e.preventDefault(); await api(`/api/people/${b.dataset.voice}/voice`, { method: "POST", body: new FormData(e.target) }); close(); toast("Профилът е обновен."); pagePeople(); }));
  })));
  document.querySelectorAll("[data-forget]").forEach((b) => b.addEventListener("click", guard(async () => { if (confirm("Да изтрия ли гласовия профил?")) { await api(`/api/people/${b.dataset.forget}/voice`, { method: "DELETE" }); pagePeople(); } })));
  document.querySelectorAll("[data-erase]").forEach((b) => b.addEventListener("click", guard(async () => {
    if (!confirm("Да изтрия ли човека, гласовия му профил и името му от всички срещи?")) return;
    const r = await api(`/api/people/${b.dataset.erase}`, { method: "DELETE" }); toast(`Изтрито. Засегнати срещи: ${r.meetings_touched}`); pagePeople();
  })));
}

// --- users ------------------------------------------------------------------------------
async function pageUsers() {
  const users = await api("/api/users");
  view().innerHTML = `<div class="row spread"><h1>Потребители</h1><button class="primary" id="add">Нов потребител</button></div>
    <p class="muted">Администраторът вижда и управлява всичко. Членът вижда срещите, в които е участвал. Одиторът вижда само одита и метаданни, без съдържание.</p>
    <table><thead><tr><th>Имейл</th><th>Име</th><th>Роля</th><th>Вход</th><th>Последен вход</th><th></th></tr></thead><tbody>
    ${users.map((u) => `<tr><td>${esc(u.email)}</td><td>${esc(u.name)}</td>
      <td><select data-role="${esc(u.id)}">${Object.entries(ROLE_NAMES).map(([k, v]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${v}</option>`).join("")}</select></td>
      <td class="small">${u.sso ? "SSO" : "парола"}</td><td class="small">${ago(u.last_login)}</td>
      <td><button class="link" data-active="${esc(u.id)}" data-v="${u.active ? "0" : "1"}">${u.active ? "Спри достъпа" : "Върни достъпа"}</button></td></tr>`).join("")}</tbody></table>`;
  $("#add").addEventListener("click", () => modal(`<h2>Нов потребител</h2><form id="uf">
    <label class="f">Имейл<input name="email" type="email" required></label><label class="f">Име<input name="name"></label>
    <label class="f">Роля<select name="role">${Object.entries(ROLE_NAMES).map(([k, v]) => `<option value="${k}" ${k === "member" ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label class="f">Парола (празно = само SSO вход)<input name="password" type="password" minlength="12"></label>
    <div class="row"><button class="primary">Създай</button><button type="button" data-close>Отказ</button></div></form>`, (m, close) => {
    $("#uf", m).addEventListener("submit", guard(async (e) => { e.preventDefault(); await api("/api/users", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); close(); pageUsers(); }));
  }));
  document.querySelectorAll("select[data-role]").forEach((s) => s.addEventListener("change", guard(async () => { await api(`/api/users/${s.dataset.role}`, { method: "PATCH", body: { role: s.value } }); toast("Ролята е сменена."); })));
  document.querySelectorAll("[data-active]").forEach((b) => b.addEventListener("click", guard(async () => { await api(`/api/users/${b.dataset.active}`, { method: "PATCH", body: { active: b.dataset.v === "1" } }); pageUsers(); })));
}

// --- settings -----------------------------------------------------------------------------
async function pageSettings() {
  const s = await api("/api/settings");
  const st = can("admin") ? await api("/api/status").catch(() => null) : null;
  const v = s.settings;
  const admin = can("admin");
  const dis = admin ? "" : "disabled";
  const statusRow = (k, label) => st && st[k] ? `<tr><td>${label}</td><td><span class="badge ${st[k].ok ? "ok" : "bad"}">${st[k].ok ? "OK" : "проблем"}</span></td><td>${esc(st[k].provider || "")}</td><td class="small">${esc(typeof st[k].detail === "object" ? Object.entries(st[k].detail).map(([n, ok]) => `${MODEL_NAMES[n] || n} ${ok ? "✓" : "липсва"}`).join(" · ") : st[k].detail)}</td></tr>` : "";
  view().innerHTML = `<h1>Настройки</h1>
    <form id="sf" class="card">
      <label class="f">Организация<input name="org_name" value="${esc(s.org_name)}" ${dis}></label>
      <label class="f">Език на срещите<select name="language" ${dis}>${[["bg", "български"], ["en", "английски"], ["de", "немски"], ["ro", "румънски"]].map(([k, n]) => `<option value="${k}" ${v.language === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label class="f">Как се разпознават говорещите<select name="speaker_mode" ${dis}>
        <option value="voice_profiles" ${v.speaker_mode === "voice_profiles" ? "selected" : ""}>Гласови профили (със съгласие) + календар</option>
        <option value="calendar_direction" ${v.speaker_mode === "calendar_direction" ? "selected" : ""}>Само календар и разговор, без гласови профили</option>
        <option value="anonymous" ${v.speaker_mode === "anonymous" ? "selected" : ""}>Анонимно (Говорещ 1, 2, …)</option></select></label>
      <div class="row"><label class="f">Звукът се трие след (дни, 0 = никога)<input type="number" name="retention_audio_days" value="${v.retention_audio_days}" min="0" ${dis}></label>
        <label class="f">Текстът и минутите се трият след (дни, 0 = никога)<input type="number" name="retention_text_days" value="${v.retention_text_days}" min="0" ${dis}></label></div>
      <label class="c"><input type="checkbox" name="live_transcription" ${v.live_transcription ? "checked" : ""} ${dis}> Текст на живо на екрана на залата (около 4 пъти по-скъпо от обработка след срещата)</label>
      <label class="c"><input type="checkbox" name="auto_send" ${v.auto_send ? "checked" : ""} ${dis}> Минутите се пращат автоматично след срещата</label>
      <label class="c"><input type="checkbox" name="members_see_all_meetings" ${v.members_see_all_meetings ? "checked" : ""} ${dis}> Членовете виждат всички срещи (иначе само своите)</label>
      <label class="f">Към кого се пращат<span class="row">${[["organizer", "организатора"], ["attendees", "участниците"], ["room", "получателите на залата"], ["fixed", "фиксирани адреси"]].map(([k, n]) => `<label class="small"><input type="checkbox" name="send_to" value="${k}" ${(v.send_to || []).includes(k) ? "checked" : ""} ${dis}> ${n}</label>`).join("")}</span></label>
      <label class="f">Фиксирани адреси<input name="fixed_recipients" value="${esc((v.fixed_recipients || []).join(", "))}" ${dis}></label>
      <div class="row"><label class="f">Край на срещата след минути без реч (0 = изключено)<input type="number" name="auto_stop_silence_min" value="${v.auto_stop_silence_min}" min="0" ${dis}></label>
        <label class="f">Максимална продължителност (часове)<input type="number" name="max_meeting_hours" value="${v.max_meeting_hours}" min="1" ${dis}></label></div>
      <label class="f">Текст на екрана по време на запис (AI Act, чл. 50)<textarea name="notice_text" rows="2" ${dis}>${esc(v.notice_text)}</textarea></label>
      <label class="f">Речник: имена, продукти, съкращения
        <textarea name="glossary" rows="7" ${dis} placeholder="Пощенска банка&#10;ДСК: ди ес ке, DSK&#10;Мария Петрова">${esc((v.glossary || []).join("\n"))}</textarea>
        <span class="muted small">По един на ред. След двоеточие: как разпознаването на реч го пише грешно (по желание). Речникът помага на разпознаването, поправя текста и минутите.</span></label>
      <label class="f">Лице на екрана в залата<select name="face_style" ${dis}>${[["pixel", "LED матрица"], ["capsule", "Капсули"], ["eve", "Овали"], ["dots", "Точки"], ["brows", "Вежди"], ["marker", "Флумастер"], ["phosphor", "CRT фосфор"], ["terminal", "Терминал"], ["orb", "Сфера (без лице)"]].map(([k, n]) => `<option value="${k}" ${v.face_style === k ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      ${admin ? `<button class="primary">Запази</button>` : ""}
    </form>
    ${st ? `<h2>Състояние на услугите</h2><table><tbody>${statusRow("stt", "Реч → текст")}${statusRow("diarization", "Говорещи")}${statusRow("llm", "Минути (езиков модел)")}${statusRow("email", "Имейл")}${statusRow("m365", "Microsoft 365")}${statusRow("storage", "Хранилище")}
      <tr><td>Опашка</td><td></td><td></td><td class="small">${st.jobs.queued} чакат · ${st.jobs.running} се изпълняват · ${st.jobs.failed_24h} неуспешни за 24 ч</td></tr></tbody></table>
      <p class="muted small">Доставчиците, ключовете, SSO и хранилището се задават във файла на сървъра: <code>${esc(s.config_path)}</code> или с променливи на средата BIX_*.</p>` : ""}
    <h2>Профил</h2><form id="pw" class="card row"><input type="password" name="current" placeholder="Текуща парола"><input type="password" name="new" placeholder="Нова парола (12+ знака)" minlength="12" required><button>Смени паролата</button></form>`;
  if (admin) $("#sf").addEventListener("submit", guard(async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const settings = {
      language: f.get("language"), speaker_mode: f.get("speaker_mode"), retention_audio_days: +f.get("retention_audio_days"), retention_text_days: +f.get("retention_text_days"),
      live_transcription: !!f.get("live_transcription"), auto_send: !!f.get("auto_send"), members_see_all_meetings: !!f.get("members_see_all_meetings"),
      send_to: f.getAll("send_to"), fixed_recipients: (f.get("fixed_recipients") || "").split(/[,;\s]+/).filter(Boolean),
      auto_stop_silence_min: +f.get("auto_stop_silence_min"), max_meeting_hours: +f.get("max_meeting_hours"), notice_text: f.get("notice_text"),
      face_style: f.get("face_style"),
      glossary: (f.get("glossary") || "").split(/\n/).map((x) => x.trim()).filter(Boolean),
    };
    await api("/api/settings", { method: "PATCH", body: { org_name: f.get("org_name"), settings } });
    toast("Запазено. Устройствата ще получат промените до 15 секунди.");
  }));
  $("#pw").addEventListener("submit", guard(async (e) => { e.preventDefault(); await api("/auth/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); e.target.reset(); toast("Паролата е сменена."); }));
}

// --- audit ----------------------------------------------------------------------------------
async function pageAudit() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const action = params.get("action") || "";
  const off = +(params.get("offset") || 0);
  const r = await api(`/api/audit?limit=100&offset=${off}&action=${encodeURIComponent(action)}`);
  view().innerHTML = `<h1>Одит</h1><form class="row" id="af" style="margin-bottom:12px"><input name="action" placeholder="Действие (напр. meeting., auth., voiceprint.)" value="${esc(action)}"><button>Филтър</button></form>
    <table><thead><tr><th>Кога</th><th>Кой</th><th>Действие</th><th>Обект</th><th>Детайли</th><th>IP</th></tr></thead><tbody>
    ${r.items.map((a) => `<tr><td class="small">${fmtDate(a.at)}</td><td class="small">${esc(a.actor)}</td><td><code>${esc(a.action)}</code></td><td class="small">${esc(a.target)}</td>
      <td class="small muted">${esc(Object.keys(a.details || {}).length ? JSON.stringify(a.details) : "")}</td><td class="small">${esc(a.ip)}</td></tr>`).join("")}</tbody></table>
    <div class="row" style="margin-top:10px">${off > 0 ? `<a class="btn" href="#/audit?action=${encodeURIComponent(action)}&offset=${Math.max(0, off - 100)}">← По-нови</a>` : ""}
      ${off + 100 < r.total ? `<a class="btn" href="#/audit?action=${encodeURIComponent(action)}&offset=${off + 100}">По-стари →</a>` : ""}<span class="muted small">${r.total} записа</span></div>`;
  $("#af").addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/audit?action=${encodeURIComponent(new FormData(e.target).get("action"))}`; });
}

route();
