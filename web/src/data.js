import { SEVERITY } from "./format.js";

// The four farm files are one group: real fields joined to mock plans would show the wrong flags,
// because real and mock farm ids overlap (F0001...) but are different farms. The group comes from
// data/out only when BOTH fields.geojson and plans.json are real JSON there; otherwise all four come
// from data/mock, and a missing route or deliveries file inside a real group simply means "none yet".
// Every other file is tried in data/out first and falls back to data/mock on its own, so a
// half-finished real season still renders. `?base=/mock` (or any folder under data/) pins every file
// to one base with no fallback, which is how the missing-optional-files path gets exercised.
const GROUP = [
  ["fields", "fields.geojson"],
  ["plans", "plans.json"],
  ["route", "route.json"],
  ["deliveries", "deliveries.json"],
];

const PER_FILE = [
  ["zones", "zones.geojson"],
  ["yieldModel", "yield_model.json"],
  ["ftw", "ftw_fields_2024.geojson"],
  ["frames", "frames/frames.json"],
  ["timeseries", "timeseries.json"],
  ["status", "status.json"],
  ["weather", "weather.json"],
  ["analytics", "analytics.json"],
];

// Keyed by farm id (field_env) or describing the run that made the farm files (run): never mixed
// across bases, so they load from the farm group's base only.
const FARM_BASE = [
  ["run", "run.json"],
  ["fieldEnv", "field_env.json"],
];

const FILES = [...GROUP, ...FARM_BASE, ...PER_FILE];

// A host that does not know .geojson may serve it as a plain byte stream, so the body decides when
// the content type does not; an HTML fallback page (a missing file in dev) never parses as JSON.
async function fetchJson(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const type = res.headers.get("content-type") || "";
    if (type.includes("html")) return null;
    const text = await res.text();
    if (!type.includes("json") && !/^\s*[{[]/.test(text)) return null;
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// Every URL is relative to the page, so the built site runs from any folder or host.
function pinnedBase() {
  const value = new URLSearchParams(window.location.search).get("base");
  if (!value) return null;
  return value.trim().replace(/^\/+/, "").replace(/\/+$/, "") || null;
}

async function loadFile(path, pinned) {
  if (pinned) {
    const data = await fetchJson(`${pinned}/${path}`);
    return { data, source: data ? pinned : null, base: pinned };
  }
  const out = await fetchJson(`out/${path}`);
  if (out) return { data: out, source: "out", base: "out" };
  const mock = await fetchJson(`mock/${path}`);
  if (mock) return { data: mock, source: "mock", base: "mock" };
  return { data: null, source: null, base: null };
}

async function loadGroup(pinned) {
  const fromBase = async (base, known = {}) => {
    const entries = await Promise.all(
      GROUP.map(async ([key, path]) => {
        const data = key in known ? known[key] : await fetchJson(`${base}/${path}`);
        return [key, { data, source: data ? base : null, base }];
      }),
    );
    return Object.fromEntries(entries);
  };
  if (pinned) return { files: await fromBase(pinned), realFields: null };

  const [outFields, outPlans] = await Promise.all([fetchJson("out/fields.geojson"), fetchJson("out/plans.json")]);
  if (outFields && outPlans) return { files: await fromBase("out", { fields: outFields, plans: outPlans }), realFields: null };
  return { files: await fromBase("mock"), realFields: outFields || null };
}

function walkCoords(coords, visit) {
  if (typeof coords[0] === "number") {
    visit(coords);
    return;
  }
  for (const part of coords) walkCoords(part, visit);
}

export function geometryBbox(geometry) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  walkCoords(geometry.coordinates, ([lon, lat]) => {
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  });
  return [west, south, east, north];
}

export function collectionBbox(collection) {
  let bbox = [Infinity, Infinity, -Infinity, -Infinity];
  for (const feature of collection.features) {
    const b = geometryBbox(feature.geometry);
    bbox = [Math.min(bbox[0], b[0]), Math.min(bbox[1], b[1]), Math.max(bbox[2], b[2]), Math.max(bbox[3], b[3])];
  }
  return bbox;
}

function harvestFromStatus(status) {
  if (!status?.fields?.length) return null;
  const known = status.fields.filter((f) => f.stage && f.stage !== "unknown");
  if (!known.length) return null;
  const harvested = known.filter((f) => f.harvest_date).length;
  return Math.round((100 * harvested) / known.length);
}

function harvestFromAnalytics(analytics) {
  const land = analytics?.landscape?.landscape;
  if (!land || typeof land !== "object") return null;
  for (const key of ["pct_harvested_cropped_area", "pct_harvested_area", "pct_harvested"]) {
    if (typeof land[key] === "number") return Math.round(land[key]);
  }
  return null;
}

export const YIELD_BIN_COUNT = 5;

/**
 * Calibrated bushels an acre per field, cut into five equal bands per crop for the yield map.
 * Fields without a calibrated value get bin -1 and draw grey.
 */
function yieldBinsByCrop(fields, analytics) {
  const perCrop = new Map();
  for (const feature of fields.features) {
    const id = feature.properties.field_id;
    const y = analytics?.fields?.[id]?.yield;
    const bu = typeof y?.bu_per_acre_calibrated === "number" ? y.bu_per_acre_calibrated : null;
    feature.properties.yield_bu = bu;
    feature.properties.yield_bin = -1;
    if (bu == null) continue;
    const crop = y.crop || feature.properties.crop || "unknown";
    if (!perCrop.has(crop)) perCrop.set(crop, []);
    perCrop.get(crop).push({ feature, bu });
  }
  const bins = {};
  for (const [crop, rows] of perCrop) {
    const values = rows.map((r) => r.bu);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const step = (max - min) / YIELD_BIN_COUNT || 1;
    const edges = Array.from({ length: YIELD_BIN_COUNT + 1 }, (_, i) => min + i * step);
    for (const row of rows) {
      row.feature.properties.yield_bin = Math.min(YIELD_BIN_COUNT - 1, Math.floor((row.bu - min) / step));
    }
    bins[crop] = { edges, count: rows.length };
  }
  return bins;
}

export async function loadData() {
  const pinned = pinnedBase();
  const [group, perFile] = await Promise.all([
    loadGroup(pinned),
    Promise.all(PER_FILE.map(async ([key, path]) => [key, await loadFile(path, pinned)])),
  ]);
  const farmBase = group.files.fields.base;
  const farmBaseFiles = await Promise.all(
    FARM_BASE.map(async ([key, path]) => {
      const data = await fetchJson(`${farmBase}/${path}`);
      return [key, { data, source: data ? farmBase : null, base: farmBase }];
    }),
  );
  const byKey = { ...group.files, ...Object.fromEntries(farmBaseFiles), ...Object.fromEntries(perFile) };
  const sources = Object.fromEntries(FILES.map(([key]) => [key, byKey[key].source]));
  const get = (key) => byKey[key].data;

  const fields = get("fields");
  const plans = get("plans");
  if (!fields || !plans) {
    const missing = [!fields && "fields.geojson", !plans && "plans.json"].filter(Boolean).join(" and ");
    throw new Error(`Could not load ${missing}${pinned ? ` from ${pinned}` : " from data/out or data/mock"}.`);
  }
  const missingOptional = FILES.filter(([key]) => key !== "fields" && key !== "plans" && !get(key)).map(([, path]) => path);
  if (missingOptional.length) console.info(`Optional files not found: ${missingOptional.join(", ")}`);
  if (group.realFields) console.info("Real fields.geojson found in data/out without real plans: drawn as outlines only.");

  const plansById = new Map((plans.plans || []).map((plan) => [plan.farm_id, plan]));
  const featuresById = new Map();
  const centroids = new Map();
  for (const feature of fields.features) {
    const id = feature.properties.field_id;
    const plan = plansById.get(id);
    feature.properties.flag = plan ? plan.flag : "none";
    feature.properties.match = true;
    featuresById.set(id, feature);
    const [west, south, east, north] = geometryBbox(feature.geometry);
    centroids.set(id, [(west + east) / 2, (south + north) / 2]);
  }

  const zones = get("zones");
  const zonesById = new Map((zones?.features || []).map((f) => [f.properties.zone_id, f.properties]));
  const status = get("status");
  const statusById = new Map((status?.fields || []).map((f) => [f.field_id, f]));

  const deliveries = get("deliveries");
  const deliveriesByFarm = new Map();
  const officerDeliveries = [];
  for (const row of deliveries?.deliveries || []) {
    if (!row.farm_id) officerDeliveries.push(row);
    else {
      const rows = deliveriesByFarm.get(row.farm_id) || [];
      rows.push(row);
      deliveriesByFarm.set(row.farm_id, rows);
    }
  }

  const route = get("route");
  const routeByFarm = new Map();
  (route?.stops || []).forEach((stop, index) => routeByFarm.set(stop.farm_id, { ...stop, index }));

  const farmIds = fields.features
    .map((f) => f.properties.field_id)
    .sort((a, b) => {
      const sa = SEVERITY[plansById.get(a)?.flag ?? "none"];
      const sb = SEVERITY[plansById.get(b)?.flag ?? "none"];
      return sa - sb || a.localeCompare(b);
    });

  const analytics = get("analytics");
  const frames = get("frames");
  const yieldBins = yieldBinsByCrop(fields, analytics);

  return {
    sources,
    pinned,
    farmBase: byKey.fields.base,
    isMock: sources.plans !== "out",
    framesBase: byKey.frames.base || "mock",
    fields,
    realFields: group.realFields,
    plans,
    plansById,
    featuresById,
    centroids,
    bbox: collectionBbox(fields),
    zones,
    zonesById,
    route,
    routeByFarm,
    deliveries,
    deliveriesByFarm,
    officerDeliveries,
    frames,
    timeseries: get("timeseries"),
    status,
    statusById,
    weather: get("weather"),
    analytics,
    yieldModel: get("yieldModel"),
    fieldEnv: get("fieldEnv"),
    ftw: get("ftw"),
    run: get("run"),
    yieldBins,
    farmIds,
    harvestPct: harvestFromAnalytics(analytics) ?? harvestFromStatus(status),
    missingOptional,
  };
}
