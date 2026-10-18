import * as THREE from "three";
import { makeAtmosphere } from "./atmosphere.js";
import { latLngToVector3 } from "./geo.js";
import { h } from "../dom.js";
import { bodyPlacement, frontWorlds } from "./worlds-front.js";

/**
 * Earth and Mars in one scene on the opening page. Earth stays at the origin with everything it already has; Mars,
 * painted from MOLA and TES data with the landing-site markers from sites.json, sits in the same frame at its true
 * size ratio (distance not to scale). The camera orbits whichever planet is in focus, with the left or the right
 * mouse button, CAD-style, and eases across when the focus changes: a click on Mars or its tag, the Earth/Mars
 * switch, or the arrow keys. A click on the focused Mars opens the landing map.
 */
const SPIN_RATE = 0.05;
const MOVE_SECONDS = 1.1;
const HINT_KEY = "overfield.worlds-hint-2";

function easeInOut(t) {
  return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
}

export function createWorlds({ scene, camera, controls, domElement, earth, label, reduced, legendHost, copyHost, navHost, hooks }) {
  const worlds = [];                   // { def, group, radius, center, markers, mesh? }
  const pick = [];                     // Mars: the planet mesh first, then the site markers
  const disposables = [];
  let focused = 0;
  let mars = null;
  let marsSites = null;
  let move = null;                     // { fromT, toT, fromC, toC, t0 }
  let elapsedNow = 0;
  let enabled = true;
  let aspect = 1.6;
  let marsLegend = null;
  let loader = null;
  const tmp = new THREE.Vector3();
  const offset = new THREE.Vector3();
  const listeners = new Set();

  // ---------- DOM: the Earth/Mars switch, the hint, the Mars tag, the copy ----------
  const switchBox = h("div", { class: "world-switch", role: "group", "aria-label": "Worlds" });
  const hint = h("p", { class: "world-hint" }, "Drag with the right mouse button to orbit. Click Mars to focus it.");
  const nav = h("div", { class: "world-nav" }, h("div", { class: "world-nav-foot" }, switchBox, hint));
  nav.hidden = true;
  navHost.append(nav);
  const tag = h("button", { class: "world-tag", type: "button", onclick: () => focusId("mars") }, "Mars", h("small", {}, "landing sites"));
  tag.hidden = true;
  (domElement.parentElement || navHost).append(tag);
  const copy = h("div", { class: "world-copy" });
  copy.hidden = true;
  copyHost.append(copy);
  try {
    if (window.localStorage.getItem(HINT_KEY)) hint.hidden = true;
  } catch {
    /* storage off */
  }

  function hideHint() {
    if (hint.hidden) return;
    hint.hidden = true;
    try {
      window.localStorage.setItem(HINT_KEY, "1");
    } catch {
      /* storage off */
    }
  }

  // ---------- Mars ----------
  function addMars(def) {
    const { radius, position } = bodyPlacement("mars", aspect);
    const group = new THREE.Group();
    const center = new THREE.Vector3(...position);
    group.position.copy(center);
    loader = loader || new THREE.TextureLoader();
    const geo = new THREE.SphereGeometry(radius, 96, 96);
    const mat = new THREE.MeshPhongMaterial({ color: 0xb06a44, shininess: 4, specular: new THREE.Color(0x111111) });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData.body = "mars";
    mesh.userData.place = { name: "Mars" };
    group.add(mesh);
    disposables.push(geo, mat);
    const atmosphere = makeAtmosphere(radius, 0xe0a070);
    group.add(atmosphere.group);
    scene.add(group);
    const entry = { def, group, radius, center, markers: [], mesh, atmosphere };
    worlds.push(entry);
    pick.unshift(mesh);
    return entry;
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

  /** How squarely a Mars marker faces the camera (1 = dead centre, negative = far side). */
  function facing(object) {
    if (!mars) return 1;
    object.getWorldPosition(tmp);
    tmp.sub(mars.center).normalize();
    offset.copy(camera.position).sub(mars.center).normalize();
    return tmp.dot(offset);
  }

  // ---------- copy, legend, switch ----------
  function topPickLine() {
    const top = marsSites?.sites?.[0];
    if (!top?.region) return null;
    const n = (marsSites.spacex_candidates || []).filter((c) => !c.missing && /arcadia/i.test(c.region || "")).length;
    const words = ["none", "one", "two", "three", "four", "five", "six"];
    const where = /arcadia/i.test(top.region) && n ? `, where ${words[n] || n} of SpaceX's six 2019 sites sit` : "";
    return h("p", { class: "world-line is-pick" }, `Our top pick, ${top.id}, is in ${top.region}${where}.`);
  }

  function renderCopy(entry) {
    const earthCopy = copyHost.querySelector("#earth-copy");
    if (entry.def.kind === "home") {
      // An open region card keeps the Earth copy out of the slot until its Back.
      const regionOpen = Boolean(copyHost.querySelector(".region-copy:not([hidden])"));
      if (earthCopy) earthCopy.hidden = regionOpen;
      copy.hidden = true;
      return;
    }
    if (earthCopy) earthCopy.hidden = true;
    copy.hidden = false;
    copy.replaceChildren(...[
      h("span", { class: "world-pill is-open" }, "Mars · Open"),
      h("h1", {}, "The next home"),
      h("p", { class: "tagline" }, "A city on Mars starts with one landing: near water, through enough air to slow down, on ground that holds."),
      h("p", { class: "world-line" }, "Every 7 km of Mars between 60 S and 60 N, checked against four rules from public orbital data."),
      topPickLine(),
      h("ul", { class: "world-facts" }, ...(entry.def.facts || []).map((f) => h("li", {}, f))),
      h("button", { class: "world-open", type: "button", onclick: () => hooks.onOpenMars?.() }, "Open Mars map"),
    ].filter(Boolean));
  }

  function renderLegend(entry, orbiters) {
    if (!marsLegend) {
      marsLegend = h("div", { class: "sat-legend mars-legend", id: "mars-legend" },
        h("div", { class: "sat-row is-s2" }, h("i"), h("div", { class: "sat-txt" }, h("strong", {}, "Watching Mars"))),
        ...(orbiters || []).map((o) => h("div", { class: "sat-row is-img" }, h("i"), h("div", { class: "sat-txt" }, h("strong", {}, `${o.name} (${o.years})`), h("span", {}, o.used_for)))),
        h("p", { class: "sat-src" }, "These orbiters made the data behind the landing-site screen."),
      );
      legendHost.append(marsLegend);
    }
    marsLegend.hidden = entry.def.id !== "mars";
  }

  function renderSwitch() {
    switchBox.replaceChildren(...worlds.map((w, i) => h("button", {
      type: "button", "aria-pressed": String(i === focused), onclick: () => focus(i),
    }, w.def.name)));
  }

  // ---------- focus and movement ----------
  function homeDistance(entry) {
    return 3.2 * Math.max(0.5, entry.radius);
  }

  function focus(index, { instant = false, force = false } = {}) {
    if (!worlds.length) return;
    index = ((index % worlds.length) + worlds.length) % worlds.length;
    if (index === focused && !move && !force) return;
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
    renderSwitch();
    for (const cb of listeners) cb(to.def);
  }

  function focusId(id, opts) {
    const i = worlds.findIndex((w) => w.def.id === id);
    if (i >= 0) focus(i, opts);
  }

  function step(delta) {
    if (!enabled || worlds.length < 2) return;
    focus(focused + delta);
  }

  /** The viewport changed shape: Mars keeps its place on screen, further away as the window narrows. */
  function layout(nextAspect) {
    if (!Number.isFinite(nextAspect) || nextAspect <= 0) return;
    aspect = nextAspect;
    if (!mars) return;
    const { position } = bodyPlacement("mars", aspect);
    if (tmp.set(...position).distanceTo(mars.center) < 1e-3) return;
    mars.center.copy(tmp);
    mars.group.position.copy(mars.center);
    if (worlds[focused] === mars) focus(focused, { instant: true, force: true });
  }

  function placeTag() {
    const show = mars && enabled && worlds[focused] !== mars && !move && hooks.introVisible?.() !== false;
    if (!show) {
      tag.hidden = true;
      return;
    }
    tmp.copy(mars.center);
    tmp.y -= mars.radius * 1.25;
    tmp.project(camera);
    const size = hooks.viewSize?.() || { width: domElement.clientWidth, height: domElement.clientHeight };
    if (tmp.z > 1 || Math.abs(tmp.x) > 1 || Math.abs(tmp.y) > 1) {
      tag.hidden = true;
      return;
    }
    tag.hidden = false;
    tag.style.left = `${(tmp.x * 0.5 + 0.5) * size.width}px`;
    tag.style.top = `${(-tmp.y * 0.5 + 0.5) * size.height}px`;
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
    if (mars) {
      if (!reduced) mars.group.rotation.y += delta * SPIN_RATE;
      tmp.copy(hooks.sunWorld?.() || new THREE.Vector3(1, 0, 0));
      mars.atmosphere.update(tmp, camera.position.clone().sub(mars.center));
    }
    placeTag();
  }

  // ---------- input ----------
  const onContext = (event) => event.preventDefault();
  const onKey = (event) => {
    if (!enabled || !hooks.introVisible?.()) return;
    if (event.key === "ArrowRight") step(1);
    else if (event.key === "ArrowLeft") step(-1);
  };
  domElement.addEventListener("contextmenu", onContext);
  document.addEventListener("keydown", onKey);
  controls.addEventListener("start", hideHint);

  return {
    /** worlds.json (and sites.json plus the assets base, when Mars data exists) put Mars next to Earth. */
    setWorlds(doc, sites, assetsBase) {
      const list = frontWorlds(doc?.worlds);
      marsSites = sites || marsSites;
      worlds.length = 0;
      worlds.push({ def: list[0], group: earth, radius: 1, center: new THREE.Vector3(0, 0, 0), markers: [] });
      const marsDef = list.find((w) => w.id === "mars");
      if (marsDef && !mars) {
        mars = addMars(marsDef);
        if (sites) {
          textureMars(mars, sites.assets, assetsBase);
          marsMarkers(mars, sites);
        } else if (assetsBase) {
          textureMars(mars, { globe_color: "mars_color_2k.jpg", globe_bump: "mars_bump_2k.png" }, assetsBase);
        }
      } else if (mars) {
        worlds.push(mars);
      }
      nav.hidden = !enabled || worlds.length < 2;
      renderSwitch();
      renderCopy(worlds[0]);
    },
    focus,
    focusId,
    step,
    update,
    layout,
    facing,
    current: () => worlds[focused]?.def || null,
    isEarth: () => !worlds.length || worlds[focused].def.kind === "home",
    moving: () => Boolean(move),
    setEnabled(on) {
      enabled = on;
      nav.hidden = !on || worlds.length < 2;
    },
    /** The Mars sphere and its site markers, always pickable. */
    marsPickables: () => pick,
    onChange(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    dispose() {
      domElement.removeEventListener("contextmenu", onContext);
      document.removeEventListener("keydown", onKey);
      controls.removeEventListener("start", hideHint);
      if (mars) {
        mars.atmosphere.dispose();
        scene.remove(mars.group);
      }
      for (const d of disposables) d.dispose?.();
      nav.remove();
      tag.remove();
      copy.remove();
      marsLegend?.remove();
    },
  };
}
