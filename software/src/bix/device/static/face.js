"use strict";
/* Bix's face on the room screen. One behaviour engine, several looks ("styles").
   The engine turns the device state into a mood (asleep, listening, paused,
   uploading, done, offline, error) and keeps the character alive with small
   habits: blinks, glances, nods, a yawn when bored, a giggle when poked.
   It feels the room: looks at whoever talks (mic array), startles at a bang,
   answers a knock on the case, greets someone walking up and gets shy when
   they come very close (radar), says goodbye when the room empties. And it
   feels touch: a poked eye squints, a tickle under the eyes, a stroke purrs,
   a swipe offends, a long press asks, a pinch squishes; on a phone or tablet
   shaking makes it dizzy and tilting rolls its eyes downhill.
   While a meeting is recorded it stays calm and only pays attention.
   A style only draws: it gets the current expression and paints it. */
(function () {
  // Expression parameters, all springs:
  // lid = eyes closed 0..1 · low = smiling squint · slant > 0 inner corners up (worried) · asym = left eye raised
  // lift = eyes up (happy) / down (sleepy) · size = eye size · spin = dizzy · bright = how awake
  // smile -1..1, open = mouth open, zip = mouth zipped (paused) · tilt = head tilt (degrees)
  const BASE = { lid: 0, low: 0, slant: 0, asym: 0, lift: 0, size: 1, spin: 0, bright: 1, smile: .3, open: 0, zip: 0, tilt: 0, bob: 1 };
  const MOODS = {
    neutral: {},
    happy: { low: .7, smile: .9, open: .25, lift: .4 },
    giggle: { low: .9, smile: 1, open: .45, lift: .6 },
    proud: { low: .6, smile: .8, open: .1, lift: .4 },
    listening: { size: 1.06, smile: .25, lift: .1 },
    paused: { lid: 1, smile: 0, zip: 1, bright: .6, bob: .5 },
    thinking: { asym: .5, lid: .2, smile: -.05, open: .12, tilt: -6 },
    worried: { slant: .6, lid: .15, smile: -.4, lift: -.1 },
    confused: { asym: .5, slant: .3, smile: -.15, tilt: 9 },
    curious: { asym: .3, size: 1.1, smile: .2, tilt: 10 },
    surprised: { size: 1.2, smile: 0, open: .6, lift: .5 },
    excited: { size: 1.12, low: .3, smile: 1, open: .5, lift: .7 },
    dizzy: { spin: 1, smile: -.2, open: .2 },
    yawn: { lid: .85, smile: 0, open: 1, lift: -.2, tilt: -4 },
    sleepy: { lid: .55, smile: .1, lift: -.3, bright: .75, bob: .7 },
    asleep: { lid: 1, smile: .1, lift: -.4, bright: .55, bob: 1.4 },
    wink: { low: .4, smile: .7 },
    ouch: { slant: .55, smile: -.35, open: .15, tilt: -5 },
    purr: { low: 1, smile: .85, lift: .2, bob: .6 },
    grumpy: { slant: -.55, lid: .3, smile: -.5, asym: .15 },
    squished: { size: .86, low: .45, smile: -.1, open: .3 },
    shy: { low: .55, smile: .55, lift: -.2, size: .9, tilt: -6 },
  };
  // touch zones, in face units from the face centre (the eyes sit at about ±.2, -.05)
  const EYE_X = .2, EYE_Y = -.05, EYE_R = .13;
  // what a gesture is (face units, seconds)
  const TAP_MOVE = .06, TAP_S = .35, HOLD_S = .65, SWIPE_SPEED = 2.2, PET_EVERY = .25;
  const NUM = Object.keys(BASE);
  const SLEEPY_AFTER = 10 * 60, ASLEEP_AFTER = 13 * 60;
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const rnd = (a, b) => a + Math.random() * (b - a);
  const now = () => performance.now() / 1000;
  const rgb = (c, a = 1) => `rgba(${c.map(Math.round).join(",")},${a})`;
  const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);

  function greeting() {
    const h = new Date().getHours();
    return h < 11 ? "Добро утро!" : h < 18 ? "Добър ден!" : "Добър вечер!";
  }

  // ---- drawing helpers shared by styles (origin = face centre, U = face unit) ----
  function eyePos(f, side, spread = .21) {
    const raise = side < 0 ? f.asym : -f.asym * .3;
    return { x: (side * spread + f.gx * .06) * f.U, y: (-.05 - f.lift * .05 - raise * .07 + f.gy * .04) * f.U };
  }

  // top lid (with slant) and the smiling bottom lid, painted in the background colour
  function lids(ctx, f, side, x, y, w, h, slant = f.slant) {
    const blur = ctx.shadowBlur;
    ctx.shadowBlur = 0;
    ctx.fillStyle = f.bg;
    if (f.cut) ctx.globalCompositeOperation = "destination-out"; // on a transparent layer: cut, don't paint
    const shut = 1 - f.op[side];
    if (shut > .01 || Math.abs(slant) > .01) {
      const y0 = y - h / 2 + shut * h, k = slant * .3 * h, inner = x - side * w / 2, outer = x + side * w / 2, pad = side * .05 * f.U;
      ctx.beginPath();
      ctx.moveTo(inner - pad, y0 - k); ctx.lineTo(outer + pad, y0 + k);
      ctx.lineTo(outer + pad, y - h); ctx.lineTo(inner - pad, y - h); ctx.fill();
    }
    if (f.low > .01) {
      const R = w * .8;
      ctx.beginPath(); ctx.arc(x, y + h / 2 + R - f.low * .85 * h, R, 0, Math.PI * 2); ctx.fill();
    }
    ctx.shadowBlur = blur;
    ctx.globalCompositeOperation = "source-over";
  }

  function closedArc(ctx, f, x, y, w, col, lw = .03) {
    ctx.strokeStyle = col; ctx.lineWidth = lw * f.U; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(x - w * .42, y); ctx.quadraticCurveTo(x, y + .08 * f.U, x + w * .42, y); ctx.stroke();
  }

  function spiral(ctx, f, x, y, r, side, col, lw = .022) {
    ctx.strokeStyle = col; ctx.lineWidth = lw * f.U; ctx.lineCap = "round";
    ctx.beginPath();
    for (let a = 0; a < Math.PI * 5; a += .2) {
      const rr = a / (Math.PI * 5) * r, ang = a + side * f.t * 7;
      const px = x + Math.cos(ang) * rr, py = y + Math.sin(ang) * rr;
      a ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    }
    ctx.stroke();
  }

  function mouth(ctx, f, y, half, col, lw = .026, jit = null) {
    const U = f.U, mx = f.gx * .04 * U, my = y * U + f.gy * .02 * U, hw = half * U;
    const J = jit || ((v) => v);
    ctx.strokeStyle = col; ctx.lineWidth = lw * U; ctx.lineCap = "round"; ctx.lineJoin = "round";
    if (f.zip > .5) {
      ctx.beginPath(); ctx.moveTo(J(mx - hw), J(my)); ctx.lineTo(J(mx + hw), J(my)); ctx.stroke();
      ctx.lineWidth = lw * .55 * U;
      for (let i = -1.5; i <= 1.5; i++) { const xx = mx + i * hw / 2.2; ctx.beginPath(); ctx.moveTo(xx, my - .02 * U); ctx.lineTo(xx, my + .02 * U); ctx.stroke(); }
      return;
    }
    const sm = f.smile * .06 * U, op = f.open * .1 * U;
    ctx.beginPath(); ctx.moveTo(J(mx - hw), J(my));
    if (op > .006 * U) {
      ctx.quadraticCurveTo(J(mx), J(my + 2 * (sm * .3 - op * .35)), J(mx + hw), J(my));
      ctx.quadraticCurveTo(J(mx), J(my + 2 * (sm + op * .65)), J(mx - hw), J(my));
      ctx.closePath(); ctx.fillStyle = f.bg; ctx.fill();
    } else {
      ctx.quadraticCurveTo(J(mx), J(my + 2 * sm), J(mx + hw), J(my));
    }
    ctx.stroke();
  }

  // classic rounded-rectangle eyes; used by several styles
  function capsuleEyes(ctx, f, col, { w = .19, h = .3, r = .09, spread = .21 } = {}) {
    for (const side of [-1, 1]) {
      const { x, y } = eyePos(f, side, spread);
      const ew = w * f.U * f.size, eh = h * f.U * f.size * (1 + f.lvl * .04);
      if (f.spin > .5) { spiral(ctx, f, x, y, ew * .5, side, col); continue; }
      if (eh * f.op[side] < .06 * f.U) { closedArc(ctx, f, x, y, ew, col); continue; }
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.roundRect(x - ew / 2, y - eh / 2, ew, eh, Math.min(r * f.U, ew / 2, eh / 2)); ctx.fill();
      lids(ctx, f, side, x, y, ew, eh);
    }
  }

  // ---- the looks ----------------------------------------------------------------
  const KAOMOJI = {
    neutral: ["•", "‿", "•"], happy: ["^", "‿", "^"], giggle: ["≧", "▽", "≦"], proud: ["˘", "▽", "˘"],
    listening: ["•", "_", "•"], paused: ["-", "_", "-"], thinking: ["•", "_", "•", "?"], worried: ["•", "︵", "•", ";"],
    confused: ["⊙", "_", "☉"], curious: ["•", "ᴗ", "•", "?"], surprised: ["⊙", "o", "⊙"], excited: ["^", "o", "^", "!"],
    dizzy: ["@", "_", "@"], yawn: ["-", "o", "-"], sleepy: ["ᵕ", "_", "ᵕ"], asleep: ["-", "_", "-", "zZ"], wink: ["^", "‿", "~"],
    ouch: [">", "﹏", "•", "!"], purr: ["˘", "ω", "˘", "♡"], grumpy: ["¬", "_", "¬"], squished: [">", "o", "<"], shy: ["•", "ω", "•", "⁄⁄"],
  };

  const STYLES = {
    capsule: {
      name: "Капсули", about: "Две светещи капсули. Изчистено, като малък асистент.",
      draw(ctx, f) { capsuleEyes(ctx, f, rgb([104, 210, 255], f.bright)); },
    },
    eve: {
      name: "Овали", about: "Наклонени светещи овали с ореол. Мек и дружелюбен.", glow: .05,
      draw(ctx, f) {
        const col = rgb([125, 222, 255], f.bright);
        for (const side of [-1, 1]) {
          const { x, y } = eyePos(f, side, .2);
          const rx = .13 * f.U * f.size, ry = .085 * f.U * f.size * (1 + f.lvl * .05);
          if (f.spin > .5) { spiral(ctx, f, x, y, rx * .8, side, col); continue; }
          ctx.save(); ctx.translate(x, y); ctx.rotate(side * (f.slant * .45 + .16));
          if (ry * f.op[side] < .03 * f.U) { closedArc(ctx, f, 0, 0, rx * 2, col); ctx.restore(); continue; }
          ctx.fillStyle = col; ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); ctx.fill();
          lids(ctx, f, side, 0, 0, rx * 2.1, ry * 2.1, 0);
          ctx.restore();
        }
      },
    },
    dots: {
      name: "Точки", about: "Две точки и линия. Най-простото лице, характерът е в движението.",
      draw(ctx, f) {
        const col = rgb([236, 240, 244], f.bright), U = f.U;
        const L = eyePos(f, -1, .17), R = eyePos(f, 1, .17), r = .05 * U * f.size;
        ctx.strokeStyle = col; ctx.lineWidth = .016 * U; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(L.x, L.y); ctx.lineTo(R.x, R.y); ctx.stroke();
        for (const [side, p] of [[-1, L], [1, R]]) {
          if (f.spin > .5) { spiral(ctx, f, p.x, p.y, r * 1.3, side, col, .014); continue; }
          const op = f.op[side];
          ctx.fillStyle = col;
          if (op < .15) { ctx.beginPath(); ctx.ellipse(p.x, p.y, r * 1.05, r * .18, 0, 0, Math.PI * 2); ctx.fill(); continue; }
          if (f.low > .45) { // happy: the dot becomes an arch
            ctx.lineWidth = .03 * U; ctx.beginPath(); ctx.arc(p.x, p.y + r * .7, r * 1.25, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke(); continue;
          }
          ctx.beginPath(); ctx.ellipse(p.x, p.y, r, r * op, 0, 0, Math.PI * 2); ctx.fill();
        }
      },
    },
    brows: {
      name: "Вежди", about: "Квадратни очи с вежди. Вежди казват много, без да е детско.",
      draw(ctx, f) {
        const col = rgb([240, 243, 246], f.bright), U = f.U;
        capsuleEyes(ctx, f, col, { w: .15, h: .19, r: .045, spread: .19 });
        ctx.strokeStyle = col; ctx.lineWidth = .03 * U; ctx.lineCap = "round";
        for (const side of [-1, 1]) {
          const { x, y } = eyePos(f, side, .19);
          const by = y - .16 * U * f.size - Math.max(0, f.size - 1) * .3 * U - f.lift * .02 * U + f.lid * .03 * U;
          const inner = x - side * .075 * U, outer = x + side * .075 * U;
          const k = f.slant * .045 * U - f.low * .01 * U;
          ctx.beginPath(); ctx.moveTo(inner, by - k); ctx.lineTo(outer, by + k * .4); ctx.stroke();
        }
      },
    },
    marker: {
      name: "Флумастер", about: "Нарисувано на ръка лице с уста. Линиите леко трептят, като анимация.",
      draw(ctx, f) {
        const col = rgb([246, 244, 238], f.bright), U = f.U;
        const seed = Math.floor(f.t * 8);
        let n = 0;
        const J = (v) => v + (Math.sin((seed * 13.7 + (n++) * 7.3) * 12.9898) * 43758.5 % 1) * .006 * U;
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineCap = "round"; ctx.lineWidth = .026 * U;
        for (const side of [-1, 1]) {
          const { x, y } = eyePos(f, side, .17);
          const r = .04 * U * f.size, op = f.op[side];
          if (f.spin > .5) { spiral(ctx, f, x, y, r * 1.5, side, col, .018); continue; }
          if (op < .2) { ctx.beginPath(); ctx.moveTo(J(x - r * 1.2), J(y)); ctx.quadraticCurveTo(J(x), J(y + r), J(x + r * 1.2), J(y)); ctx.stroke(); }
          else if (f.low > .45) { ctx.beginPath(); ctx.moveTo(J(x - r * 1.2), J(y + r * .4)); ctx.quadraticCurveTo(J(x), J(y - r * 1.4), J(x + r * 1.2), J(y + r * .4)); ctx.stroke(); }
          else { ctx.beginPath(); ctx.ellipse(J(x), J(y), r, r * Math.max(.2, op), 0, 0, Math.PI * 2); ctx.fill(); }
          if (Math.abs(f.slant) > .15 || f.asym > .2 || f.size > 1.1) { // brows only when they say something
            const by = y - .1 * U - (f.size - 1) * .3 * U, k = f.slant * .03 * U;
            ctx.beginPath(); ctx.moveTo(J(x - side * .05 * U), J(by - k)); ctx.lineTo(J(x + side * .05 * U), J(by + k)); ctx.stroke();
          }
        }
        mouth(ctx, f, .17, .08, col, .026, J);
      },
    },
    pixel: {
      name: "LED матрица", about: "Светещи точки като стар табло-дисплей. Пасва на ретро корпуса.",
      draw(ctx, f) {
        const cols = 44, rows = 28, cell = 1.32 * f.U / cols;
        if (!this.off) { this.off = document.createElement("canvas"); this.off.width = cols; this.off.height = rows; this.octx = this.off.getContext("2d", { willReadFrequently: true }); }
        const o = this.octx, U2 = rows / .88;
        o.setTransform(1, 0, 0, 1, 0, 0); o.fillStyle = "#000"; o.fillRect(0, 0, cols, rows);
        o.translate(cols / 2, rows / 2 + f.bounceY * U2 * .1);
        o.rotate(f.tiltA * Math.PI / 180);
        const g = Object.assign({}, f, { U: U2, bg: "#000" });
        capsuleEyes(o, g, "#fff", { w: .2, h: .3, r: .07 });
        mouth(o, g, .26, .09, "#fff", .05);
        const px = o.getImageData(0, 0, cols, rows).data;
        const on = rgb([255, 176, 64], f.bright);
        ctx.setTransform(1, 0, 0, 1, f.W / 2, f.H * .47); // the matrix itself never tilts; the picture on it does
        for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
          const lit = px[(j * cols + i) * 4] > 110;
          const edge = Math.hypot((i - cols / 2) / (cols / 2), (j - rows / 2) / (rows / 2));
          ctx.fillStyle = lit ? on : `rgba(255,176,64,${Math.max(0, .07 - edge * .055)})`;
          ctx.beginPath(); ctx.arc((i - cols / 2 + .5) * cell, (j - rows / 2 + .5) * cell, cell * .36, 0, Math.PI * 2); ctx.fill();
        }
      },
    },
    phosphor: {
      name: "CRT фосфор", about: "Зелен фосфор с ореол и сканлинии, като стар монитор.", glow: .04,
      draw(ctx, f) {
        const col = rgb([96, 255, 150], f.bright * (.92 + Math.random() * .08));
        capsuleEyes(ctx, f, col, { w: .17, h: .26, r: .03 });
        mouth(ctx, f, .24, .08, col, .03);
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalCompositeOperation = "source-atop"; // scanlines only over the glowing parts
        ctx.fillStyle = "rgba(0,0,0,.32)";
        const step = Math.max(2, Math.round(f.H / 180));
        for (let y = (Math.floor(f.t * 30) % step); y < f.H; y += step) ctx.fillRect(0, y, f.W, Math.max(1, step / 2));
        ctx.restore();
      },
    },
    terminal: {
      name: "Терминал", about: "Емотикони от текст на тъмен екран. Скромно и малко нърдско.",
      draw(ctx, f) {
        const k = KAOMOJI[f.mood] || KAOMOJI.neutral;
        const blink = Math.min(f.op[-1], f.op[1]) < .3 && !["happy", "giggle", "proud", "wink", "excited"].includes(f.mood);
        const eyeL = blink ? "-" : k[0], eyeR = blink ? "-" : (f.op[1] < .3 ? "-" : k[2]);
        const text = `(${eyeL}${k[1]}${eyeR})${k[3] ? " " + k[3] : ""}`;
        const col = rgb([150, 235, 255], f.bright);
        ctx.fillStyle = col; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.font = `600 ${.2 * f.U * Math.min(1.1, f.size)}px "DejaVu Sans Mono", Consolas, "Segoe UI Symbol", monospace`;
        ctx.fillText(text, f.gx * .07 * f.U, (-.02 - f.lift * .04 + f.gy * .03) * f.U);
      },
    },
    orb: {
      name: "Сфера", about: "Без лице: светлина, която диша, слуша и сменя цвета си. Най-сериозният вариант.",
      draw(ctx, f) {
        const U = f.U;
        let c = [104, 210, 255];
        c = mix(c, [255, 200, 110], Math.max(0, Math.min(1, (f.smile - .35) * 1.6)));
        c = mix(c, [255, 140, 70], Math.max(0, Math.min(1, f.slant * 1.4)));
        c = mix(c, [130, 140, 152], f.zip);
        const r = .2 * U * f.size * (1 + f.lvl * .12 + Math.sin(f.t * 2) * .015) * (1 - f.lid * .25);
        const x = f.gx * .07 * U, y = (f.gy * .05 - f.lift * .03) * U;
        const g = ctx.createRadialGradient(x, y, r * .1, x, y, r * 1.6);
        g.addColorStop(0, rgb(c, .9 * f.bright)); g.addColorStop(.55, rgb(c, .35 * f.bright)); g.addColorStop(1, rgb(c, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 1.6, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = rgb(c, f.bright); ctx.lineWidth = .014 * U;
        if (f.mood === "thinking" || f.spin > .5) {
          const a = f.spinA * 3; ctx.beginPath(); ctx.arc(x, y, r * 1.25, a, a + 1.6); ctx.stroke();
        } else if (f.zip > .5) {
          for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2 + .3; ctx.beginPath(); ctx.arc(x, y, r * 1.2, a, a + 1); ctx.stroke(); }
        } else { ctx.beginPath(); ctx.arc(x, y, r * 1.2, 0, Math.PI * 2); ctx.stroke(); }
      },
    },
  };

  class BixFace {
    constructor(canvas, { onSay, style = "capsule", bg = "#0e1116" } = {}) {
      this.c = canvas;
      this.ctx = canvas.getContext("2d");
      this.onSay = onSay || (() => {});
      this.bg = bg;
      this.setStyle(style);
      this.st = null;
      this.p = Object.assign({}, BASE);
      this.acts = []; // queued short expressions: {mood, ms, say, bounce, tilt, wink, sparkle, wobble}
      this.act = null;
      this.gaze = { x: 0, y: 0, tx: 0, ty: 0, until: 0 };
      this.bounce = { y: 0, v: 0 };
      this.tilt = { a: 0, v: 0, target: 0 };
      this.blink = { next: now() + rnd(2.5, 5), t0: -1, eyes: "LR", again: false };
      this.parts = [];
      this.level = { db: -90, speaking: false, doa: null, at: 0 };
      this.lastActivity = now();
      this.asleep = false;
      this.nextIdle = now() + rnd(5, 10);
      this.pokes = [];
      this.spinA = 0;
      this.t = now();
      this.reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.fingers = new Map(); // pointers on the face: {x0, y0, x, y, t0, path, pets, held, moved}
      this.finger = null; // where a single finger rests (face units): the eyes follow it
      this.pinch = null;
      this.squish = 0; // springs to the pinch
      this.squint = { side: 0, until: 0 }; // one eye shut after it was poked
      this.near = 0; // somebody close to the screen (radar), 0..1
      this.dist = null;
      this.tiltIn = 0; // the device itself is tilted (motion sensor), -1..1
      this.lastPresent = null;
      this.said = {}; // when a reaction last spoke: they don't repeat every time
      this.pets = [];
      this.voice = { side: 0, since: 0 };
      canvas.addEventListener("pointerdown", (e) => this._down(e));
      canvas.addEventListener("pointermove", (e) => this._move(e));
      for (const t of ["pointerup", "pointercancel", "pointerleave"]) canvas.addEventListener(t, (e) => this._up(e, t === "pointerup"));
      const fit = () => this._fit();
      window.addEventListener("resize", fit);
      fit();
      requestAnimationFrame(() => this._frame());
    }

    setStyle(name) {
      this.styleName = STYLES[name] ? name : "capsule";
      this.style = STYLES[this.styleName];
    }

    // ---- input from the device ------------------------------------------------
    set(st) {
      const prev = this.st;
      this.st = st;
      if (!prev) {
        this.lastPresent = st.present;
        this.dist = st.distance_cm == null ? null : st.distance_cm;
        if (!st.enrolled) this._say("Здравейте! Аз съм Bix. Регистрирайте ме с код от портала.", 7000);
        return;
      }
      const wasRec = prev.state === "recording", rec = st.state === "recording";
      if (!wasRec && rec) { this._wake(false); this.play([{ mood: "happy", ms: 700, bounce: -1, say: "Слушам." }]); }
      if (wasRec && rec && prev.muted !== st.muted) {
        this.play(st.muted ? [{ mood: "paused", ms: 300, say: "Не слушам. Натиснете „Продължи“, когато сте готови." }]
                           : [{ mood: "surprised", ms: 300 }, { mood: "happy", ms: 700, bounce: -.6, say: "Пак слушам." }]);
      }
      if (prev.online && !st.online && st.enrolled) this._say(rec ? "Няма връзка. Записвам тук и ще кача после." : "Няма връзка със сървъра.", 6000);
      if (!prev.online && st.online && prev.enrolled) this.play([{ mood: "happy", ms: 1200, bounce: -.5 }]);
      if (!prev.enrolled && st.enrolled) this.play([{ mood: "excited", ms: 1800, bounce: -1.4, sparkle: 10, say: "Регистриран съм! Готов за срещи." }]);
      if (st.error && st.error !== prev.error) this.play([{ mood: "confused", ms: 1500 }]);
      this.presence(st.present);
      if (st.distance_cm !== undefined && st.distance_cm !== this.dist) this.setDistance(st.distance_cm);
      if (st.state !== prev.state || st.muted !== prev.muted) { this.lastActivity = now(); this.tilt.target = 0; }
    }

    // the radar: people came in, or the last one left (the panel may hand over the same object, so remember it here)
    presence(present) {
      const was = this.lastPresent;
      this.lastPresent = present;
      if (was === present || was === null || !this.st) return;
      if (present) this._wake(true);
      else if (this.st.state !== "recording" && !this.asleep) {
        this.play([{ mood: "happy", ms: 1500, wink: true, tilt: 6, say: pick(["Довиждане!", "Чао, до скоро!", "Хубав ден!"]) }, { mood: "sleepy", ms: 1500 }]);
      }
    }

    // the radar's distance to the nearest person: someone walks up, or comes very close
    setDistance(cm) {
      const prev = this.dist;
      this.dist = cm;
      if (cm == null || !this.st || this.st.state === "recording") return;
      if (this.asleep) { this._wake(true); return; }
      if (cm < 45 && (prev == null || prev >= 45) && this._once("shy", 30)) {
        this.play([{ mood: "shy", ms: 1800, bounce: .6, say: pick(["Ой, много сме близо…", "Хи, виждам ви отблизо!"]) }]);
      } else if (cm < 130 && (prev == null || prev >= 160) && this._once("hello", 90)) {
        this.play([{ mood: "happy", ms: 1400, bounce: -.6, say: pick(["Здрасти!", "О, здравейте!", greeting()]) }]);
      }
      this.lastActivity = now();
    }

    // a knock on the case or a bang in the room (only how loud and from where, never what)
    sound({ kind, count = 1, doa = null }) {
      if (!this.st) return;
      const dir = doa == null ? pick([-.7, .7]) : Math.sin(doa * Math.PI / 180) * .85;
      if (this.st.state === "recording") { this._look(dir, 0, .9); return; } // a glance, the meeting goes on
      this.lastActivity = now();
      if (this.asleep) {
        this.asleep = false;
        this._look(dir, 0, 2);
        this.play(kind === "bang"
          ? [{ mood: "surprised", ms: 700, bounce: -1.6, say: "Какво беше това?!" }, { mood: "worried", ms: 900 }]
          : [{ mood: "surprised", ms: 500, bounce: -1 }, { mood: "curious", ms: 1500, tilt: 9, say: "Кой е?" }]);
        return;
      }
      this._look(dir, -.05, 2.2);
      if (kind === "bang") this.play([{ mood: "surprised", ms: 650, bounce: -1.8, say: this._once("bang", 20) ? pick(["Ох, стреснах се!", "Уф!", "Леле!"]) : "" }, { mood: "worried", ms: 900 }]);
      else if (count >= 4) this.play([{ mood: "grumpy", ms: 1600, say: pick(["Стига, буден съм!", "Чух, чух!"]) }]);
      else if (count >= 2) this.play([{ mood: "excited", ms: 600, bounce: -1.2 }, { mood: "curious", ms: 1500, tilt: 10, say: pick(["Кой е там?", "Влез!", "Тук съм!"]) }]);
      else this.play([{ mood: "curious", ms: 1300, tilt: pick([-8, 8]), say: this._once("knock", 15) ? "Ехо?" : "" }]);
    }

    // the device moved (motion sensor of a phone/tablet; buttons in the simulator)
    motion(kind, arg = 0) {
      if (kind === "tilt") { this.tiltIn = Math.max(-1, Math.min(1, arg)); return; }
      if (!this.st) return;
      this.lastActivity = now();
      if (this.st.state === "recording") {
        if (kind === "shake") this.play([{ mood: "worried", ms: 1200, say: this._once("shakeRec", 30) ? "Внимателно, записвам!" : "" }]);
        return;
      }
      this.asleep = false;
      if (kind === "shake") this.play([{ mood: "dizzy", ms: 2000, wobble: true, say: pick(["Ууу, спри, ще ми прилошее!", "Земетресение!", "Ааа, върти се!"]) }, { mood: "confused", ms: 900 }]);
      else if (kind === "bump") this.play([{ mood: "surprised", ms: 700, bounce: -1.6, say: this._once("bump", 10) ? "Опа!" : "" }]);
    }

    // true at most once per `secs` for this reaction: lines that would get annoying
    _once(key, secs) {
      const t = now();
      if (t - (this.said[key] || -1e9) < secs) return false;
      this.said[key] = t;
      return true;
    }

    setLevel({ db, speaking, doa }) {
      this.level = { db, speaking, doa: doa == null ? null : doa, at: now() };
      // somebody is talking in the room: wake up (but never start recording by itself)
      if (speaking && this.st && this.st.state !== "recording") {
        if (this.asleep && db > -45 && this.st.present !== false) this._wake(true); // the radar says empty: a radio, not people
        else if (!this.asleep) this.lastActivity = now();
      }
    }

    event(name, data = {}) {
      if (name === "live") { this._nod(.45); this.focusT = now(); }
      else if (name === "uploaded") this._sparkle(6);
      else if (name === "identify") {
        this._wake(false);
        this.play([{ mood: "excited", ms: 700, bounce: -1.6 }, { mood: "excited", ms: 700, bounce: -1.6, sparkle: 8 }, { mood: "happy", ms: 1600, say: "Тук съм!" }]);
      } else if (name === "ended") {
        const why = {
          silence: "Спрях: беше тихо дълго време.",
          room_empty: "Спрях: стаята се опразни.",
          max_duration: "Спрях: срещата стана твърде дълга.",
          discarded: "Изтрих записа. Нищо не остана.",
        }[data.reason];
        if (data.reason === "discarded") this.play([{ mood: "neutral", ms: 1500, say: why }]);
        else this.play([{ mood: "proud", ms: 4000, sparkle: 14, bounce: -.8, say: (why ? why + " " : "Готово! ") + "Минутите ще дойдат по имейл." }]);
      }
    }

    // ---- touch: where and how the face is touched ------------------------------------
    _at(e) { // pointer → face units from the face centre
      const r = this.c.getBoundingClientRect();
      const U = Math.min(r.width / 1.5, r.height) || 1;
      return { x: (e.clientX - r.left - r.width / 2) / U, y: (e.clientY - r.top - r.height * .47) / U };
    }

    _calm() { return this.st && this.st.state === "recording"; } // a meeting: no games, a wink at most

    _down(e) {
      e.preventDefault();
      try { this.c.setPointerCapture(e.pointerId); } catch (_) { /* synthetic events */ }
      const p = this._at(e), t = now();
      this.fingers.set(e.pointerId, { x0: p.x, y0: p.y, x: p.x, y: p.y, t0: t, path: 0, petPath: 0, pets: 0, held: false, moved: false });
      this.lastActivity = t;
      if (this.asleep) { this._wake(true); this.fingers.get(e.pointerId).held = true; return; }
      if (this.fingers.size === 2) {
        const [a, b] = [...this.fingers.values()];
        this.pinch = { d0: Math.hypot(a.x - b.x, a.y - b.y) || .01, done: false };
      }
      if (this.fingers.size === 1 && !this._calm()) {
        this.finger = p;
        const id = e.pointerId;
        setTimeout(() => { // held still: "what is it?"
          const f = this.fingers.get(id);
          if (f && !f.moved && !f.held && this.fingers.size === 1 && !this._calm()) {
            f.held = true;
            this.play([{ mood: "curious", ms: 1800, tilt: 10, say: pick(["Да? Какво има?", "Слушам ви.", "Хм?"]) }]);
          }
        }, HOLD_S * 1000);
      }
    }

    _move(e) {
      const f = this.fingers.get(e.pointerId);
      if (!f) return;
      const p = this._at(e), step = Math.hypot(p.x - f.x, p.y - f.y);
      f.path += step; f.petPath += step; f.x = p.x; f.y = p.y;
      if (Math.hypot(p.x - f.x0, p.y - f.y0) > TAP_MOVE) f.moved = true;
      if (this._calm()) return;
      if (this.fingers.size === 2 && this.pinch) {
        const [a, b] = [...this.fingers.values()];
        const k = Math.hypot(a.x - b.x, a.y - b.y) / this.pinch.d0;
        this.squish = Math.max(0, Math.min(1, (1 - k) * 1.6));
        if (k < .7 && !this.pinch.done) { this.pinch.done = true; this.play([{ mood: "squished", ms: 60000, say: pick(["Мачкаш ме!", "Ой, тясно ми е!"]) }]); }
        return;
      }
      this.finger = p;
      // a slow stroke is petting: it purrs while the hand moves
      const speed = f.path / Math.max(.05, now() - f.t0);
      if (f.petPath > PET_EVERY && speed < SWIPE_SPEED) {
        f.petPath = 0; f.pets++;
        this._pet(Math.sign(p.x - f.x0) || 1);
      }
    }

    _up(e, released) {
      const f = this.fingers.get(e.pointerId);
      if (!f) return;
      this.fingers.delete(e.pointerId);
      if (this.fingers.size < 2 && this.pinch) {
        const done = this.pinch.done;
        this.pinch = null; this.squish = 0;
        if (done) this.play([{ mood: "surprised", ms: 300, bounce: -1.2 }, { mood: "happy", ms: 900, say: "Уф, пак съм си аз." }]);
        return;
      }
      if (!this.fingers.size) this.finger = null;
      if (!released || f.held) return;
      const dur = now() - f.t0, dx = f.x - f.x0, dy = f.y - f.y0, dist = Math.hypot(dx, dy);
      if (dist < TAP_MOVE && dur < TAP_S) return this._tap(f.x0, f.y0);
      if (f.pets) return; // it was petting: already answered while the hand moved
      if (dist / dur >= SWIPE_SPEED) return this._swipe(Math.sign(dx) || 1);
    }

    _tap(x, y) {
      if (this.asleep) return this._wake(true);
      if (this._calm()) return this.play([{ mood: "wink", ms: 600, wink: true }]); // don't disturb the meeting
      for (const side of [-1, 1]) {
        if (Math.hypot(x - side * EYE_X, y - EYE_Y) < EYE_R) { // poked in the eye
          this.squint = { side, until: now() + 1.1 };
          this.lastActivity = now();
          return this.play([{ mood: "ouch", ms: 1100, bounce: .5, tilt: side * -6, say: pick(["Ау, окото ми!", "Ой!", "Внимавай!"]) }]);
        }
      }
      if (y > .1 && Math.abs(x) < .3) { // under the eyes: a tickle
        this.lastActivity = now();
        return this.play([{ mood: "giggle", ms: 1100, wobble: true, bounce: -.6, say: pick(["Хи-хи, гъделичка!", "Ихи-хи!", "Не там, гъдел ме е!"]) }]);
      }
      this.poke();
    }

    _pet(dir) {
      const t = now();
      this.lastActivity = t;
      this.pets = this.pets.filter((x) => t - x < 6).concat(t);
      if (this.pets.length >= 4) this._hearts(1);
      // leans into the hand; keeps purring while stroked
      this.acts = [];
      this.act = { mood: "purr", tilt: dir * 7, until: t + 1.2 };
      if (this._once("purr", 12)) this._say(pick(["Мрр…", "Ммм, приятно.", "Още!"]));
    }

    _swipe(dir) {
      if (this.asleep) return this._wake(true);
      if (this._calm()) return;
      this.lastActivity = now();
      this.tilt.v += dir * 160; // knocked aside
      this._look(-dir * .9, .1, 2.2); // and looks away from the hand
      this.play([{ mood: "surprised", ms: 250 }, { mood: "grumpy", ms: 2000, say: pick(["Ей! Защо така?", "Хм!", "Това не беше мило."]) }, { mood: "neutral", ms: 400 }]);
    }

    poke() {
      const t = now();
      this.lastActivity = t;
      if (this.asleep) { this._wake(true); return; }
      if (this.st && this.st.state === "recording") { this.play([{ mood: "wink", ms: 600, wink: true }]); return; } // don't disturb the meeting
      this.pokes = this.pokes.filter((x) => t - x < 3).concat(t);
      if (this.pokes.length >= 4) {
        this.pokes = [];
        this.play([{ mood: "dizzy", ms: 1800, wobble: true, say: "Ей, завъртя ми се главата!" }, { mood: "happy", ms: 800 }]);
      } else {
        this.play([{ mood: "giggle", ms: 900, bounce: -.7, tilt: pick([-8, 8]), say: pick(["Хи-хи!", "Гъделичка!", "Здрасти!", "Ихи!"]) }]);
      }
    }

    play(seq) {
      this.acts = seq.slice();
      this.act = null;
    }

    // ---- behaviour ----------------------------------------------------------------
    _say(text, ms) { this.onSay(text, ms || Math.max(2200, text.length * 70)); }

    _wake(greet) {
      this.lastActivity = now();
      if (!this.asleep) return;
      this.asleep = false;
      const seq = [{ mood: "surprised", ms: 450, bounce: -1 }];
      if (greet) seq.push({ mood: "happy", ms: 1800, say: greeting() });
      this.play(seq);
    }

    _nod(k) { this.bounce.v += 1.6 * k; }

    _hearts(n) {
      for (let i = 0; i < n; i++) this.parts.push({ k: "heart", x: rnd(-.25, .25), y: -.2, vx: rnd(-.08, .08), vy: rnd(-.25, -.15), life: 0, max: rnd(1.2, 1.8), r: rnd(.035, .05) });
    }

    _sparkle(n) {
      for (let i = 0; i < n; i++) {
        const a = rnd(0, Math.PI * 2), s = rnd(.25, .6);
        this.parts.push({ k: "spark", x: Math.cos(a) * .3, y: -.05 + Math.sin(a) * .2, vx: Math.cos(a) * s, vy: Math.sin(a) * s - .2, life: 0, max: rnd(.9, 1.5), r: rnd(.03, .055) });
      }
    }

    _baseMood() {
      const s = this.st;
      if (!s) return "neutral";
      if (s.state === "recording") return s.error ? "worried" : s.muted ? "paused" : "listening";
      if (this.asleep) return "asleep";
      if (s.error) return "confused";
      if (!s.enrolled) return "curious";
      if (!s.online) return "worried";
      if (s.backlog_s > 0) return "thinking";
      if (now() - this.lastActivity > SLEEPY_AFTER) return "sleepy";
      return "neutral";
    }

    _idleHabits(t, mood) {
      if (t < this.nextIdle || this.act) return;
      if (mood === "listening") {
        // follow the conversation: glance around unless the mic array tells where the voice is
        this.nextIdle = t + rnd(2, 5);
        if (this.level.doa == null) this._look(rnd(-.6, .6), rnd(-.15, .2), rnd(1, 2.5));
        if (Math.random() < .25) this.tilt.target = pick([-5, 0, 5]);
        return;
      }
      if (mood !== "neutral" && mood !== "sleepy" && mood !== "curious") { this.nextIdle = t + 3; return; }
      this.nextIdle = t + rnd(5, 12);
      const bored = t - this.lastActivity > 180;
      const habit = pick(["glance", "glance", "around", "hum", "tilt", ...(bored ? ["yawn", "yawn"] : [])]);
      if (habit === "glance") this._look(rnd(-.9, .9), rnd(-.5, .4), rnd(.8, 2));
      else if (habit === "around") { this._look(-.85, 0, .9); setTimeout(() => this._look(.85, -.1, .9), 900); }
      else if (habit === "hum") this.play([{ mood: "happy", ms: 1400, tilt: pick([-6, 6]) }]);
      else if (habit === "tilt") this.play([{ mood: "curious", ms: 1600, tilt: pick([-10, 10]) }]);
      else if (habit === "yawn") this.play([{ mood: "yawn", ms: 1700 }, { mood: "sleepy", ms: 500 }]);
    }

    _look(x, y, secs) {
      this.gaze.tx = x; this.gaze.ty = y; this.gaze.until = now() + secs;
    }

    // ---- animation ------------------------------------------------------------------
    _frame() {
      const t = now(), dt = Math.min(.05, t - this.t);
      this.t = t;
      const s = this.st;
      const idle = !s || s.state !== "recording";
      // falls asleep when the room empties (after saying goodbye) or nothing happened for long
      if (idle && !this.asleep && s && !this.act && !this.acts.length && !this.fingers.size
          && (this.lastPresent === false || t - this.lastActivity > ASLEEP_AFTER)) this.asleep = true;

      // current short expression
      if (!this.act || t > this.act.until) {
        const a = this.acts.shift();
        this.act = a ? Object.assign({}, a, { until: t + a.ms / 1000 }) : null;
        if (a) {
          if (a.say) this._say(a.say);
          if (a.bounce) this.bounce.v += a.bounce * 2.2;
          if (a.sparkle) this._sparkle(a.sparkle);
          if (a.wink) this._startBlink(t, "R");
        }
      }
      const mood = this._baseMood();
      this.mood = this.act ? this.act.mood : mood;
      const m = Object.assign({}, BASE, MOODS[this.mood]);
      this._idleHabits(t, mood);

      // springs towards the target expression
      const k = 1 - Math.exp(-dt * 9);
      for (const n of NUM) this.p[n] += (m[n] - this.p[n]) * k;

      // somebody close (radar): a little bigger, looking up at them
      const nearT = this.dist == null || this.asleep ? 0 : Math.max(0, Math.min(1, (180 - this.dist) / 130));
      this.near += (nearT - this.near) * (1 - Math.exp(-dt * 3));

      // gaze: a finger on the face, else the voice (mic array), else habits; quick saccades
      const voice = this.level.doa != null && this.level.speaking && t - this.level.at < .6 && !this.asleep;
      const vside = voice ? Math.sign(this.level.doa) : 0;
      if (vside !== this.voice.side) this.voice = { side: vside, since: t };
      let gx = 0, gy = 0;
      if (mood === "thinking" && !this.act) { gx = .55; gy = -.7; }
      else if (this.finger) { gx = Math.max(-1, Math.min(1, this.finger.x * 2.2)); gy = Math.max(-1, Math.min(1, (this.finger.y - EYE_Y) * 2.2)); }
      else if (voice && !(this.act && t < this.gaze.until)) { gx = Math.sin(this.level.doa * Math.PI / 180) * .85; gy = .05; }
      else if (t < this.gaze.until) { gx = this.gaze.tx; gy = this.gaze.ty; }
      else if (this.near > .2) gy = -.35 * this.near;
      gx += this.tiltIn * .8; // tilted device: the eyes slide downhill
      gx = Math.max(-1.2, Math.min(1.2, gx));
      if (this.asleep || (this.act && this.act.mood === "giggle")) { gx = 0; gy = .1; }
      if (this.act && this.act.mood === "dizzy") { gx = Math.sin(t * 5) * .5; gy = Math.cos(t * 5) * .4; }
      const kg = 1 - Math.exp(-dt * 16);
      this.gaze.x += (gx - this.gaze.x) * kg;
      this.gaze.y += (gy - this.gaze.y) * kg;

      // tilt and bounce: damped springs, so nods and hops overshoot a little
      let tiltT = (this.act && this.act.tilt != null) ? this.act.tilt : (m.tilt || this.tilt.target || 0);
      if (!tiltT && this.voice.side && t - this.voice.since > 2.5) tiltT = this.voice.side * 4; // one side talks a while: turns to them
      tiltT += this.tiltIn * 12;
      if (this.act && this.act.wobble) tiltT = Math.sin(t * 9) * 12;
      this.tilt.v += ((tiltT - this.tilt.a) * 60 - this.tilt.v * 9) * dt;
      this.tilt.a += this.tilt.v * dt;
      this.bounce.v += (-this.bounce.y * 120 - this.bounce.v * 11) * dt;
      this.bounce.y += this.bounce.v * dt;
      this.spinA += dt * (.4 + Math.max(0, 1 - (t - (this.focusT || -9)) / .7) * 9 + this.p.spin * 14);

      // blinks (sometimes double), not while eyes are closed anyway
      if (t > this.blink.next && this.p.lid < .8) this._startBlink(t, "LR");

      // particles: z's while asleep, sparks when something went well
      if (this.asleep && (!this._zAt || t - this._zAt > 1.6)) {
        this._zAt = t;
        this.parts.push({ k: "z", x: .3, y: -.25, vx: .07, vy: -.12, life: 0, max: 3, r: rnd(.05, .07) });
      }
      for (const p of this.parts) { p.life += dt; p.x += p.vx * dt; p.y += p.vy * dt; if (p.k === "spark") p.vy += .5 * dt; }
      this.parts = this.parts.filter((p) => p.life < p.max);

      this._draw(t, mood);
      requestAnimationFrame(() => this._frame());
    }

    _startBlink(t, eyes) {
      this.blink.t0 = t;
      this.blink.eyes = eyes;
      if (eyes === "LR") {
        const again = !this.blink.again && Math.random() < .18;
        this.blink.again = again;
        this.blink.next = t + (again ? .28 : rnd(2.2, 6));
      }
    }

    _blinkF(t, side) {
      const d = this.blink.eyes === "R" ? .45 : .14;
      const ph = (t - this.blink.t0) / d;
      if (ph < 0 || ph > 1) return 1;
      if (this.blink.eyes === "R" && side < 0) return 1;
      return 1 - Math.sin(Math.PI * ph) * .94;
    }

    _squintF(t, side) { // the poked eye stays shut a moment, then opens slowly
      if (side !== this.squint.side || t > this.squint.until) return 1;
      const left = this.squint.until - t;
      return left > .35 ? .08 : .08 + (1 - left / .35) * .92;
    }

    _fit() {
      const r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      this.c.width = Math.max(1, Math.round(r.width * dpr));
      this.c.height = Math.max(1, Math.round(r.height * dpr));
    }

    _draw(t, mood) {
      const { ctx, p } = this, W = this.c.width, H = this.c.height;
      if (!W || !H) return;
      const U = Math.min(W / 1.5, H);
      const breathe = this.reduced ? 0 : Math.sin(t * (this.asleep ? 1.4 : 2.1)) * .012 * p.bob;
      const squash = Math.max(-.08, Math.min(.08, -this.bounce.v * .025));
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.translate(W / 2, H * .47 + (this.bounce.y * .1 + breathe) * U);
      ctx.rotate(this.tilt.a * Math.PI / 180);
      this.sq = (this.sq || 0) + (this.squish - (this.sq || 0)) * .25;
      ctx.scale((1 - squash * .6) * (1 + this.sq * .22), (1 + squash) * (1 - this.sq * .3));
      const lvl = this.st && this.st.state === "recording" && this.level.speaking && !this.st.muted
        ? Math.max(0, Math.min(1, (this.level.db + 60) / 40)) : 0;
      const f = Object.assign({}, p, {
        U, W, H, t, bg: this.bg, mood: this.mood, size: p.size * (1 + this.near * .1), gx: this.gaze.x, gy: this.gaze.y, lvl, spinA: this.spinA,
        tiltA: this.tilt.a, bounceY: this.bounce.y,
        op: { "-1": (1 - p.lid) * this._blinkF(t, -1) * this._squintF(t, -1), "1": (1 - p.lid) * this._blinkF(t, 1) * this._squintF(t, 1) },
      });
      if (this.style.glow) {
        // glowing looks: draw once to a layer, then a blurred copy under a sharp one
        if (!this.layer) this.layer = document.createElement("canvas");
        const L = this.layer, lc = L.getContext("2d");
        if (L.width !== W || L.height !== H) { L.width = W; L.height = H; }
        lc.setTransform(1, 0, 0, 1, 0, 0); lc.clearRect(0, 0, W, H);
        lc.setTransform(ctx.getTransform());
        this.style.draw(lc, Object.assign(f, { cut: true }));
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.filter = `blur(${Math.round(this.style.glow * U)}px)`; ctx.drawImage(L, 0, 0);
        ctx.filter = "none"; ctx.drawImage(L, 0, 0);
        ctx.restore();
      } else {
        ctx.save();
        this.style.draw(ctx, f);
        ctx.restore();
      }

      // small extras, the same for every style
      const col = "rgba(170,215,240,.9)";
      ctx.fillStyle = col;
      if (mood === "worried" && !this.act) {
        const d = (t * .35) % 1, x = .42 * U, y = (-.26 + d * .12) * U, r = .025 * U;
        ctx.fillStyle = `rgba(140,200,255,${1 - d})`;
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI); ctx.lineTo(x, y - r * 2.2); ctx.closePath(); ctx.fill();
      }
      if (mood === "confused" && !this.act && this.styleName !== "terminal") {
        ctx.font = `700 ${.12 * U}px system-ui, sans-serif`; ctx.textAlign = "center";
        ctx.fillText("?", .42 * U, (-.3 + Math.sin(t * 3) * .015) * U);
      }
      if (mood === "thinking" && !this.act && this.styleName !== "orb") {
        for (let i = 0; i < 3; i++) {
          ctx.globalAlpha = .25 + .75 * Math.max(0, Math.sin(t * 4 - i * .9));
          ctx.beginPath(); ctx.arc((.4 + i * .06) * U, (-.3 - i * .05) * U, (.014 + i * .005) * U, 0, Math.PI * 2); ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
      for (const q of this.parts) {
        const a = Math.max(0, 1 - q.life / q.max);
        if (q.k === "z") {
          if (this.styleName === "terminal") continue;
          ctx.globalAlpha = a * .8; ctx.fillStyle = col; ctx.font = `700 ${q.r * U * (1 + q.life * .3)}px system-ui, sans-serif`; ctx.textAlign = "center";
          ctx.fillText("z", q.x * U, q.y * U);
        } else if (q.k === "heart") {
          ctx.globalAlpha = a; ctx.fillStyle = "rgb(255,120,150)";
          const r = q.r * U, x = q.x * U, y = q.y * U;
          ctx.beginPath(); ctx.moveTo(x, y + r * .9);
          ctx.bezierCurveTo(x - r * 1.6, y - r * .2, x - r * .6, y - r * 1.3, x, y - r * .4);
          ctx.bezierCurveTo(x + r * .6, y - r * 1.3, x + r * 1.6, y - r * .2, x, y + r * .9); ctx.fill();
        } else {
          ctx.globalAlpha = a; ctx.fillStyle = "rgb(255,200,96)";
          const r = q.r * U, x = q.x * U, y = q.y * U;
          ctx.beginPath(); ctx.moveTo(x, y - r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.quadraticCurveTo(x, y, x, y + r);
          ctx.quadraticCurveTo(x, y, x - r, y); ctx.quadraticCurveTo(x, y, x, y - r); ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
    }
  }

  BixFace.STYLES = Object.fromEntries(Object.entries(STYLES).map(([k, v]) => [k, { name: v.name, about: v.about }]));
  BixFace.MOODS = Object.keys(MOODS);
  window.BixFace = BixFace;
})();
