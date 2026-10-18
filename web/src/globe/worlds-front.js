/**
 * What the opening page shows: Earth and Mars together in one scene. Pure functions, no three.js, so they are
 * unit-tested with `npm test`. worlds.json may list moons and exoplanets too; they stay in the data for the Data
 * page but are not drawn on the front page.
 */
export const FRONT_IDS = ["earth", "mars"];

const EARTH_DEF = { id: "earth", name: "Earth", kind: "home", status: "live", facts: [], tagline: "" };

// The home camera of globe.js: where it starts, looking at Earth at the origin, 42 degrees of vertical field of
// view on a wide screen; on a portrait one globe.js widens the vertical field so the horizontal one stays 42.
const HOME = [0.22, 0.62, 3.12];
const HALF_TAN = Math.tan((42 / 2) * (Math.PI / 180));
const MARS_RADIUS = 0.53; // true ratio to Earth
const MARS_ANCHOR = { x: 0.72, y: 0.62 }; // where Mars's centre lands on screen, in clip space (-1..1), from the home camera
const MARS_DEPTH = { near: 5.6, far: 7.5, aspectAtNear: 1.35 }; // further away (smaller) as the window narrows

/** The worlds drawn on the opening scene, Earth first, Mars second when the file has it. */
export function frontWorlds(list) {
  const byId = new Map((Array.isArray(list) ? list : []).map((w) => [w.id, w]));
  const out = [];
  for (const id of FRONT_IDS) {
    const def = byId.get(id);
    if (def) out.push(def);
    else if (id === "earth") out.push({ ...EARTH_DEF });
  }
  return out;
}

function normalize([x, y, z]) {
  const l = Math.hypot(x, y, z) || 1;
  return [x / l, y / l, z / l];
}

function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

/** The home camera's basis: forward, right, up, and the half-tangents of its view at this aspect ratio. */
function homeView(aspect) {
  const forward = normalize(HOME.map((c) => -c));
  const right = normalize(cross(forward, [0, 1, 0]));
  const up = cross(right, forward);
  const wide = !(Number.isFinite(aspect) && aspect < 1);
  const a = Number.isFinite(aspect) && aspect > 0 ? aspect : 1.6;
  return { forward, right, up, halfTanX: wide ? HALF_TAN * a : HALF_TAN, halfTanY: wide ? HALF_TAN : HALF_TAN / a };
}

/**
 * A world point seen from the home camera: its centre in clip space (x, y in -1..1), the clip-space radii of a
 * sphere of the given radius there (rx sideways, ry vertically) and its depth along the view axis.
 */
export function projectHome(position, radius, aspect) {
  const { forward, right, up, halfTanX, halfTanY } = homeView(aspect);
  const v = [position[0] - HOME[0], position[1] - HOME[1], position[2] - HOME[2]];
  const depth = dot(v, forward);
  const sx = depth * halfTanX;
  const sy = depth * halfTanY;
  return { x: dot(v, right) / sx, y: dot(v, up) / sy, rx: radius / sx, ry: radius / sy, depth };
}

/**
 * Where a body sits and how big it is, in Earth radii. Earth is the origin, the pivot the camera orbits by default.
 * Mars keeps the true size ratio and sits in the same frame, up and to the right of Earth and behind it: its screen
 * position is fixed from the home camera, and it moves further away (so it looks smaller) as the window narrows,
 * which keeps it whole in the frame and clear of Earth on every screen shape.
 */
export function bodyPlacement(id, aspect) {
  if (id === "earth") return { radius: 1, position: [0, 0, 0] };
  if (id === "mars") {
    const a = Number.isFinite(aspect) && aspect > 0 ? aspect : 1.6;
    const depth = Math.min(MARS_DEPTH.far, Math.max(MARS_DEPTH.near, MARS_DEPTH.near * (MARS_DEPTH.aspectAtNear / a) ** 1.5));
    const { forward, right, up, halfTanX, halfTanY } = homeView(a);
    const sx = MARS_ANCHOR.x * depth * halfTanX;
    const sy = MARS_ANCHOR.y * depth * halfTanY;
    const position = [0, 1, 2].map((i) => HOME[i] + forward[i] * depth + right[i] * sx + up[i] * sy);
    return { radius: MARS_RADIUS, position: position.map((c) => Math.round(c * 1000) / 1000) };
  }
  throw new Error(`unknown body: ${id}`);
}
