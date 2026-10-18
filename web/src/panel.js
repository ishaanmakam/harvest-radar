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
  pending: "#f4a259",
  sent: "#3bb273",
  failed: "#d7263d",
};

const ROUTE_PAGE = 12;

function sourceLabel(source) {
  if (source === "perceptron") return "Perceptron";
  if (source === "vegetation_index") return "Vegetation index";
  return source || "";
}

function channelLabel(channel) {
  if (channel === "grok_voice") return "Voice";
  if (channel === "photon_sms") return "SMS";
  return channel || "";
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
  if (status === "act_now" || status === "failed") el.style.color = "#fff";
  return el;
}

export function renderRoute(el, route, options) {
  const { onSelect, limit = ROUTE_PAGE, selectedId = "", officerDeliveries = [], onLimit } = options;
  el.replaceChildren();
  addText(el, "h2", "Route");
  if (!route?.stops?.length) {
    addText(el, "p", "No route for this morning.", "empty");
    return;
  }

  addText(el, "p", `${route.stops.length} stops · ${route.as_of}`, "meta");
  if (officerDeliveries.length) {
    const bits = officerDeliveries.map((row) => `${channelLabel(row.channel)} ${row.status}`);
    addText(el, "p", `Officer · ${bits.join(" · ")}`, "meta");
  }

  const list = document.createElement("ol");
  for (const stop of route.stops.slice(0, limit)) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = stop.farm_id === selectedId ? "stop selected" : "stop";
    const title = document.createElement("span");
    title.className = "stop-title";
    addText(title, "strong", stop.farm_id);
    addText(title, "span", stop.action);
    if (stop.status) chip(title, stop.status.replace("_", " "), stop.status);
    button.appendChild(title);
    addText(button, "span", stop.why, "why");
    button.addEventListener("click", () => onSelect(stop.farm_id));
    item.appendChild(button);
    list.appendChild(item);
  }
  el.appendChild(list);

  const left = route.stops.length - limit;
  if (left > 0) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    more.textContent = `Show ${Math.min(ROUTE_PAGE, left)} more (${left} left)`;
    more.addEventListener("click", () => onLimit(limit + ROUTE_PAGE));
    el.appendChild(more);
  }
}

export function renderPlan(el, plan, feature, deliveries) {
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

  addText(el, "h3", "Delivery");
  if (!deliveries || deliveries.length === 0) {
    addText(el, "p", "No delivery recorded.");
  } else {
    for (const row of deliveries) {
      const line = document.createElement("div");
      line.className = "delivery";
      chip(line, row.status, row.status);
      addText(line, "span", channelLabel(row.channel));
      if (row.detail) addText(line, "span", row.detail, "meta");
      el.appendChild(line);
    }
  }

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
