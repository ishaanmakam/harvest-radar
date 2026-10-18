import { h, skeleton } from "../dom.js";
import { CROP_LABEL, fmtDate, fmtNum, fmtPct, titleCase } from "../format.js";

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

function yieldTiles(data) {
  const tiles = [tile("As of", fmtDate(data.yieldModel?.as_of || data.analytics?.as_of || data.plans.as_of))];
  const crops = data.yieldModel?.crops && typeof data.yieldModel.crops === "object" ? Object.entries(data.yieldModel.crops) : [];
  for (const [crop, model] of crops) {
    if (model.estimate_bu_per_acre == null) continue;
    const band = Array.isArray(model.interval_bu_per_acre) ? ` (${fmtNum(model.interval_bu_per_acre[0])} to ${fmtNum(model.interval_bu_per_acre[1])})` : "";
    tiles.push(tile(`${CROP_LABEL[crop] || titleCase(crop)} bu/acre${band}`, fmtNum(model.estimate_bu_per_acre), "stat-fine"));
  }
  const scored = Object.values(data.yieldBins || {}).reduce((sum, bins) => sum + bins.count, 0);
  if (scored) tiles.push(tile("Farms scored", `${scored} of ${data.fields.features.length}`));
  if (!crops.length && !scored) tiles.push(tile("County model", "not run"));
  return tiles;
}

export function renderHeadline(el, data, aspect = "health") {
  if (aspect === "yield") {
    el.replaceChildren(...yieldTiles(data));
    return;
  }
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
