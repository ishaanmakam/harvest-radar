import { h, skeleton } from "../dom.js";
import { fmtDate, fmtPct } from "../format.js";

function tile(label, value, className) {
  return h(
    "div",
    { class: `stat${className ? ` ${className}` : ""}` },
    h("div", { class: "stat-value" }, value),
    h("div", { class: "stat-label" }, label),
  );
}

export function renderHeadlineSkeleton(el) {
  el.replaceChildren(
    ...[0, 1, 2, 3, 4].map(() =>
      h("div", { class: "stat" }, skeleton("44px", 20), skeleton("56px", 10)),
    ),
  );
}

export function renderHeadline(el, data) {
  const counts = { act_now: 0, watch: 0, fine: 0 };
  for (const plan of data.plans.plans || []) {
    if (counts[plan.flag] != null) counts[plan.flag] += 1;
  }
  const tiles = [
    tile("Plan for", fmtDate(data.plans.as_of)),
    tile("Farms", String(data.fields.features.length)),
    tile("Act now", String(counts.act_now), "stat-act"),
    tile("Watch", String(counts.watch), "stat-watch"),
    tile("Fine", String(counts.fine), "stat-fine"),
  ];
  if (data.harvestPct != null) tiles.push(tile("Harvested", fmtPct(data.harvestPct)));
  el.replaceChildren(...tiles);
}
