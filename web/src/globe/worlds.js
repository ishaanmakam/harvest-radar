import * as THREE from "three";
import { makeAtmosphere } from "./atmosphere.js";
import { latLngToVector3 } from "./geo.js";
import { h } from "../dom.js";

/**
 * Worlds beyond Earth on the opening globe. Earth stays at the origin with everything it already has; the other
 * worlds from worlds.json sit along a line in space, and the camera eases from one to the next. Left drag keeps
 * rotating the focused world; right-click, right-drag, the arrow keys, the chevrons and a two-finger swipe move
 * between worlds. Mars is a textured sphere painted from MOLA and TES data with the landing-site markers from
 * sites.json; moons and exoplanets are procedural spheres, labelled as illustrations because no image of them exists.
 */
const SPACING = 7;
const SPIN_RATE = 0.05;
const MOVE_SECONDS = 1.1;
const DRAG_STEP_PX = 40;
const HINT_KEY = "overfield.worlds-hint";
const ORDER_KIND = { home: 0, planet: 1, ocean_moon: 2, exoplanet: 3 };

function easeInOut(t) {
  return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
}

function worldRadius(world) {
  if (world.kind === "home") return 1;
  if (world.id === "mars") return 0.53;
  if (world.kind === "ocean_moon") return { europa: 0.25, enceladus: 0.16, titan: 0.4 }[world.id] || 0.3;
  const r = world.radius_earth || 1;
  return Math.min(1.5, Math.max(0.55, r * 0.8));
}

function worldColor(world) {
  if (world.kind === "ocean_moon") return { europa: 0xd8d0c2, enceladus: 0xf1f3f5, titan: 0xc48b3c }[world.id] || 0xcfd3d8;
  const t = world.eq_temp_k;
  if (t == null) return 0x9fb3c8;
  if (t < 220) return 0xcfe0ef;
  if (t < 320) return 0x6f9bb0;
  return 0xc69a62;
}

function fmt(value, digits = 1) {
  if (value == null) return null;
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function createWorlds({ scene, camera, controls, domElement, earth, label, reduced, legendHost, copyHost, navHost, hooks }) {
  const worlds = [];                   // { def, group, radius, center, markers: [] }
  const pick = [];                     // pickable marker meshes on Mars
  const disposables = [];
  let focused = 0;
  let move = null;                     // { from, to, t0 }
  let elapsedNow = 0;
  let enabled = true;
  let marsLegend = null;
  let loader = null;
  const tmp = new THREE.Vector3();
  const offset = new THREE.Vector3();
  const listeners = new Set();

  // ---------- DOM: chevrons, dots, hint, copy ----------
  const prev = h("button", { class: "world-chevron is-prev", type: "button", "aria-label": "Previous world", onclick: () => step(-1) }, "‹");
  const next = h("button", { class: "world-chevron is-next", type: "button", "aria-label": "Next world", onclick: () => step(1) }, "›");
  const dots = h("div", { class: "world-dots", role: "tablist", "aria-label": "Worlds" });
  const hint = h("p", { class: "world-hint" }, "Right-click to explore other worlds");
  const nav = h("div", { class: "world-nav" }, prev, next, h("div", { class: "world-nav-foot" }, dots, hint));
  nav.hidden = true;
  navHost.append(nav);
  const copy = h("div", { class: "world-copy" });
  copy.hidden = true;
  copyHost.append(copy);
  try {
    if (window.localStorage.getItem(HINT_KEY)) hint.hidden = true;
  } catch {
    /* storage off */
  }

  function hideHint() {
    hint.hidden = true;
    try {
      window.localStorage.setItem(HINT_KEY, "1");
    } catch {
      /* storage off */
    }
  }

  // ---------- building the worlds ----------
  function addWorld(def, index) {
    const radius = worldRadius(def);
    const group = new THREE.Group();
    const center = new THREE.Vector3(index * SPACING, 0, 0);
    group.position.copy(center);
    const entry = { def, group, radius, center, markers: [], spin: true };
    if (def.id === "mars") buildMars(entry);
    else buildPlain(entry);
    scene.add(group);
    worlds.push(entry);
    return entry;
  }

  function buildPlain(entry) {
    const { def, radius, group } = entry;
    const geo = new THREE.SphereGeometry(radius, 64, 64);
    const mat = new THREE.MeshPhongMaterial({ color: worldColor(def), shininess: def.kind === "ocean_moon" ? 40 : 8, specular: new THREE.Color(0x222222) });
    const mesh = new THREE.Mesh(geo, mat);
    group.add(mesh);
    disposables.push(geo, mat);
    if (def.id === "titan" || def.kind === "exoplanet") {
      const atm = makeAtmosphere(radius, def.id === "titan" ? 0xe0a050 : 0x9fbad8);
      group.add(atm.group);
      entry.atmosphere = atm;
    }
  }

  function buildMars(entry) {
    const { radius, group } = entry;
    loader = loader || new THREE.TextureLoader();
    const geo = new THREE.SphereGeometry(radius, 96, 96);
    const mat = new THREE.MeshPhongMaterial({ color: 0xb06a44, shininess: 4, specular: new THREE.Color(0x111111) });
    const mesh = new THREE.Mesh(geo, mat);
    group.add(mesh);
    disposables.push(geo, mat);
    entry.mesh = mesh;
    const atm = makeAtmosphere(radius, 0xe0a070);
    group.add(atm.group);
    entry.atmosphere = atm;
  }

  function textureMars(entry, assets, base) {
    if (!entry || !assets || entry.textured) return;
    entry.textured = true;
    const color = loader.load(`${base}${assets.globe_color}`, () => {
      entry.mesh.material.color.set(0xffffff);
      entry.mesh.material.needsUpdate = true;
    });
    color.colorSpace = THREE.SRGBColorSpace;
    const bump = loader.load(`${base}${assets.globe_bump}`);
    entry.mesh.material.map = color;
    entry.mesh.material.bumpMap = bump;
    entry.mesh.material.bumpScale = 0.012;
    disposables.push(color, bump);
  }

  function marsMarkers(entry, sites) {
    if (!entry || !sites) return;
    const r = entry.radius;
    const statusColor = { go: 0x43b36b, watch: 0xf2a93b, no_go: 0xd7263d };
    const place = (lat, lon, mesh, name) => {
      const local = latLngToVector3(lat, lon, r * 1.004);
      const g = new THREE.Group();
      g.position.copy(local);
      g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), local.clone().normalize());
      g.add(mesh);
      const picker = new THREE.Mesh(new THREE.SphereGeometry(r * 0.05, 8, 8), new THREE.MeshBasicMaterial({ visible: false }));
      picker.userData.place = { name };
      g.add(picker);
      pick.push(picker);
      entry.group.add(g);
      entry.markers.push(g);
      disposables.push(mesh.geometry, mesh.material, picker.geometry, picker.material);
    };
    for (const c of sites.spacex_candidates || []) {
      if (c.missing || c.lat == null) continue;
      const ring = new THREE.Mesh(new THREE.RingGeometry(r * 0.014, r * 0.022, 32),
        new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0.95, depthWrite: false }));
      place(c.lat, c.lon, ring, `${c.id} · SpaceX 2019 · ${c.status}`);
    }
    for (const s of sites.sites || []) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(r * 0.012, 12, 12), new THREE.MeshBasicMaterial({ color: statusColor[s.status] || 0xffffff }));
      place(s.lat, s.lon, dot, `${s.id} · ${s.status} · ${s.reason}`);
    }
  }

  // ---------- copy and legend ----------
  function renderCopy(entry) {
    const def = entry.def;
    const earthCopy = copyHost.querySelector("#earth-copy");
    if (def.kind === "home") {
      if (earthCopy) earthCopy.hidden = false;
      copy.hidden = true;
      return;
    }
    if (earthCopy) earthCopy.hidden = true;
    copy.hidden = false;
    copy.replaceChildren();
    if (def.id === "mars") {
      copy.append(...[
        h("span", { class: "world-pill is-open" }, "Mars · Open"),
        h("h1", {}, "The next home"),
        h("p", { class: "tagline" }, "A city on Mars starts with one landing: near water, through enough air to slow down, on ground that holds."),
        h("p", { class: "world-line" }, "Every 7 km of Mars between 60 S and 60 N, checked against four rules from public orbital data."),
        h("ul", { class: "world-facts" }, ...(def.facts || []).map((f) => h("li", {}, f))),
        h("button", { class: "world-open", type: "button", onclick: () => hooks.onOpenMars?.() }, "Open Mars map"),
      ].filter(Boolean));
      return;
    }
    const numbers = [];
    if (def.distance_ly != null) numbers.push(`${fmt(def.distance_ly, 1)} light-years`);
    if (def.radius_earth != null) numbers.push(`${fmt(def.radius_earth, 2)} Earth radii`);
    if (def.eq_temp_k != null) numbers.push(`about ${fmt(def.eq_temp_k, 0)} K`);
    if (def.orbital_period_days != null) numbers.push(`a ${fmt(def.orbital_period_days, 1)}-day year`);
    copy.append(...[
      h("span", { class: "world-pill is-soon" }, "Coming soon"),
      h("h1", {}, def.name),
      h("p", { class: "tagline" }, def.tagline || ""),
      numbers.length ? h("p", { class: "world-numbers" }, numbers.join(" · ")) : null,
      def.kind === "exoplanet" && def.host_star ? h("p", { class: "world-line" }, `Orbits ${def.host_star}${def.spectral_type ? ` (${def.spectral_type})` : ""}${def.discovered ? `, found ${def.discovered}` : ""}${def.method ? ` by ${String(def.method).toLowerCase()}` : ""}.`) : null,
      h("p", { class: "world-source" }, def.illustration ? "Illustration: no image of this world exists. " : "", def.source ? `Source: ${def.source}.` : ""),
    ].filter(Boolean));
  }

  function renderLegend(entry, orbiters) {
    if (!marsLegend) {
      marsLegend = h("div", { class: "sat-legend mars-legend", id: "mars-legend" },
        h("div", { class: "sat-row is-s2" }, h("i"), h("strong", {}, "Watching Mars")),
        ...(orbiters || []).map((o) => h("div", { class: "sat-row is-img" }, h("i"), h("strong", {}, `${o.name} (${o.years})`), h("span", {}, o.used_for))),
        h("p", { class: "sat-src" }, "These orbiters made the data behind the landing-site screen."),
      );
      legendHost.append(marsLegend);
    }
    marsLegend.hidden = entry.def.id !== "mars";
  }

  function renderDots() {
    dots.replaceChildren(...worlds.map((w, i) => h("button", {
      class: `world-dot${i === focused ? " is-active" : ""}`, type: "button", role: "tab", "aria-selected": String(i === focused),
      title: w.def.name, onclick: () => focus(i),
    }, h("span", { class: "world-dot-name" }, w.def.name))));
  }

  // ---------- focus and movement ----------
  function homeDistance(entry) {
    return 3.2 * Math.max(0.5, entry.radius);
  }

  function focus(index, { instant = false } = {}) {
    if (!worlds.length) return;
    index = ((index % worlds.length) + worlds.length) % worlds.length;
    if (index === focused && !move) return;
    const from = worlds[focused];
    const to = worlds[index];
    focused = index;
    hideHint();
    offset.copy(camera.position).sub(controls.target);
    const dir = offset.lengthSq() > 1e-6 ? offset.clone().normalize() : new THREE.Vector3(0.07, 0.2, 0.98).normalize();
    const camTo = to.center.clone().addScaledVector(dir, homeDistance(to));
    controls.minDistance = 1.6 * to.radius;
    controls.maxDistance = 6 * to.radius;
    if (instant || reduced) {
      controls.target.copy(to.center);
      camera.position.copy(camTo);
      camera.lookAt(to.center);
      controls.update();
      move = null;
      controls.enabled = true;
    } else {
      move = { fromT: controls.target.clone(), toT: to.center.clone(), fromC: camera.position.clone(), toC: camTo, t0: elapsedNow };
      controls.enabled = false;
    }
    hooks.onFocus?.(to.def, from?.def);
    renderCopy(to);
    renderLegend(to, hooks.orbiters?.());
    renderDots();
    for (const cb of listeners) cb(to.def);
  }

  function step(delta) {
    if (!enabled || worlds.length < 2) return;
    focus(focused + delta);
  }

  function update(delta, elapsed) {
    elapsedNow = elapsed;
    if (move) {
      const t = Math.min(1, (elapsed - move.t0) / MOVE_SECONDS);
      const k = easeInOut(t);
      controls.target.lerpVectors(move.fromT, move.toT, k);
      camera.position.lerpVectors(move.fromC, move.toC, k);
      camera.lookAt(controls.target);
      if (t >= 1) {
        move = null;
        controls.enabled = true;
        controls.update();
      }
    }
    const current = worlds[focused];
    for (const w of worlds) {
      if (w.def.kind === "home") continue;
      if (w === current && !reduced) w.group.rotation.y += delta * SPIN_RATE;
      if (w.atmosphere) {
        tmp.copy(hooks.sunWorld?.() || new THREE.Vector3(1, 0, 0));
        w.atmosphere.update(tmp, camera.position.clone().sub(w.center));
      }
    }
  }

  // ---------- input ----------
  const onContext = (event) => event.preventDefault();
  let rightDrag = null;
  const onDown = (event) => {
    if (event.button === 2) rightDrag = { x: event.clientX, stepped: false };
  };
  const onMove = (event) => {
    if (!rightDrag || !(event.buttons & 2)) return;
    const dx = event.clientX - rightDrag.x;
    if (!rightDrag.stepped && Math.abs(dx) > DRAG_STEP_PX) {
      rightDrag.stepped = true;
      step(dx < 0 ? 1 : -1);
    }
  };
  const onUp = (event) => {
    if (event.button !== 2 || !rightDrag) return;
    const dx = Math.abs(event.clientX - rightDrag.x);
    if (!rightDrag.stepped && dx < 6) step(1);
    rightDrag = null;
  };
  let swipe = null;
  const onTouchStart = (event) => {
    if (event.touches.length === 2) swipe = { x: (event.touches[0].clientX + event.touches[1].clientX) / 2, stepped: false };
  };
  const onTouchMove = (event) => {
    if (!swipe || event.touches.length !== 2) return;
    const x = (event.touches[0].clientX + event.touches[1].clientX) / 2;
    const dx = x - swipe.x;
    if (!swipe.stepped && Math.abs(dx) > 60) {
      swipe.stepped = true;
      step(dx < 0 ? 1 : -1);
    }
  };
  const onTouchEnd = () => {
    swipe = null;
  };
  const onKey = (event) => {
    if (!enabled || !hooks.introVisible?.()) return;
    if (event.key === "ArrowRight") step(1);
    else if (event.key === "ArrowLeft") step(-1);
  };
  domElement.addEventListener("contextmenu", onContext);
  domElement.addEventListener("pointerdown", onDown);
  domElement.addEventListener("pointermove", onMove);
  domElement.addEventListener("pointerup", onUp);
  domElement.addEventListener("touchstart", onTouchStart, { passive: true });
  domElement.addEventListener("touchmove", onTouchMove, { passive: true });
  domElement.addEventListener("touchend", onTouchEnd, { passive: true });
  document.addEventListener("keydown", onKey);
  if (controls.mouseButtons) controls.mouseButtons.RIGHT = null;

  return {
    /** worlds.json (and sites.json plus the assets base, when Mars data exists) build the line of worlds. */
    setWorlds(doc, sites, assetsBase) {
      const list = [...(doc?.worlds || [])].sort((a, b) => (ORDER_KIND[a.kind] - ORDER_KIND[b.kind]) || (a.order - b.order) || ((a.distance_ly ?? 1e9) - (b.distance_ly ?? 1e9)));
      const earthDef = list.find((w) => w.kind === "home") || { id: "earth", name: "Earth", kind: "home", status: "live", facts: [], tagline: "" };
      worlds.length = 0;
      worlds.push({ def: earthDef, group: earth, radius: 1, center: new THREE.Vector3(0, 0, 0), markers: [] });
      let i = 1;
      for (const def of list) {
        if (def.kind === "home") continue;
        addWorld(def, i);
        i += 1;
      }
      const mars = worlds.find((w) => w.def.id === "mars");
      if (mars && sites) {
        textureMars(mars, sites.assets, assetsBase);
        marsMarkers(mars, sites);
      } else if (mars && assetsBase) {
        textureMars(mars, { globe_color: "mars_color_2k.jpg", globe_bump: "mars_bump_2k.png" }, assetsBase);
      }
      nav.hidden = worlds.length < 2;
      renderDots();
      renderCopy(worlds[0]);
    },
    focus,
    focusId(id, opts) {
      const i = worlds.findIndex((w) => w.def.id === id);
      if (i >= 0) focus(i, opts);
    },
    step,
    update,
    current: () => worlds[focused]?.def || null,
    isEarth: () => !worlds.length || worlds[focused].def.kind === "home",
    moving: () => Boolean(move),
    setEnabled(on) {
      enabled = on;
    },
    pickMeshes: () => pick,
    onChange(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    dispose() {
      domElement.removeEventListener("contextmenu", onContext);
      domElement.removeEventListener("pointerdown", onDown);
      domElement.removeEventListener("pointermove", onMove);
      domElement.removeEventListener("pointerup", onUp);
      domElement.removeEventListener("touchstart", onTouchStart);
      domElement.removeEventListener("touchmove", onTouchMove);
      domElement.removeEventListener("touchend", onTouchEnd);
      document.removeEventListener("keydown", onKey);
      for (const w of worlds) {
        if (w.def.kind === "home") continue;
        w.atmosphere?.dispose();
        scene.remove(w.group);
      }
      for (const d of disposables) d.dispose?.();
      nav.remove();
      copy.remove();
      marsLegend?.remove();
    },
  };
}
