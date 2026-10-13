"use strict";
/* Virtual Bix: the Bix CRT enclosure (the same meshes as hardware/enclosure) with the real
   room screen inside it, plus the parts the laptop does not have: the LD2410C radar, the
   Mute button and the 12 lights of the XVF3800 microphone array.
   The screen is an iframe of this panel, placed with CSS3D exactly where the 7" display
   sits; the WebGL layer above it is transparent there, so the bezel still covers its edges. */
(function () {
  const $ = (id) => document.getElementById(id);
  const post = (path, body) => fetch(path, { method: "POST", headers: { "X-Bix": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());
  const stage = $("stage");

  // --- scene ----------------------------------------------------------------------------
  const gl = new THREE.WebGLRenderer({ canvas: $("gl"), antialias: true, alpha: true });
  gl.setClearColor(0x000000, 0);
  gl.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const scene = new THREE.Scene();
  const cssScene = new THREE.Scene();
  const css = new THREE.CSS3DRenderer();
  $("css").append(css.domElement);
  const cam = new THREE.PerspectiveCamera(30, 1, 5, 5000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x6a737c, 0.8));
  const key = new THREE.DirectionalLight(0xffffff, 0.7); key.position.set(300, 500, 400); scene.add(key);
  const fill = new THREE.DirectionalLight(0xbcd6ff, 0.3); fill.position.set(-400, 200, -300); scene.add(fill);

  // the real screen: 1280 x 720 px onto the 155.5 x 88 mm visible area of the Touch Display 2
  const SCREEN = { w: 155.5, h: 88, cx: 0, cy: 85, z: 0.25 };
  const frame = document.createElement("iframe");
  frame.src = "/";
  frame.width = 1280; frame.height = 720;
  frame.style.width = "1280px"; frame.style.height = "720px";
  frame.title = "Екранът на Bix";
  const screenObj = new THREE.CSS3DObject(frame);
  screenObj.position.set(SCREEN.cx, SCREEN.cy, SCREEN.z);
  screenObj.scale.set(SCREEN.w / 1280, SCREEN.h / 720, 1);
  cssScene.add(screenObj);
  // a transparent "hole" in the WebGL picture where the screen is
  const hole = new THREE.Mesh(new THREE.PlaneGeometry(SCREEN.w, SCREEN.h), new THREE.MeshBasicMaterial({ color: 0x000000, opacity: 0, blending: THREE.NoBlending }));
  hole.position.set(SCREEN.cx, SCREEN.cy, SCREEN.z);
  scene.add(hole);

  // --- the enclosure ------------------------------------------------------------------------
  const MAT = {
    shell: { color: 0xe9e5dc, roughness: 0.6 }, bezel: { color: 0xe9e5dc, roughness: 0.55 },
    top_cap: { color: 0xf4f6f7, roughness: 0.3, transparent: true, opacity: 0.55 }, base: { color: 0x22262b, roughness: 0.7 },
  };
  const meshes = {};
  let ready = 0;
  const leds = [];
  let muteBtn = null;
  for (const k of ["shell", "bezel", "top_cap", "base"]) {
    fetch(`/sim/asm_bix_crt_${k}.json`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }).then((d) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(Float32Array.from(d.v, (x) => x * d.s), 3));
      g.setIndex(d.f);
      g.rotateX(-Math.PI / 2); // assembly: x, y = depth, z = height
      const geo = g.toNonIndexed();
      geo.computeVertexNormals();
      const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial(Object.assign({ metalness: 0.02, side: THREE.DoubleSide }, MAT[k])));
      if (k === "top_cap") m.renderOrder = 2;
      scene.add(m);
      meshes[k] = m;
      if (++ready === 4) built();
    }).catch(() => { $("msg").textContent = "Моделът не се зареди."; });
  }

  function built() {
    $("msg").hidden = true;
    const box = new THREE.Box3();
    Object.values(meshes).forEach((m) => box.expandByObject(m));
    const cap = new THREE.Box3().setFromObject(meshes.top_cap);
    const c = cap.getCenter(new THREE.Vector3());
    // 12 lights of the XVF3800 under the clear cap
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      const led = new THREE.Mesh(new THREE.CylinderGeometry(2.4, 2.4, 1, 16), new THREE.MeshBasicMaterial({ color: 0x2a2e33 }));
      led.position.set(c.x + Math.sin(a) * 40, cap.max.y - 7, c.z + Math.cos(a) * 40);
      led.userData.a = a;
      scene.add(led);
      leds.push(led);
    }
    muteBtn = new THREE.Mesh(new THREE.CylinderGeometry(6, 6, 3, 24), new THREE.MeshStandardMaterial({ color: 0x30353c, roughness: 0.4 }));
    muteBtn.position.set(c.x + 52, cap.max.y + 1, c.z + 52);
    scene.add(muteBtn);
    // soft shadow on the desk
    const sh = document.createElement("canvas"); sh.width = sh.height = 128;
    const sc = sh.getContext("2d"); const rg = sc.createRadialGradient(64, 64, 4, 64, 64, 64);
    rg.addColorStop(0, "rgba(0,0,0,.55)"); rg.addColorStop(1, "rgba(0,0,0,0)"); sc.fillStyle = rg; sc.fillRect(0, 0, 128, 128);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(380, 300), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sh), transparent: true, depthWrite: false }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(0, box.min.y + 0.2, (box.min.z + box.max.z) / 2);
    scene.add(floor);
    target.center = box.getCenter(new THREE.Vector3());
    setView("angle");
  }

  // --- camera ---------------------------------------------------------------------------------
  const VIEWS = {
    front: { th: 0, ph: 1.52, r: 430, look: () => new THREE.Vector3(0, SCREEN.cy, 0) },
    angle: { th: 0.5, ph: 1.22, r: 640, look: () => target.center || new THREE.Vector3(0, 90, -60) },
    top: { th: 0.25, ph: 0.42, r: 560, look: () => target.center || new THREE.Vector3(0, 90, -60) },
  };
  const target = { center: null };
  const view = { th: 0.5, ph: 1.22, r: 640, look: new THREE.Vector3(0, 90, -60) };
  const goal = { th: 0.5, ph: 1.22, r: 640, look: new THREE.Vector3(0, 90, -60) };
  function setView(name) {
    const v = VIEWS[name];
    Object.assign(goal, { th: v.th, ph: v.ph, r: v.r, look: v.look() });
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === name));
  }
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

  let drag = null;
  stage.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, moved: 0 }; stage.setPointerCapture(e.pointerId); stage.classList.add("drag"); });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY; drag.moved += Math.abs(dx) + Math.abs(dy);
    goal.th = Math.max(-1.1, Math.min(1.1, goal.th - dx * 0.006));
    goal.ph = Math.max(0.3, Math.min(1.56, goal.ph - dy * 0.005));
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.remove("on"));
  });
  stage.addEventListener("pointerup", (e) => {
    stage.classList.remove("drag");
    if (drag && drag.moved < 5 && muteBtn) { // a click: the Mute button?
      const r = stage.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera({ x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -((e.clientY - r.top) / r.height) * 2 + 1 }, cam);
      if (ray.intersectObject(muteBtn).length) pressMute();
    }
    drag = null;
  });
  stage.addEventListener("wheel", (e) => { e.preventDefault(); goal.r = Math.max(260, Math.min(1000, goal.r * (1 + Math.sign(e.deltaY) * 0.08))); }, { passive: false });

  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    gl.setSize(w, h, false);
    css.setSize(w, h);
    cam.aspect = w / h;
    cam.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  // --- device state → lights ---------------------------------------------------------------
  let st = null, doa = null, speaking = false, identifyUntil = 0;
  function ledColors(t) {
    const out = new Array(12).fill(0x2a2e33);
    if (!st) return out;
    if (t < identifyUntil) return out.fill(Math.floor(t * 4) % 2 ? 0xffffff : 0x333333);
    if (st.error) return out.fill(0x8a2a8a);
    if (st.state !== "recording") return st.asking ? out.fill(0x6a4a10) : out;
    if (st.muted) return out.fill(0xd02020);
    out.fill(0x0b3354);
    if (speaking && doa != null) { // points at whoever speaks
      const a = (doa * Math.PI) / 180;
      leds.forEach((l, i) => { const d = Math.abs(Math.atan2(Math.sin(l.userData.a - a), Math.cos(l.userData.a - a))); if (d < 0.3) out[i] = 0x38c8ff; else if (d < 0.7) out[i] = 0x1478b0; });
    }
    return out;
  }

  function frameLoop(now) {
    requestAnimationFrame(frameLoop);
    const k = 0.12;
    view.th += (goal.th - view.th) * k; view.ph += (goal.ph - view.ph) * k; view.r += (goal.r - view.r) * k;
    view.look.lerp(goal.look, k);
    cam.position.set(
      view.look.x + view.r * Math.sin(view.ph) * Math.sin(view.th),
      view.look.y + view.r * Math.cos(view.ph),
      view.look.z + view.r * Math.sin(view.ph) * Math.cos(view.th),
    );
    cam.lookAt(view.look);
    const cols = ledColors(now / 1000);
    leds.forEach((l, i) => l.material.color.setHex(cols[i]));
    if (muteBtn) muteBtn.material.color.setHex(st && st.muted ? 0xb02020 : 0x30353c);
    frame.style.visibility = Math.cos(view.th) * Math.sin(view.ph) > 0.15 ? "visible" : "hidden"; // seen from behind or straight above
    gl.render(scene, cam);
    css.render(cssScene, cam);
  }
  requestAnimationFrame(frameLoop);

  // --- sensors ----------------------------------------------------------------------------------
  function showState() {
    if (!st) return;
    $("pState").innerHTML = "Радар: <b></b>";
    $("pState").firstElementChild.textContent = st.present === true ? "има хора в залата" : st.present === false ? "залата е празна" : "не е симулиран (без радар)";
    $("mState").textContent = st.state === "recording" ? (st.muted ? "Mute е натиснат: записът е на пауза." : "Записва. Mute спира записа веднага.") : "Mute действа само по време на среща.";
  }
  function pressMute() { post("/api/sim/mute"); }
  $("mute").onclick = pressMute;
  $("pIn").onclick = () => post("/api/sim/presence", { present: true });
  $("pOut").onclick = () => post("/api/sim/presence", { present: false });
  $("doa").oninput = () => post("/api/sim/doa", { deg: +$("doa").value });
  post("/api/sim/doa", { deg: 0 });
  const showDist = () => { const v = +$("dist").value; $("distV").textContent = v >= 500 ? "няма никой" : (v / 100).toFixed(1).replace(".", ",") + " м"; };
  $("dist").oninput = () => { showDist(); const v = +$("dist").value; post("/api/sim/distance", { cm: v >= 500 ? null : v }); };
  showDist();
  $("knock1").onclick = () => post("/api/sim/sound", { kind: "knock", count: 1 });
  $("knock2").onclick = () => post("/api/sim/sound", { kind: "knock", count: 2 });
  $("knock5").onclick = () => post("/api/sim/sound", { kind: "knock", count: 5 });
  $("bang").onclick = () => post("/api/sim/sound", { kind: "bang", count: 1 });
  // motion: the screen in the 3D model is this same panel; tell its face directly (a phone has a real sensor)
  const motion = (kind, arg) => { try { const f = frame.contentWindow.bixFace; if (f) f.motion(kind, arg); } catch (e) { /* not loaded yet */ } };
  $("shake").onclick = () => motion("shake");
  $("bump").onclick = () => motion("bump");
  $("tiltL").onclick = () => { motion("tilt", -0.6); setTimeout(() => motion("tilt", 0), 2500); };
  $("tiltR").onclick = () => { motion("tilt", 0.6); setTimeout(() => motion("tilt", 0), 2500); };

  fetch("/api/status").then((r) => r.json()).then((s) => { st = s; showState(); });
  const es = new EventSource("/api/events");
  es.addEventListener("state", (e) => { st = JSON.parse(e.data); showState(); });
  es.addEventListener("presence", (e) => { if (st) { st.present = JSON.parse(e.data).present; showState(); } });
  es.addEventListener("level", (e) => { const d = JSON.parse(e.data); speaking = d.speaking; if (d.doa != null) doa = d.doa; });
  es.addEventListener("identify", () => { identifyUntil = performance.now() / 1000 + 5; });
})();
