import { h } from "../dom.js";
import { CHANNEL_LABEL, CHECK_LABEL, chip, fmtDate, fmtHours, fmtKm, titleCase } from "../format.js";

function stat(label, value) {
  return h("div", { class: "stat" }, h("div", { class: "stat-value" }, value), h("div", { class: "stat-label" }, label));
}

/** analytics.landscape.route adds km, ETAs and totals to the stops in route.json when it exists. */
function routeAnalytics(data) {
  const route = data.analytics?.landscape?.route;
  if (!route || typeof route !== "object") return null;
  const visits = new Map();
  for (const visit of Array.isArray(route.visits) ? route.visits : []) visits.set(visit.farm_id, visit);
  const calls = new Map();
  for (const call of Array.isArray(route.calls) ? route.calls : []) calls.set(call.farm_id, call);
  return { route, visits, calls };
}

export function renderRoute(el, ctx) {
  const { data, store } = ctx;

  if (!data.route) {
    el.replaceChildren(
      h("div", { class: "section" }, h("h2", { class: "panel-title" }, "Officer's day")),
      h("div", { class: "empty" }, "No route in this dataset. The decision layer writes route.json beside plans.json."),
    );
    return { update() {} };
  }

  const stops = data.route.stops || [];
  const extra = routeAnalytics(data);
  const visits = stops.filter((s) => s.action === "visit");
  const calls = stops.filter((s) => s.action !== "visit");

  const totals = [stat("Visits", String(visits.length)), stat("Calls", String(calls.length))];
  if (extra?.route.total_km != null) totals.push(stat("Driving", fmtKm(extra.route.total_km)));
  if (extra?.route.total_hours != null) totals.push(stat("Driving time", fmtHours(extra.route.total_hours)));

  const timing = [];
  if (extra?.route.departure) timing.push(`leaves ${extra.route.departure}`);
  if (extra?.route.finish_time) timing.push(`last visit ${extra.route.finish_time}`);
  if (extra?.route.km_saved != null && extra.route.km_saved > 0) timing.push(`${fmtKm(extra.route.km_saved)} saved against the listed order`);

  const officer = data.officerDeliveries || [];
  const rows = new Map();

  function stopRow(stop, number) {
    const plan = data.plansById.get(stop.farm_id);
    const detail = stop.action === "visit" ? extra?.visits.get(stop.farm_id) : extra?.calls.get(stop.farm_id);
    const meta = [];
    if (stop.kind) meta.push(CHECK_LABEL[stop.kind] || titleCase(stop.kind));
    if (detail?.km_from_previous != null) meta.push(`${fmtKm(detail.km_from_previous)} from the previous stop`);
    if (detail?.eta) meta.push(`ETA ${detail.eta}`);
    const row = h(
      "button",
      {
        class: `stop stop-${stop.action === "visit" ? "visit" : "call"}`,
        type: "button",
        dataset: { farm: stop.farm_id },
        onclick: () => ctx.select(stop.farm_id),
      },
      h("span", { class: "n", "aria-hidden": "true" }, String(number)),
      h(
        "span",
        { style: { minWidth: 0 } },
        h(
          "div",
          { class: "stop-head" },
          h("span", {}, stop.farm_id),
          chip(stop.action === "visit" ? "visit" : "call", stop.action === "visit" ? "Visit" : "Call"),
          plan ? chip(plan.flag) : null,
        ),
        stop.why ? h("div", { class: "stop-why" }, stop.why) : null,
        meta.length ? h("div", { class: "stop-meta" }, meta.join(" / ")) : null,
      ),
    );
    rows.set(stop.farm_id, row);
    return h("li", {}, row);
  }

  el.replaceChildren(
    h(
      "div",
      { class: "section" },
      h("h2", { class: "panel-title" }, "Officer's day"),
      h("p", { class: "muted small" }, `Route for ${fmtDate(data.route.as_of, "long")}. Act-now farms are visited first, then the watch farms get a call.`),
      h("div", { class: "route-totals", style: { marginTop: "10px" } }, ...totals),
      timing.length ? h("p", { class: "muted small", style: { marginTop: "8px" } }, titleCase(timing.join(" / "))) : null,
      officer.length
        ? h(
            "div",
            { class: "delivery-badges", style: { marginTop: "8px" } },
            ...officer.map((d) => h("span", { class: "delivery-badge", title: d.detail || "" }, `Officer ${(CHANNEL_LABEL[d.channel] || titleCase(d.channel)).toLowerCase()}`, chip(d.status))),
          )
        : null,
      ...(Array.isArray(extra?.route.notes) ? extra.route.notes : []).map((note) => h("div", { class: "note note-watch", style: { marginTop: "8px" } }, note)),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Visits", h("span", { class: "count" }, String(visits.length))),
      visits.length ? h("ol", { class: "stops" }, ...visits.map((stop, i) => stopRow(stop, i + 1))) : h("p", { class: "muted small" }, "No visits today."),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Calls", h("span", { class: "count" }, String(calls.length))),
      calls.length ? h("ol", { class: "stops" }, ...calls.map((stop, i) => stopRow(stop, visits.length + i + 1))) : h("p", { class: "muted small" }, "No calls today."),
    ),
  );

  function highlight() {
    const selected = store.get().selected;
    for (const [id, row] of rows) {
      const on = id === selected;
      row.classList.toggle("is-selected", on);
      if (on) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
    if (selected && rows.has(selected)) rows.get(selected).scrollIntoView({ block: "nearest" });
  }

  highlight();
  return {
    update(keys) {
      if (keys.includes("selected")) highlight();
    },
  };
}
