const KIND_LABELS = {
  harvest_ready: "Harvest readiness",
  harvest_before_rain: "Harvest vs rain",
  establishment: "Establishment",
  burn_scar: "Burn scar",
  fire: "Fire",
  flood: "Flood",
  stress: "Stress",
  visible_change: "Visible change",
};

const STATUS_COLORS = {
  clear: "#8a9a8e",
  watch: "#f4a259",
  act_now: "#d7263d",
  fine: "#3bb273",
  none: "#888888",
};

function sourceLabel(source) {
  if (source === "perceptron") return "Perceptron";
  if (source === "vegetation_index") return "Vegetation index";
  return source || "";
}

function addText(parent, tag, text, className) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  el.textContent = text;
  parent.appendChild(el);
  return el;
}

function chip(parent, text, status) {
  const el = addText(parent, "span", text, "chip");
  el.style.background = STATUS_COLORS[status] || "#888888";
  if (status === "act_now") el.style.color = "#fff";
  return el;
}

export function renderPlan(el, plan, feature) {
  el.replaceChildren();
  if (!plan) {
    addText(el, "p", feature ? "No plan for this farm." : "Click a farm to see its morning plan.", "empty");
    return;
  }

  const props = feature?.properties || {};
  const head = document.createElement("div");
  head.className = "plan-head";
  addText(head, "h2", props.field_id || plan.farm_id);
  chip(head, plan.flag.replace("_", " "), plan.flag);
  el.appendChild(head);

  addText(el, "p", `Zone ${props.zone_id || ""} · ${props.area_ha} ha`, "meta");
  addText(el, "p", `${plan.as_of} · ${sourceLabel(plan.source)}`, "meta");
  addText(el, "p", plan.today, "today");

  const checks = document.createElement("div");
  checks.className = "checks";
  for (const check of plan.checks) {
    const row = document.createElement("div");
    row.className = "check";
    const title = document.createElement("div");
    title.className = "check-title";
    addText(title, "span", KIND_LABELS[check.kind] || check.kind);
    chip(title, check.status.replace("_", " "), check.status);
    row.appendChild(title);
    addText(row, "p", check.detail, "detail");
    if (check.where) addText(row, "p", check.where, "meta");
    const start = check.window?.start;
    const end = check.window?.end;
    if (start || end) addText(row, "p", `${start || ""} – ${end || ""}`.trim(), "meta");
    checks.appendChild(row);
  }
  el.appendChild(checks);

  addText(el, "h3", "Risks");
  if (!plan.risks || plan.risks.length === 0) {
    addText(el, "p", "No risks today.");
  } else {
    const list = document.createElement("ul");
    for (const risk of plan.risks) addText(list, "li", risk);
    el.appendChild(list);
  }
}
