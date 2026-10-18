const FILES = [
  ["fields", "fields.geojson"],
  ["plans", "plans.json"],
  ["route", "route.json"],
  ["deliveries", "deliveries.json"],
  ["frames", "frames/frames.json"],
  ["insights", "insights.json"],
  ["evaluation", "evaluation.json"],
];

async function fetchJson(url) {
  const res = await fetch(url);
  const type = res.headers.get("content-type") || "";
  if (!res.ok || !type.includes("json")) return null;
  return res.json();
}

async function loadFile(path) {
  const out = await fetchJson(`/out/${path}`);
  if (out) return { data: out, source: "out" };
  const mock = await fetchJson(`/mock/${path}`);
  if (mock) return { data: mock, source: "mock" };
  return { data: null, source: null };
}

export async function loadData() {
  const loaded = await Promise.all(FILES.map(async ([key, path]) => [key, await loadFile(path)]));
  const byKey = Object.fromEntries(loaded);
  const sources = Object.fromEntries(loaded.map(([key, file]) => [key, file.source]));
  const fields = byKey.fields.data;
  const plans = byKey.plans.data;
  const route = byKey.route.data;
  const deliveries = byKey.deliveries.data;
  const frames = byKey.frames.data;

  if (!fields || !plans) {
    const missing = [!fields && "fields.geojson", !plans && "plans.json"].filter(Boolean).join(" and ");
    throw new Error(`Could not load ${missing}.`);
  }
  for (const key of ["route", "deliveries", "frames", "insights", "evaluation"]) {
    if (!byKey[key].data) console.warn(`Skipping ${key}`);
  }

  const plansById = new Map(plans.plans.map((plan) => [plan.farm_id, plan]));
  for (const feature of fields.features) {
    const plan = plansById.get(feature.properties.field_id);
    feature.properties.flag = plan ? plan.flag : "none";
  }

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

  const framesBase = sources.frames === "out" ? "/out" : "/mock";
  return {
    base: framesBase,
    framesBase,
    isMock: sources.plans !== "out",
    sources,
    fields,
    plans,
    plansById,
    route,
    deliveries,
    deliveriesByFarm,
    officerDeliveries,
    frames,
    insights: byKey.insights.data,
    evaluation: byKey.evaluation.data,
  };
}
