async function fetchJson(url) {
  const res = await fetch(url);
  const type = res.headers.get("content-type") || "";
  if (!res.ok || !type.includes("json")) return null;
  return res.json();
}

export async function loadData() {
  const probe = await fetch("/out/plans.json");
  const probeType = probe.headers.get("content-type") || "";
  const base = probe.ok && probeType.includes("json") ? "/out" : "/mock";

  const [fields, plans, route, deliveries, frames] = await Promise.all([
    fetchJson(`${base}/fields.geojson`),
    fetchJson(`${base}/plans.json`),
    fetchJson(`${base}/route.json`),
    fetchJson(`${base}/deliveries.json`),
    fetchJson(`${base}/frames/frames.json`),
  ]);

  if (!fields || !plans) {
    const missing = [!fields && "fields.geojson", !plans && "plans.json"].filter(Boolean).join(" and ");
    throw new Error(`Could not load ${missing} from ${base}.`);
  }
  if (!route) console.warn(`Skipping ${base}/route.json`);
  if (!deliveries) console.warn(`Skipping ${base}/deliveries.json`);
  if (!frames) console.warn(`Skipping ${base}/frames/frames.json`);

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

  return {
    base,
    isMock: base === "/mock",
    fields,
    plans,
    plansById,
    route,
    deliveries,
    deliveriesByFarm,
    officerDeliveries,
    frames,
  };
}
