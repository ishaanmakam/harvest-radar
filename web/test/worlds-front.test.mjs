import { test } from "node:test";
import assert from "node:assert/strict";
import { frontWorlds, bodyPlacement, projectHome, FRONT_IDS } from "../src/globe/worlds-front.js";

const doc = [
  { id: "kepler-452b", kind: "exoplanet", name: "Kepler-452 b", order: 9 },
  { id: "europa", kind: "ocean_moon", name: "Europa", order: 2 },
  { id: "mars", kind: "planet", name: "Mars", order: 1 },
  { id: "earth", kind: "home", name: "Earth", order: 0 },
  { id: "titan", kind: "ocean_moon", name: "Titan", order: 3 },
];

const ASPECTS = [0.55, 0.7, 0.9, 1, 1.2, 1.37, 1.6, 1.78, 2.2];

test("the front page shows Earth and Mars only, Earth first", () => {
  const list = frontWorlds(doc);
  assert.deepEqual(list.map((w) => w.id), ["earth", "mars"]);
  assert.deepEqual(FRONT_IDS, ["earth", "mars"]);
});

test("a missing Earth entry is synthesised so the list always starts at home", () => {
  const list = frontWorlds(doc.filter((w) => w.id !== "earth"));
  assert.equal(list[0].id, "earth");
  assert.equal(list[0].kind, "home");
  assert.equal(list[1].id, "mars");
});

test("without Mars in the file the front page is Earth alone", () => {
  const list = frontWorlds(doc.filter((w) => w.id !== "mars"));
  assert.deepEqual(list.map((w) => w.id), ["earth"]);
  assert.deepEqual(frontWorlds(undefined).map((w) => w.id), ["earth"]);
});

test("Earth sits at the origin at unit radius", () => {
  const p = bodyPlacement("earth", 1.6);
  assert.equal(p.radius, 1);
  assert.deepEqual(p.position, [0, 0, 0]);
});

test("Mars keeps the true size ratio and sits up and to the right of Earth, behind it", () => {
  const p = bodyPlacement("mars", 1.6);
  assert.ok(Math.abs(p.radius - 0.53) < 0.01);
  const [x, y, z] = p.position;
  assert.ok(x > 0.8, "to the right of Earth");
  assert.ok(y > 0.5, "above the equator");
  assert.ok(z < -0.5, "behind Earth");
});

test("from the home camera Mars is whole in the frame and clear of Earth at every window shape", () => {
  const EARTH_HALO = 0.87; // Earth's silhouette plus its atmosphere rim, in Earth radii
  for (const aspect of ASPECTS) {
    const { position, radius } = bodyPlacement("mars", aspect);
    const mars = projectHome(position, radius, aspect);
    const earth = projectHome([0, 0, 0], EARTH_HALO, aspect);
    const wide = aspect >= 1.3;
    const margin = wide ? 0.95 : 0.98;
    assert.ok(mars.depth > 0, `aspect ${aspect}: in front of the camera`);
    assert.ok(Math.abs(mars.x) + mars.rx <= margin, `aspect ${aspect}: inside the frame sideways (${mars.x.toFixed(2)} ± ${mars.rx.toFixed(2)})`);
    assert.ok(Math.abs(mars.y) + mars.ry <= margin, `aspect ${aspect}: inside the frame vertically (${mars.y.toFixed(2)} ± ${mars.ry.toFixed(2)})`);
    // Disc overlap in Earth-radius units. Wide screens have room for the whole disc; on square and portrait ones
    // Mars may peek from behind Earth's rim, so only its centre and half its disc must be clear.
    const separation = Math.hypot((mars.x - earth.x) / earth.rx, (mars.y - earth.y) / earth.ry);
    const ratio = mars.ry / earth.ry;
    const need = wide ? 1 + ratio + 0.02 : 1 + ratio * 0.5;
    assert.ok(separation > need, `aspect ${aspect}: clear of Earth (${separation.toFixed(2)} vs ${need.toFixed(2)})`);
    assert.ok(mars.ry >= 0.09, `aspect ${aspect}: still readable (${mars.ry.toFixed(2)} of the half-height)`);
  }
});

test("Mars stays on the right half of the screen, so it never sits over the copy on the left", () => {
  for (const aspect of ASPECTS) {
    const { position, radius } = bodyPlacement("mars", aspect);
    const mars = projectHome(position, radius, aspect);
    assert.ok(mars.x - mars.rx > 0.2, `aspect ${aspect}: right of centre`);
    assert.ok(mars.y > 0.3, `aspect ${aspect}: upper half`);
  }
});

test("an unknown body is refused", () => {
  assert.throws(() => bodyPlacement("venus", 1.6), /unknown body/);
});
