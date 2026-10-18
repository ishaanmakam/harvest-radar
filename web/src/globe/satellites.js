import * as THREE from "three";
import { degreesLat, degreesLong, eciToGeodetic, gstime, json2satrec, propagate } from "satellite.js";
import { localFromEcf } from "./geo.js";

/**
 * Real satellites over the globe. Orbital elements are a CelesTrak GP snapshot baked into
 * public/sats/*.json by scripts/fetch_tles.py; positions come from SGP4 (satellite.js) on a
 * simulation clock that starts at wall-clock time and runs at 1x, 60x or 600x.
 *
 * Imaging set: Sentinel-2A/B/C (the satellites that visit every farm), Landsat 8/9, Terra, Aqua,
 * drawn as labelled glowing points with their ground track for the next 90 minutes. Starlink is a
 * faint point shell for connectivity context only; it is not a data source. Starlink is refreshed
 * by SGP4 in chunks of 100 per frame and extrapolated linearly in between, so the frame rate holds.
 */

const EARTH_KM = 6371;
const SPEEDS = [1, 60, 600];
const TRACK_MINUTES = 90;
const TRACK_STEPS = 60;
const TRACK_REFRESH_MS = 90 * 1000;
const TRACK_RADIUS = 1.006; // the ground track sits on the surface, just above the atmosphere rim
const DROP_RADIUS = 1.004;
const STARLINK_CHUNK = 100;
const PASS_RADIUS_KM = 150;
const PASS_LOOKAHEAD_MS = 5 * 86400 * 1000;
const PASS_COARSE_MS = 60 * 1000;
const PASS_FINE_MS = 5 * 1000;
const AMES = { lat: 42.03, lng: -93.63 };
const S2_COLOR = new THREE.Color(0x8fe0a8);
const IMG_COLOR = new THREE.Color(0xcfe6ff);
const SL_COLOR = new THREE.Color(0x9cc0ea);

const pointVertex = `
  uniform float uScale;
  uniform float uSize;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = uSize * uScale / -mv.z;
    gl_Position = projectionMatrix * mv;
  }
`;

const pointFragment = `
  uniform vec3 uColor;
  uniform float uOpacity;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.15, d);
    gl_FragColor = vec4(uColor, a * uOpacity);
  }
`;

function isSentinel(name) {
  return /^SENTINEL-2/.test(name);
}

function prettyName(name) {
  if (isSentinel(name)) return name.replace("SENTINEL-2", "Sentinel-2");
  if (/^LANDSAT/.test(name)) return name.replace("LANDSAT", "Landsat");
  return name.charAt(0) + name.slice(1).toLowerCase();
}

/** A soft radial glow drawn on a canvas, so the sprites need no texture file. */
function glowTexture() {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.55)");
  grad.addColorStop(0.6, "rgba(255,255,255,0.12)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** ECI km (satellite.js) to a point in the Earth group's frame, radius 1 = sea level. */
function eciToLocal(eci, gmst, out) {
  const cos = Math.cos(gmst);
  const sin = Math.sin(gmst);
  const x = eci.x * cos + eci.y * sin;
  const y = -eci.x * sin + eci.y * cos;
  return localFromEcf(x / EARTH_KM, y / EARTH_KM, eci.z / EARTH_KM, out);
}

function haversineKm(lat1, lng1, lat2, lng2) {
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLng = (lng2 - lng1) * toRad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Great-circle distance from the sub-satellite point to a place, or Infinity when SGP4 fails. */
function groundDistanceKm(satrec, date, place) {
  const result = propagate(satrec, date);
  if (!result?.position) return Infinity;
  const geo = eciToGeodetic(result.position, gstime(date));
  return haversineKm(degreesLat(geo.latitude), degreesLong(geo.longitude), place.lat, place.lng);
}

/**
 * The first time within the next five days that any Sentinel-2 sub-satellite point comes within
 * 150 km of a place. Coarse one-minute steps with a wide gate, then five-second steps inside a
 * gated minute. Runs in slices between frames and resolves to a timestamp in ms, or null.
 */
function findNextPass(satrecs, place, fromMs) {
  return new Promise((resolve) => {
    const end = fromMs + PASS_LOOKAHEAD_MS;
    // A satellite in low orbit moves about 460 km of ground track a minute; the gate covers that.
    const gate = PASS_RADIUS_KM + 260;
    let t = fromMs;
    const slice = () => {
      const sliceEnd = Math.min(end, t + 6 * 3600 * 1000);
      for (; t < sliceEnd; t += PASS_COARSE_MS) {
        const date = new Date(t);
        for (const satrec of satrecs) {
          if (groundDistanceKm(satrec, date, place) > gate) continue;
          for (let f = t; f < t + PASS_COARSE_MS; f += PASS_FINE_MS) {
            if (groundDistanceKm(satrec, new Date(f), place) <= PASS_RADIUS_KM) {
              resolve(f);
              return;
            }
          }
        }
      }
      if (t >= end) resolve(null);
      else setTimeout(slice, 0);
    };
    setTimeout(slice, 0);
  });
}

function fmtRelative(ms) {
  const minutes = Math.round(ms / 60000);
  if (minutes < 2) return "a minute or two";
  if (minutes < 120) return `${minutes} min`;
  const hours = ms / 3600000;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}

function fmtClock(date) {
  const day = date.getUTCDate();
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getUTCMonth()];
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${day} ${month} ${hh}:${mm} UTC`;
}

function fmtSnapshot(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown date";
  return fmtClock(date).replace(/ \d\d:\d\d UTC$/, "");
}

async function fetchJson(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

/**
 * @param earth    the rotating Earth group; satellites are its children
 * @param overlay  element over the canvas that holds the labels
 * @param legendHost element that receives the legend panel
 */
export function createSatellites({ earth, overlay, legendHost, reduced }) {
  const glow = glowTexture();
  const imaging = [];
  const starlink = { satrecs: [], pending: [], total: 0, alive: 0, cursor: 0, points: null, p0: null, v0: null, t0: null };
  let simMs = Date.now();
  let speed = reduced ? 1 : 60;
  let gmst = gstime(new Date(simMs));
  let viewportHeight = 1;
  let nextPassMs = null;
  let passSearching = false;
  let passCheckedAt = 0;
  let disposed = false;
  let visible = true;
  let s2Satrecs = [];
  const tmp = new THREE.Vector3();
  const tmpCam = new THREE.Vector3();

  // ----- legend -----
  const legend = el("div", "sat-legend");
  legend.id = "sat-legend";
  const rowS2 = el("div", "sat-row is-s2");
  rowS2.append(el("i"), el("strong", null, "Sentinel-2A, 2B, 2C"), el("span", null, "the satellite that visits every farm: 10 m pixels, every few days"));
  const rowImg = el("div", "sat-row is-img");
  rowImg.append(el("i"), el("strong", null, "Landsat 8, 9, Terra, Aqua"), el("span", null, "Landsat joins Sentinel-2 in the HLS record; Terra and Aqua carry MODIS, the fire detections"));
  const rowSl = el("div", "sat-row is-sl");
  const slTitle = el("strong", null, "Starlink");
  rowSl.append(el("i"), slTitle, el("span", null, "shown for connectivity context only; not a data source"));
  const pass = el("p", "sat-pass", "Next Sentinel-2 pass near Story County: computing an estimate…");
  const foot = el("div", "sat-foot");
  const clock = el("span", "sat-clock");
  const speedBox = el("div", "sat-speed");
  speedBox.setAttribute("role", "group");
  speedBox.setAttribute("aria-label", "Satellite clock speed");
  const speedButtons = SPEEDS.map((value) => {
    const button = el("button", "sat-speed-btn", `${value}×`);
    button.type = "button";
    button.addEventListener("click", () => setSpeed(value));
    speedBox.append(button);
    return button;
  });
  foot.append(clock, speedBox);
  const source = el("p", "sat-src", "Orbits: CelesTrak GP data, SGP4 at the clock above");
  const track = el("p", "sat-src", "Lines: ground track for the next 90 minutes");
  legend.append(rowS2, rowImg, rowSl, pass, foot, source, track);
  legendHost.append(legend);

  function setSpeed(value) {
    speed = value;
    for (const [index, button] of speedButtons.entries()) {
      button.setAttribute("aria-pressed", String(SPEEDS[index] === value));
    }
  }
  setSpeed(speed);

  function renderPass() {
    if (nextPassMs == null) {
      pass.textContent = passSearching
        ? "Next Sentinel-2 pass near Story County: computing an estimate…"
        : "Next Sentinel-2 pass near Story County: no pass found in the next 5 days (estimate)";
      return;
    }
    const wait = nextPassMs - Date.now();
    pass.textContent = wait > -600000
      ? `Next Sentinel-2 pass near Story County in about ${fmtRelative(Math.max(0, wait))} (estimate: sub-satellite point within 150 km of Ames)`
      : "Next Sentinel-2 pass near Story County: recomputing…";
  }

  async function searchPass() {
    if (passSearching || !s2Satrecs.length) return;
    passSearching = true;
    renderPass();
    const found = await findNextPass(s2Satrecs, AMES, Date.now());
    passSearching = false;
    if (disposed) return;
    nextPassMs = found;
    renderPass();
  }

  // ----- imaging satellites -----
  const trackColors = new Float32Array((TRACK_STEPS + 1) * 3);

  function addImaging(record) {
    let satrec;
    try {
      satrec = json2satrec(record);
    } catch {
      return;
    }
    const name = record.OBJECT_NAME;
    const sentinel = isSentinel(name);
    const color = sentinel ? S2_COLOR : IMG_COLOR;
    const group = new THREE.Group();
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: glow, color, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }),
    );
    sprite.scale.setScalar(sentinel ? 0.11 : 0.075);
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(sentinel ? 0.009 : 0.006, 10, 10),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    );
    group.add(sprite, core);

    const positions = new Float32Array((TRACK_STEPS + 1) * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    if (!trackColors[0]) {
      for (let i = 0; i <= TRACK_STEPS; i += 1) {
        const k = 1 - (i / TRACK_STEPS) ** 1.4;
        trackColors.set([k, k, k], i * 3);
      }
    }
    geometry.setAttribute("color", new THREE.BufferAttribute(trackColors, 3));
    const line = new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({
        color,
        vertexColors: true,
        transparent: true,
        opacity: sentinel ? 0.85 : 0.5,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    line.frustumCulled = false;
    line.visible = false;

    // A thin line from the satellite down to its sub-satellite point, where the ground track starts.
    const dropGeometry = new THREE.BufferGeometry();
    dropGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    const drop = new THREE.Line(
      dropGeometry,
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: sentinel ? 0.55 : 0.3, depthWrite: false }),
    );
    drop.frustumCulled = false;

    // Alternate labels above and below the point so two satellites flying close still read.
    const label = el("div", `sat-label${sentinel ? " is-s2" : ""}${imaging.length % 2 ? " is-below" : ""}`);
    label.append(el("strong", null, prettyName(name)));
    const sub = el("small", null, "visits every farm");
    if (sentinel) label.append(sub);
    label.hidden = true;
    overlay.append(label);

    earth.add(group, line, drop);
    const sat = { name, satrec, sentinel, group, line, drop, positions, label, sub, trackAt: -Infinity, ok: true };
    imaging.push(sat);
    if (sentinel) s2Satrecs.push(satrec);
  }

  function refreshTrack(sat) {
    const step = (TRACK_MINUTES * 60 * 1000) / TRACK_STEPS;
    let count = 0;
    for (let i = 0; i <= TRACK_STEPS; i += 1) {
      const t = simMs + i * step;
      const result = propagate(sat.satrec, new Date(t));
      if (!result?.position) break;
      eciToLocal(result.position, gstime(new Date(t)), tmp).normalize().multiplyScalar(TRACK_RADIUS);
      sat.positions.set([tmp.x, tmp.y, tmp.z], i * 3);
      count += 1;
    }
    sat.line.geometry.attributes.position.needsUpdate = true;
    sat.line.geometry.setDrawRange(0, count);
    sat.line.visible = count > 1;
    sat.trackAt = simMs;
  }

  // ----- starlink -----
  function setupStarlink(records) {
    const n = records.length;
    starlink.total = n;
    starlink.pending = records.slice();
    starlink.p0 = new Float64Array(n * 3);
    starlink.v0 = new Float64Array(n * 3);
    starlink.t0 = new Float64Array(n).fill(Number.NaN);
    const positions = new Float32Array(n * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uScale: { value: viewportHeight / 2 },
        uSize: { value: 0.022 },
        uColor: { value: SL_COLOR },
        uOpacity: { value: 0.6 },
      },
      vertexShader: pointVertex,
      fragmentShader: pointFragment,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false;
    earth.add(points);
    starlink.points = points;
  }

  function stepStarlink(date) {
    const { satrecs, pending, p0, v0, t0 } = starlink;
    // Build at most one chunk of SGP4 records a frame, so the load never stalls a frame.
    if (pending.length) {
      const batch = pending.splice(0, STARLINK_CHUNK);
      for (const record of batch) {
        try {
          satrecs.push(json2satrec(record));
        } catch {
          satrecs.push(null);
        }
      }
    }
    const n = satrecs.length;
    if (!n) return;
    // Refresh one chunk with SGP4; everything else runs on its last velocity until its turn.
    const start = starlink.cursor % n;
    const stop = Math.min(n, start + STARLINK_CHUNK);
    for (let i = start; i < stop; i += 1) {
      const satrec = satrecs[i];
      const result = satrec ? propagate(satrec, date) : null;
      if (result?.position && result.velocity) {
        p0.set([result.position.x, result.position.y, result.position.z], i * 3);
        v0.set([result.velocity.x, result.velocity.y, result.velocity.z], i * 3);
        t0[i] = simMs;
      } else {
        t0[i] = Number.NaN;
      }
    }
    starlink.cursor = stop >= n ? 0 : stop;

    const positions = starlink.points.geometry.attributes.position.array;
    const cos = Math.cos(gmst);
    const sin = Math.sin(gmst);
    let alive = 0;
    for (let i = 0; i < n; i += 1) {
      const t = t0[i];
      if (Number.isNaN(t)) {
        positions[i * 3] = 0;
        positions[i * 3 + 1] = 0;
        positions[i * 3 + 2] = 0;
        continue;
      }
      const dt = (simMs - t) / 1000;
      const ex = p0[i * 3] + v0[i * 3] * dt;
      const ey = p0[i * 3 + 1] + v0[i * 3 + 1] * dt;
      const ez = p0[i * 3 + 2] + v0[i * 3 + 2] * dt;
      const x = (ex * cos + ey * sin) / EARTH_KM;
      const y = (-ex * sin + ey * cos) / EARTH_KM;
      localFromEcf(x, y, ez / EARTH_KM, tmp);
      positions[i * 3] = tmp.x;
      positions[i * 3 + 1] = tmp.y;
      positions[i * 3 + 2] = tmp.z;
      alive += 1;
    }
    starlink.alive = alive;
    starlink.points.geometry.attributes.position.needsUpdate = true;
  }

  // ----- load -----
  let loaded = false;
  Promise.all([fetchJson("sats/imaging.json"), fetchJson("sats/starlink.json")]).then(([img, sl]) => {
    if (disposed) return;
    const snapshots = [];
    if (img?.objects?.length) {
      for (const record of img.objects) addImaging(record);
      snapshots.push(img.fetched_at);
    }
    if (sl?.objects?.length) {
      setupStarlink(sl.objects);
      const total = sl.catalogue_count ? ` (${sl.objects.length.toLocaleString()} of ${sl.catalogue_count.toLocaleString()} shown)` : "";
      slTitle.textContent = `Starlink${total}`;
      snapshots.push(sl.fetched_at);
    }
    loaded = imaging.length > 0 || starlink.total > 0;
    const snapshot = snapshots.filter(Boolean).sort().pop();
    source.textContent = loaded
      ? `Orbits: CelesTrak GP data, snapshot ${fmtSnapshot(snapshot)}, propagated with SGP4`
      : "Orbits: the satellite snapshot (sats/*.json) did not load";
    if (!imaging.length) pass.hidden = true;
    searchPass();
  });

  /** Hidden by the globe: the segment camera → point passes through the unit sphere before the point. */
  function occluded(worldPoint, cameraPos) {
    const dx = worldPoint.x - cameraPos.x;
    const dy = worldPoint.y - cameraPos.y;
    const dz = worldPoint.z - cameraPos.z;
    const dd = dx * dx + dy * dy + dz * dz;
    if (dd === 0) return false;
    const t = -(cameraPos.x * dx + cameraPos.y * dy + cameraPos.z * dz) / dd;
    if (t <= 0 || t >= 1) return false;
    const cx = cameraPos.x + t * dx;
    const cy = cameraPos.y + t * dy;
    const cz = cameraPos.z + t * dz;
    return cx * cx + cy * cy + cz * cz < 1;
  }

  function placeLabel(sat, camera, width, height) {
    if (!sat.ok || !sat.group.visible) {
      sat.label.hidden = true;
      return;
    }
    sat.group.getWorldPosition(tmp);
    camera.getWorldPosition(tmpCam);
    if (occluded(tmp, tmpCam)) {
      sat.label.hidden = true;
      return;
    }
    tmp.project(camera);
    if (tmp.z > 1 || Math.abs(tmp.x) > 1.05 || Math.abs(tmp.y) > 1.05) {
      sat.label.hidden = true;
      return;
    }
    sat.label.hidden = false;
    sat.label.style.transform = `translate(${((tmp.x * 0.5 + 0.5) * width).toFixed(1)}px, ${((-tmp.y * 0.5 + 0.5) * height).toFixed(1)}px)`;
  }

  /** Called every frame from the globe loop with the wall-clock delta in seconds. */
  function update(delta, camera, width, height) {
    if (!visible) return;
    simMs += delta * 1000 * speed;
    const date = new Date(simMs);
    gmst = gstime(date);
    clock.textContent = `${fmtClock(date)} · ${speed === 1 ? "real time" : `${speed}× clock`}`;
    if (!loaded) return;

    let nearestS2 = null;
    let frontS2 = null;
    let frontFacing = -Infinity;
    let staleTrack = null;
    for (const sat of imaging) {
      const result = propagate(sat.satrec, date);
      if (!result?.position) {
        sat.ok = false;
        sat.group.visible = false;
        sat.line.visible = false;
        sat.drop.visible = false;
        continue;
      }
      sat.ok = true;
      sat.group.visible = true;
      sat.drop.visible = true;
      const pos = eciToLocal(result.position, gmst, sat.group.position);
      const dropArray = sat.drop.geometry.attributes.position.array;
      dropArray[0] = pos.x;
      dropArray[1] = pos.y;
      dropArray[2] = pos.z;
      tmp.copy(pos).normalize().multiplyScalar(DROP_RADIUS);
      dropArray[3] = tmp.x;
      dropArray[4] = tmp.y;
      dropArray[5] = tmp.z;
      sat.drop.geometry.attributes.position.needsUpdate = true;
      if (!staleTrack && simMs - sat.trackAt > TRACK_REFRESH_MS) staleTrack = sat;
      if (sat.sentinel) {
        if (!nearestS2) nearestS2 = sat;
        sat.group.getWorldPosition(tmp);
        const facing = tmp.normalize().dot(camera.position.clone().normalize());
        if (facing > frontFacing) {
          frontFacing = facing;
          frontS2 = sat;
        }
      }
    }
    // One ground track a frame at most: 61 SGP4 steps, spread across frames.
    if (staleTrack) refreshTrack(staleTrack);
    for (const sat of imaging) {
      if (sat.sentinel) sat.sub.hidden = sat !== frontS2;
      placeLabel(sat, camera, width, height);
    }
    if (starlink.points) stepStarlink(date);

    if (nextPassMs != null && Date.now() - passCheckedAt > 30000) {
      passCheckedAt = Date.now();
      if (Date.now() > nextPassMs + 600000) searchPass();
      else renderPass();
    }
    return nearestS2;
  }

  /** The Sentinel-2 closest (by angle) to a direction in the Earth frame, for the visit arc. */
  function nearestSentinel(dir) {
    let best = null;
    let bestAngle = Infinity;
    for (const sat of imaging) {
      if (!sat.sentinel || !sat.ok) continue;
      const angle = sat.group.position.clone().normalize().angleTo(dir);
      if (angle < bestAngle) {
        bestAngle = angle;
        best = sat;
      }
    }
    return best ? { position: best.group.position, angle: bestAngle } : null;
  }

  /** Off Earth the satellites, their tracks, labels and legend go away and the propagation pauses. */
  function setVisible(on) {
    visible = on;
    for (const sat of imaging) {
      sat.group.visible = on;
      sat.line.visible = on;
      sat.drop.visible = on;
      if (!on) sat.label.hidden = true;
    }
    if (starlink.points) starlink.points.visible = on;
    legend.hidden = !on;
  }

  function setViewport(width, height) {
    viewportHeight = height;
    if (starlink.points) starlink.points.material.uniforms.uScale.value = height / 2;
  }

  function dispose() {
    disposed = true;
    for (const sat of imaging) {
      sat.label.remove();
      sat.line.geometry.dispose();
      sat.line.material.dispose();
      sat.drop.geometry.dispose();
      sat.drop.material.dispose();
      for (const child of sat.group.children) {
        child.geometry?.dispose();
        child.material?.dispose();
      }
    }
    if (starlink.points) {
      starlink.points.geometry.dispose();
      starlink.points.material.dispose();
    }
    glow.dispose();
    legend.remove();
  }

  return { update, nearestSentinel, setViewport, setVisible, setSpeed, now: () => new Date(simMs), dispose, legend };
}
