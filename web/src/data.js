import { SEVERITY } from "./format.js";

// Every file is tried in data/out first and falls back to data/mock on its own, so a half-finished
// real season still renders. `?base=/mock` (or any folder under data/) pins every file to one base
// with no fallback, which is how the missing-optional-files path gets exercised.
const FILES = [
  ["fields", "fields.geojson"],
  ["plans", "plans.json"],
  ["zones", "zones.geojson"],
  ["route", "route.json"],
  ["deliveries", "deliveries.json"],
  ["frames", "frames/frames.json"],
  ["timeseries", "timeseries.json"],
  ["status", "status.json"],
  ["weather", "weather.json"],
  ["analytics", "analytics.json"],
  ["insights", "insights.json"],
  ["evaluation", "evaluation.json"],
];

const OPTIONAL = FILES.map(([key]) => key).filter((key) => key !== "fields" && key !== "plans");

async function fetchJson(url) {
  try {
    const res = await fetch(url);
    const type = res.headers.get("content-type") || "";
    if (!res.ok || !type.includes("json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function pinnedBase() {
  const value = new URLSearchParams(window.location.search).get("base");
  if (!value) return null;
  const trimmed = value.trim().replace(/\/+$/, "");
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

async function loadFile(path, pinned) {
  if (pinned) {
    const data = await fetchJson(`${pinned}/${path}`);
    return { data, source: data ? pinned.slice(1) : null, base: pinned };
  }
  const out = await fetchJson(`/out/${path}`);
  if (out) return { data: out, source: "out", base: "/out" };
  const mock = await fetchJson(`/mock/${path}`);
  if (mock) return { data: mock, source: "mock", base: "/mock" };
  return { data: null, source: null, base: null };
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

export async function loadData() {
  const pinned = pinnedBase();
  const loaded = await Promise.all(FILES.map(async ([key, path]) => [key, await loadFile(path, pinned)]));
  const byKey = Object.fromEntries(loaded);
  const sources = Object.fromEntries(loaded.map(([key, file]) => [key, file.source]));
  const get = (key) => byKey[key].data;

  const fields = get("fields");
  const plans = get("plans");
  if (!fields || !plans) {
    const missing = [!fields && "fields.geojson", !plans && "plans.json"].filter(Boolean).join(" and ");
    throw new Error(`Could not load ${missing}${pinned ? ` from ${pinned}` : " from data/out or data/mock"}.`);
  }
  const missingOptional = OPTIONAL.filter((key) => !get(key)).map((key) => FILES.find(([k]) => k === key)[1]);
  if (missingOptional.length) console.info(`Optional files not found: ${missingOptional.join(", ")}`);

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
  const framesBase = byKey.frames.base || "/mock";

  return {
    sources,
    pinned,
    isMock: sources.plans !== "out",
    framesBase,
    fields,
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
    insights: get("insights"),
    evaluation: get("evaluation"),
    farmIds,
    harvestPct: harvestFromAnalytics(analytics) ?? harvestFromStatus(status),
    missingOptional,
  };
}
