import { h } from "../dom.js";

/**
 * The card for a world region (data/global/regions.json) when its green dot on the globe is clicked. The pure parts
 * (lines, pill, honesty line) are unit-tested; renderRegionCard builds the DOM in the same copy slot Mars uses.
 * The file's thumbnails are stage-coloured illustrations, not imagery, so the card leaves them out.
 */
export const HONESTY_LINE = "Region-level preview: one box about 5 km across at the region centre. The canopy curve is the "
  + "Iowa season shape adjusted for this region's hemisphere and crop, not a new satellite read.";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  if (!m) return null;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}`;
}

function num(v, digits) {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : null;
}

function capital(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** Label/value pairs for the card; anything missing is left out rather than shown as a blank or NaN. */
export function regionLines(region) {
  const lines = [];
  if (region.alert) lines.push({ key: "alert", label: "Alert", value: String(region.alert) });
  if (region.stage) lines.push({ key: "stage", label: "Stage", value: capital(region.stage) });
  const now = num(region.current?.value, 2);
  const peak = num(region.peak?.value, 2);
  const peakDay = fmtDay(region.peak?.date);
  if (now && peak) lines.push({ key: "canopy", label: "Canopy", value: `NDVI ${now} now, peak ${peak}${peakDay ? ` on ${peakDay}` : ""}` });
  else if (now) lines.push({ key: "canopy", label: "Canopy", value: `NDVI ${now} now` });
  if (typeof region.rain_next3_mm === "number" && Number.isFinite(region.rain_next3_mm)) {
    lines.push({ key: "rain", label: "Rain next 3 days", value: `${Math.round(region.rain_next3_mm)} mm${region.rain_flag ? ", watch" : ""}` });
  }
  if (typeof region.fires_5d === "number") lines.push({ key: "fires", label: "Fires, last 5 days", value: region.fires_5d ? String(region.fires_5d) : "none" });
  if (Array.isArray(region.crops) && region.crops.length) {
    const hand = /hand/i.test(region.crops_note || "");
    lines.push({ key: "crops", label: "Crops", value: `${region.crops.join(", ")}${hand ? " (hand-labelled)" : ""}` });
  }
  const passes = typeof region.clear_passes === "number" ? region.clear_passes : null;
  const latest = fmtDay(region.latest_clear_date);
  if (passes != null) lines.push({ key: "passes", label: "Clear passes", value: `${passes}${latest ? `, latest ${latest}` : ""}` });
  else if (latest) lines.push({ key: "passes", label: "Latest clear pass", value: latest });
  return lines;
}

export function regionPill(region) {
  return `Region watch${region.country ? ` / ${region.country}` : ""}`;
}

/**
 * @param region  one entry of regions.json
 * @param onBack  restores the Earth copy
 * @param onMap   opens the imagery map at the region (optional)
 */
export function renderRegionCard(region, { onBack = null, onMap = null } = {}) {
  const lines = regionLines(region);
  return h("div", { class: "region-card" },
    h("div", { class: "region-head" },
      onBack ? h("button", { class: "region-back", type: "button", onclick: onBack }, "Return to Earth") : null,
      h("span", { class: "world-pill is-open" }, regionPill(region))),
    h("h1", {}, region.name || region.id),
    region.headline ? h("p", { class: "tagline" }, region.headline) : null,
    lines.length ? h("dl", { class: "region-lines" }, ...lines.flatMap((l) => [
      h("dt", {}, l.label),
      h("dd", { class: l.key === "alert" || (l.key === "rain" && region.rain_flag) ? "is-alert" : "" }, l.value),
    ])) : null,
    h("p", { class: "region-note" }, HONESTY_LINE),
    onMap ? h("div", { class: "region-actions" }, h("button", { class: "world-open", type: "button", onclick: onMap }, "Open the map here")) : null,
  );
}
