import { h } from "../dom.js";

/**
 * The Regions page: every world region from data/global/regions.json with its stage line, and a way into its own
 * season map (?season=<id>, the stand-in season generated for the demo) or to its dot on the globe (?region=<id>).
 */
const REGIONS_URL = "global/regions.json";
const SEASONS_URL = "global/seasons/index.json";

async function fetchJson(url) {
  try {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok || !(res.headers.get("content-type") || "").includes("json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function capital(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

export function renderRegions(el) {
  let alive = true;
  el.replaceChildren(h("p", { class: "muted small" }, "Loading the regions."));
  Promise.all([fetchJson(REGIONS_URL), fetchJson(SEASONS_URL)]).then(([doc, seasons]) => {
    if (!alive) return;
    if (!doc?.regions?.length) {
      el.replaceChildren(h("p", { class: "muted" }, "No regions file in this dataset."));
      return;
    }
    const have = new Set(Object.keys(seasons?.seasons || {}));
    const base = window.location.pathname;
    el.replaceChildren(
      h("p", { class: "lede" }, `${doc.regions.length} farm regions on six continents. Story County, Iowa, is the live season read from Sentinel-2. The other regions carry a stand-in season generated for the demo over their own farmland: fields, flags, checks and a route, with every value made up.`),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Region"), h("th", {}, "Crops"), h("th", {}, "Stage"), h("th", {}, "Clear passes"), h("th", {}, "Open"))),
        h("tbody", {}, ...doc.regions.map((r) => {
          const live = r.id === "us-ia-story";
          const seasonHref = live ? base : `${base}?season=${encodeURIComponent(r.id)}`;
          return h("tr", {},
            h("td", {}, h("strong", {}, r.name), h("div", { class: "muted small" }, r.country)),
            h("td", { class: "small" }, (r.crops || []).join(", ")),
            h("td", { class: "small" }, capital(r.stage || "")),
            h("td", { class: "num small" }, r.clear_passes == null ? "" : String(r.clear_passes)),
            h("td", { class: "small" },
              live || have.has(r.id) ? h("a", { href: seasonHref }, live ? "Open the live season" : "Open the season map") : h("span", { class: "muted" }, "No season yet"),
              " / ",
              h("a", { href: `${base}?region=${encodeURIComponent(r.id)}` }, "Show on the globe")),
          );
        })),
      )),
      h("p", { class: "muted small", style: { marginTop: "12px" } }, doc.method || ""),
    );
  });
  return { destroy() { alive = false; } };
}
