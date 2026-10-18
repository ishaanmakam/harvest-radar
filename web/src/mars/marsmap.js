import maplibregl from "maplibre-gl";
import { h } from "../dom.js";
import { chip } from "../format.js";

/**
 * The Mars landing-site map: a second MapLibre instance over our own pre-warped images (no network sources), the
 * ranked sites and SpaceX's 2019 candidates as DOM markers, a dock with the list, the rules and the SpaceX section,
 * and a drawer per site in the same visual language as the farm plan. Everything comes from out/mars/sites.json,
 * read fresh on first open; the file is absent until the pipeline has run, and the view says so.
 */
const BASE = "out/mars/";
const STATUS_CHIP = { go: ["fine", "Go"], watch: ["watch", "Watch"], no_go: ["act_now", "No-go"], no_data: ["none", "No data"] };
const STATUS_COLOR = { go: "#43b36b", watch: "#f2a93b", no_go: "#d7263d" };
const FACTOR_LINE = {
  ice: "Ice within reach: water for propellant, air and crops.",
  low_elevation: "Low ground: more atmosphere to slow the ship.",
  plume_safe_ground: "Plume-safe ground: firm soil the engines will not dig out.",
  science_reach: "Science reach: more kinds of rock within a rover's range.",
  magnetic_shelter: "Magnetic shelter: a little local radiation cover from magnetized crust.",
};

function statusChip(status, large = false) {
  const [key, label] = STATUS_CHIP[status] || STATUS_CHIP.no_data;
  return chip(key, label, { large });
}

function fmtNum(v, digits = 0) {
  return v == null ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function coords(p) {
  const lat = `${Math.abs(p.lat).toFixed(2)} ${p.lat >= 0 ? "N" : "S"}`;
  const lon = `${Math.abs(p.lon).toFixed(2)} ${p.lon >= 0 ? "E" : "W"}`;
  return `${lat}, ${lon}`;
}

export function createMarsView({ host, onBack }) {
  const $ = (id) => host.querySelector(`#${id}`);
  const dockBody = $("mars-dock-body");
  const drawer = $("mars-drawer");
  const drawerBody = $("mars-drawer-body");
  const dock = $("mars-dock");
  const handle = $("mars-dock-handle");
  let map = null;
  let doc = null;
  let loading = null;
  let open = false;
  let selected = null;
  const markers = [];
  const list = [];                       // ids in dock order for arrow keys

  $("mars-back").addEventListener("click", () => onBack?.());
  handle.addEventListener("click", () => {
    const expanded = dock.classList.toggle("is-expanded");
    handle.setAttribute("aria-expanded", String(expanded));
  });

  async function loadSites() {
    if (doc) return doc;
    if (!loading) {
      loading = fetch(`${BASE}sites.json`, { cache: "no-cache" }).then(async (res) => {
        if (!res.ok || !(res.headers.get("content-type") || "").includes("json")) return null;
        return res.json();
      }).catch(() => null);
    }
    doc = await loading;
    return doc;
  }

  function emptyState() {
    dockBody.replaceChildren(
      h("div", { class: "empty" }, "Mars data not built yet. Run ", h("code", { class: "inline-code" }, "python pipeline/mars/run_all.py"), "."),
    );
  }

  // ---------- map ----------
  function buildMap() {
    const corners = doc.assets?.map_corners || [[-180, 70], [180, 70], [180, -70], [-180, -70]];
    const style = {
      version: 8,
      sources: {
        base: { type: "image", url: `${BASE}${doc.assets?.map_base || "mars_base_merc.jpg"}`, coordinates: corners },
        score: { type: "image", url: `${BASE}${doc.assets?.map_score || "mars_score_merc.png"}`, coordinates: corners },
        sites: { type: "geojson", data: featureCollection(doc.sites, (s) => ({ id: s.id, status: s.status })) },
        spacex: { type: "geojson", data: featureCollection(doc.spacex_candidates.filter((c) => !c.missing), (c) => ({ id: c.id })) },
      },
      layers: [
        { id: "base", type: "raster", source: "base", paint: { "raster-fade-duration": 0 } },
        { id: "score", type: "raster", source: "score", paint: { "raster-opacity": 0.55, "raster-fade-duration": 0, "raster-resampling": "linear" }, layout: { visibility: "visible" } },
        { id: "spacex-ring", type: "circle", source: "spacex", paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 } },
        { id: "sites-dot", type: "circle", source: "sites", paint: {
          "circle-radius": 11, "circle-color": ["match", ["get", "status"], "go", STATUS_COLOR.go, "watch", STATUS_COLOR.watch, STATUS_COLOR.no_go],
          "circle-stroke-color": "#0d1217", "circle-stroke-width": 2 } },
      ],
    };
    map = new maplibregl.Map({
      container: $("mars-map"), style, center: [0, 10], zoom: 1.4, minZoom: 1, maxZoom: 6, renderWorldCopies: false,
      attributionControl: false,
    });
    host.__map = map;
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "MOLA, TES (NASA/USGS); ice: SWIM (PSI); geology: USGS SIM 3292" }));
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.on("load", () => {
      fitPlanet();
      for (const s of doc.sites) markers.push(marker(s.lon, s.lat, h("div", { class: "mars-site-marker", "data-status": s.status }, String(s.rank)), () => select(s.id)));
      for (const c of doc.spacex_candidates) {
        if (c.missing) continue;
        markers.push(marker(c.lon, c.lat, h("div", { class: "mars-spacex-marker" }, h("span", {}, "SpaceX 2019")), () => select(c.id)));
      }
      for (const lm of doc.landmarks || []) markers.push(marker(lm.lon, lm.lat, h("div", { class: "mars-landmark" }, lm.name), null));
    });
    map.on("click", "sites-dot", (e) => select(e.features[0].properties.id));
    map.on("click", "spacex-ring", (e) => select(e.features[0].properties.id));
  }

  /** Frame the whole mapped planet (70 S to 70 N) beside the dock, and never zoom out past it into the void. */
  function fitPlanet() {
    const wide = window.matchMedia("(min-width: 768px)").matches;
    map.fitBounds([[-180, -70], [180, 70]], { duration: 0, padding: { top: 8, bottom: 8, left: wide ? 540 : 8, right: 8 } });
    map.setMinZoom(Math.max(0.5, map.getZoom() - 0.1));
  }

  function featureCollection(items, props) {
    return { type: "FeatureCollection", features: items.map((it) => ({ type: "Feature", geometry: { type: "Point", coordinates: [it.lon, it.lat] }, properties: props(it) })) };
  }

  function marker(lon, lat, el, onclick) {
    if (onclick) el.addEventListener("click", onclick);
    return new maplibregl.Marker({ element: el, anchor: onclick ? "center" : "left" }).setLngLat([lon, lat]).addTo(map);
  }

  function setScoreVisible(on) {
    if (map?.getLayer("score")) map.setLayoutProperty("score", "visibility", on ? "visible" : "none");
  }

  // ---------- dock ----------
  function factorChips() {
    return h("div", { class: "mars-factors" }, ...doc.factors.map((f) =>
      h("span", { class: `mars-factor-chip is-${f.from}`, title: f.why }, h("b", {}, f.from === "spacex" ? "SpaceX" : "Ours"), f.name)));
  }

  function siteRow(s) {
    return h("button", { class: `mars-row${selected === s.id ? " is-active" : ""}`, type: "button", "data-id": s.id, onclick: () => select(s.id) },
      h("span", { class: "mars-rank" }, String(s.rank)),
      h("span", { class: "mars-row-main" }, h("strong", {}, s.id), s.region ? h("span", { class: "mars-region" }, ` ${s.region}`) : null, h("span", { class: "muted small" }, ` ${coords(s)}`), h("div", { class: "small" }, s.reason)),
      h("span", { class: "mars-row-side" }, statusChip(s.status), h("span", { class: "mono small" }, s.score.toFixed(2))),
    );
  }

  function candidateRow(c) {
    return h("button", { class: `mars-row${selected === c.id ? " is-active" : ""}`, type: "button", "data-id": c.id, onclick: () => select(c.id) },
      h("span", { class: "mars-rank is-spacex" }, "○"),
      h("span", { class: "mars-row-main" }, h("strong", {}, c.id), h("span", { class: "muted small" }, ` ${c.region}`),
        h("div", { class: "small" }, c.missing ? "Page could not be read; listed as missing." : `Scores better than ${fmtNum(c.percentile)} percent of scored cells.`)),
      h("span", { class: "mars-row-side" }, c.missing ? statusChip("no_data") : statusChip(c.status), c.score != null ? h("span", { class: "mono small" }, c.score.toFixed(2)) : null),
    );
  }

  function rules() {
    return h("details", { class: "mars-rules" },
      h("summary", {}, "How we rank"),
      h("p", { class: "small muted" }, doc.method),
      h("table", { class: "table mars-rules-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Check"), h("th", {}, "Go"), h("th", {}, "Watch"), h("th", {}, "No-go"), h("th", {}, "Weight"))),
        h("tbody", {}, ...doc.factors.map((f) => h("tr", {}, h("td", {}, h("strong", {}, f.name), h("div", { class: "muted small" }, f.dataset)), h("td", {}, f.go), h("td", {}, f.watch), h("td", {}, f.no_go), h("td", { class: "num" }, f.weight.toFixed(2)))))),
      h("div", { class: "section-title" }, "Shown, not scored"),
      h("ul", { class: "small explainer-list" }, ...doc.shown_not_scored.map((x) => h("li", {}, h("strong", {}, x.name), ": ", x.why))),
      h("div", { class: "section-title" }, "Blind spots"),
      h("ul", { class: "small explainer-list" }, ...doc.blind_spots.map((b) => h("li", {}, b))),
      h("p", { class: "small muted" }, `Cells scored: ${fmtNum(doc.stats.cells_scored)} (go ${fmtNum(doc.stats.go)}, watch ${fmtNum(doc.stats.watch)}, no-go ${fmtNum(doc.stats.no_go)}).`),
    );
  }

  function renderDock() {
    list.length = 0;
    const scoreToggle = h("input", { type: "checkbox", checked: true, onchange: (e) => setScoreVisible(e.target.checked) });
    dockBody.replaceChildren(
      h("div", { class: "section" },
        h("p", { class: "small" }, "Four checks on every cell. From SpaceX's stated criteria: ice within reach and low ground. From us: plume-safe ground and science reach. Sunlight and landing-scale rocks are shown, not scored."),
        factorChips(),
        h("label", { class: "check-row small", style: { marginTop: "8px" } }, scoreToggle, h("span", {}, "Show site scores on the map")),
      ),
      h("div", { class: "section" }, h("div", { class: "section-title" }, "Our top ten", h("span", { class: "count" }, String(doc.sites.length))),
        h("div", { class: "mars-list" }, ...doc.sites.map((s) => { list.push(s.id); return siteRow(s); }))),
      h("div", { class: "section" }, h("div", { class: "section-title" }, "SpaceX's 2019 sites"),
        h("p", { class: "small" }, "SpaceX scouted six sites in 2019. We graded them on what they don't publicly screen for."),
        h("div", { class: "mars-list" }, ...doc.spacex_candidates.map((c) => { if (!c.missing) list.push(c.id); return candidateRow(c); })),
        h("p", { class: "small", style: { marginTop: "8px" } }, doc.spacex_summary)),
      h("div", { class: "section" }, rules()),
    );
  }

  // ---------- drawer ----------
  function checksTable(p) {
    return h("div", { class: "mars-checks" }, ...p.checks.map((c) => {
      const f = doc.factors.find((x) => x.id === c.factor);
      return h("div", { class: "mars-check" },
        statusChip(c.status),
        h("div", { class: "mars-check-main" },
          h("strong", {}, f?.name || c.factor), h("span", { class: `mars-from is-${f?.from || "ours"}` }, f?.from === "spacex" ? "SpaceX" : "ours"),
          h("div", { class: "small" }, c.detail),
          h("div", { class: "muted small" }, FACTOR_LINE[c.factor] || "")),
        h("span", { class: "mono small" }, c.goodness == null ? "" : c.goodness.toFixed(2)));
    }));
  }

  function shownNotScored(p) {
    const ctx = p.context || {};
    const why = Object.fromEntries(doc.shown_not_scored.map((x) => [x.id, x]));
    return h("div", { class: "section" }, h("div", { class: "section-title" }, "Shown, not scored"),
      h("ul", { class: "small explainer-list" },
        h("li", {}, h("strong", {}, "Regional slope "), ctx.slope_deg == null ? "" : `${ctx.slope_deg.toFixed(2)} degrees over about 20 km. `, h("span", { class: "muted" }, why.slope?.why || "")),
        h("li", {}, h("strong", {}, "Latitude "), ctx.abs_latitude == null ? "" : `${ctx.abs_latitude.toFixed(1)} degrees from the equator. `, h("span", { class: "muted" }, why.latitude?.why || "")),
        h("li", {}, h("strong", {}, "Dust (albedo) "), ctx.albedo == null ? "" : `${ctx.albedo.toFixed(2)}. `, h("span", { class: "muted" }, why.albedo?.why || ""))));
  }

  function geologyBlock(p) {
    const g = p.geology || {};
    return h("div", { class: "section" }, h("div", { class: "section-title" }, "Geology"),
      h("p", { class: "small" }, g.unit ? `Unit ${g.unit} (${g.age || "age unknown"}) at the site; ${g.units_within_100km ?? "?"} distinct units within about 100 km; ${g.oldest_within_100km || "age mix unknown"}.` : "Geology layer not available for this site."));
  }

  function thumb(p) {
    if (!p.thumb) return null;
    const themis = /THEMIS/i.test(p.thumb_source || "");
    return h("figure", { class: "mars-thumb" },
      h("img", { src: `${BASE}${p.thumb}`, alt: `${p.id} seen from orbit`, loading: "lazy", width: 512, height: 512 }),
      h("figcaption", { class: "muted small" }, p.thumb_source || "", themis ? h("div", {}, "Same STAC search we run over Iowa. Different planet.") : null));
  }

  function renderDrawer(id) {
    const site = doc.sites.find((s) => s.id === id);
    const cand = doc.spacex_candidates.find((c) => c.id === id);
    const p = site || cand;
    if (!p) return;
    const isCand = Boolean(cand);
    drawerBody.replaceChildren(
      h("div", { class: "row", style: { justifyContent: "space-between", alignItems: "center" } },
        h("div", {}, h("h2", { class: "panel-title" }, isCand ? cand.id : `${site.id} · rank ${site.rank}`),
          h("p", { class: "muted small", style: { margin: "2px 0 0" } }, isCand ? `${cand.name || "HiRISE candidate"} · ${cand.region}${cand.acquired ? ` · imaged ${cand.acquired}` : ""}` : `${coords(site)}${site.region ? ` · ${site.region}` : ""}`)),
        h("button", { class: "btn btn-ghost", type: "button", "aria-label": "Close", onclick: closeDrawer }, "×")),
      h("div", { class: "row row-wrap", style: { gap: "8px", margin: "10px 0" } }, statusChip(p.status, true),
        h("span", { class: "mono" }, `score ${p.score != null ? p.score.toFixed(3) : "n/a"}`),
        isCand && cand.percentile != null ? h("span", { class: "muted small" }, `better than ${fmtNum(cand.percentile)}% of scored cells`) : null,
        site?.nearest_spacex ? h("span", { class: "muted small" }, `${fmtNum(site.nearest_spacex.km)} km from ${site.nearest_spacex.id}`) : null),
      isCand ? h("p", { class: "small" }, coords(cand), " · ", h("a", { href: cand.url, target: "_blank", rel: "noopener" }, "HiRISE page")) : h("p", { class: "small" }, site.reason),
      thumb(p),
      h("div", { class: "section" }, h("div", { class: "section-title" }, "The four checks"), checksTable(p)),
      shownNotScored(p),
      geologyBlock(p),
      isCand && cand.best_within_200km ? h("div", { class: "section" }, h("div", { class: "section-title" }, "Best cell within 200 km"),
        h("div", { class: "row row-wrap", style: { gap: "8px", alignItems: "center" } }, statusChip(cand.best_within_200km.status),
          h("span", { class: "mono small" }, `score ${cand.best_within_200km.score.toFixed(3)}`),
          h("span", { class: "small" }, `${fmtNum(cand.best_within_200km.km)} km ${cand.best_within_200km.bearing}`),
          h("button", { class: "btn btn-primary", type: "button", onclick: () => map?.flyTo({ center: [cand.best_within_200km.lon, cand.best_within_200km.lat], zoom: 5.5 }) }, "Fly there"))) : null,
    );
    drawer.hidden = false;
  }

  function select(id) {
    selected = id;
    const p = doc.sites.find((s) => s.id === id) || doc.spacex_candidates.find((c) => c.id === id);
    if (!p || p.missing) return;
    renderDrawer(id);
    for (const row of dockBody.querySelectorAll(".mars-row")) row.classList.toggle("is-active", row.dataset.id === id);
    map?.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 3.2), duration: 900 });
  }

  function closeDrawer() {
    drawer.hidden = true;
    selected = null;
    for (const row of dockBody.querySelectorAll(".mars-row")) row.classList.remove("is-active");
  }

  function onKey(event) {
    if (!open) return;
    if (event.key === "Escape") closeDrawer();
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!list.length) return;
      const i = list.indexOf(selected);
      const next = i < 0 ? (event.key === "ArrowDown" ? 0 : list.length - 1) : (i + (event.key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      select(list[next]);
      event.preventDefault();
    }
  }

  return {
    async open() {
      open = true;
      host.hidden = false;
      document.body.classList.add("has-mars");
      document.addEventListener("keydown", onKey);
      dockBody.replaceChildren(h("div", { class: "empty" }, "Loading the Mars screen…"));
      const data = await loadSites();
      if (!open) return;
      if (!data) {
        emptyState();
        return;
      }
      if (!map) buildMap();
      else map.resize();
      renderDock();
    },
    close() {
      open = false;
      host.hidden = true;
      document.body.classList.remove("has-mars");
      document.removeEventListener("keydown", onKey);
      closeDrawer();
      if (window.location.hash === "#mars") window.history.replaceState(null, "", window.location.pathname + window.location.search);
    },
    isOpen: () => open,
  };
}
