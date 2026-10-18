import { h } from "../dom.js";
import { hindcastChart } from "../charts.js";
import { YIELD_COLORS } from "../map.js";
import { CROP_LABEL, fmtDate, fmtHa, fmtNum, titleCase } from "../format.js";

function stat(label, value, sub) {
  return h(
    "div",
    { class: "outlook-card" },
    h("div", { class: "label" }, label),
    h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null,
  );
}

function band(interval) {
  if (!Array.isArray(interval) || interval.length < 2) return "";
  return `${fmtNum(interval[0])} to ${fmtNum(interval[1])}`;
}

function countyCard(crop, model) {
  const years = Array.isArray(model.years) ? model.years : [];
  const parts = [];
  if (model.rmse_bu_per_acre != null) parts.push(`held-out error about ${fmtNum(model.rmse_bu_per_acre, 1)} bu/acre`);
  if (model.r2 != null) parts.push(`R² ${fmtNum(model.r2, 2)}`);
  if (model.n_train != null) parts.push(`${fmtNum(model.n_train)} county-seasons`);
  if (years.length) parts.push(`${years.length} years (${years[0]} to ${years[years.length - 1]})`);
  return h(
    "div",
    { class: "outlook-card county-card" },
    h("div", { class: "label" }, `${CROP_LABEL[crop] || titleCase(crop)} · ${model.county || "county"}`),
    h("div", { class: "value value-xl" }, model.estimate_bu_per_acre != null ? `${fmtNum(model.estimate_bu_per_acre)} bu/acre` : "no estimate"),
    model.interval_bu_per_acre ? h("div", { class: "sub" }, `90 percent band ${band(model.interval_bu_per_acre)} bu/acre`) : null,
    parts.length ? h("div", { class: "sub" }, parts.join(" · ")) : null,
  );
}

function legendRow(color, text, count) {
  return h(
    "div",
    { class: "legend-row legend-static" },
    h("span", { class: "swatch", style: { background: color } }),
    h("span", {}, text),
    count != null ? h("span", { class: "count" }, String(count)) : null,
  );
}

function yieldLegend(data) {
  const crops = Object.entries(data.yieldBins || {});
  if (!crops.length) return h("p", { class: "muted small" }, "No calibrated field values in this dataset, so the map keeps today's flags.");
  const unscored = data.fields.features.filter((f) => f.properties.yield_bin < 0).length;
  return h(
    "div",
    {},
    ...crops.map(([crop, bins]) =>
      h(
        "div",
        { style: { marginBottom: "8px" } },
        h("div", { class: "small", style: { fontWeight: 600, marginBottom: "2px" } }, `${CROP_LABEL[crop] || titleCase(crop)}`, h("span", { class: "muted" }, ` · ${bins.count} fields`)),
        h(
          "div",
          { class: "ramp" },
          ...YIELD_COLORS.map((color, i) =>
            h("div", { class: "ramp-step", title: `${fmtNum(bins.edges[i])} to ${fmtNum(bins.edges[i + 1])} bu/acre` }, h("span", { class: "ramp-swatch", style: { background: color } }), h("span", { class: "ramp-label" }, fmtNum(bins.edges[i]))),
          ),
          h("div", { class: "ramp-step ramp-end" }, h("span", { class: "ramp-label" }, fmtNum(bins.edges[YIELD_COLORS.length]))),
        ),
      ),
    ),
    unscored ? legendRow("#6b7378", "Not scored (crop unknown or no clear season)", unscored) : null,
  );
}

function totalsTable(data) {
  const ly = data.analytics?.landscape?.yield;
  const crops = ly?.crops && typeof ly.crops === "object" ? Object.entries(ly.crops) : [];
  if (!crops.length) return null;
  const cal = ly.calibration && typeof ly.calibration === "object" ? ly.calibration : {};
  const td = (v, cls) => h("td", { class: cls }, v);
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Crop"), h("th", {}, "Fields"), h("th", {}, "Area"), h("th", {}, "Reference mean"), h("th", {}, "Calibrated mean"), h("th", {}, "County model"), h("th", {}, "Production"))),
      h(
        "tbody",
        {},
        ...crops.map(([crop, c]) =>
          h(
            "tr",
            {},
            td(CROP_LABEL[crop] || titleCase(crop)),
            td(c.fields != null ? fmtNum(c.fields) : "", "num"),
            td(c.ha != null ? fmtHa(c.ha) : "", "num"),
            td(c.mean_bu_per_acre != null ? `${fmtNum(c.mean_bu_per_acre, 1)} bu/ac` : "", "num"),
            td(c.calibrated_mean_bu_per_acre != null ? `${fmtNum(c.calibrated_mean_bu_per_acre, 1)} bu/ac` : "", "num"),
            td(cal[crop]?.county_estimate_bu_per_acre != null ? `${fmtNum(cal[crop].county_estimate_bu_per_acre, 1)} bu/ac` : "", "num"),
            td(c.production_bu != null ? `${fmtNum(c.production_bu)} bu` : "", "num"),
          ),
        ),
      ),
    ),
  );
}

export function renderYield(el, ctx) {
  const { data, mapApi } = ctx;
  const model = data.yieldModel;
  const analytics = data.analytics;
  const asOf = model?.as_of || analytics?.as_of || data.plans?.as_of;
  const crops = model?.crops && typeof model.crops === "object" ? Object.entries(model.crops) : [];
  const sections = [
    h(
      "div",
      { class: "section" },
      h("h2", { class: "panel-title" }, "Crop yield"),
      h("p", { class: "muted small" }, `As of ${fmtDate(asOf, "long")}. An estimate from weather and the satellite season, not a measurement.`),
    ),
  ];

  if (!model && !analytics) {
    sections.push(h("div", { class: "note note-info" }, "The yield layer has not run on this dataset: no county model and no calibrated field values yet."));
    el.replaceChildren(...sections);
    return { update() {} };
  }

  if (crops.length) {
    const method = crops.find(([, m]) => typeof m.method === "string")?.[1].method;
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "County estimate this season"),
        h("div", { class: "milestones" }, ...crops.map(([crop, m]) => countyCard(crop, m))),
        method ? h("p", { class: "muted small", style: { marginTop: "8px" } }, method) : null,
        Array.isArray(model.sources) && model.sources.length ? h("p", { class: "muted small", style: { marginTop: "4px" } }, `Sources: ${model.sources.join("; ")}.`) : null,
      ),
    );
    for (const [crop, m] of crops) {
      if (!Array.isArray(m.hindcast) || !m.hindcast.length) continue;
      sections.push(
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, `${CROP_LABEL[crop] || titleCase(crop)} hindcast`, h("span", { class: "count" }, "leave-one-year-out")),
          hindcastChart({ rows: m.hindcast, rmse: m.rmse_bu_per_acre, target: m.estimate_bu_per_acre, targetYear: m.target?.year }),
        ),
      );
    }
  } else {
    sections.push(
      h("div", { class: "note note-info" }, "yield_model.json is not in this dataset, so there is no county model: the field values below are reference-only indices."),
    );
  }

  sections.push(
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Calibrated yield on the map"),
      h("p", { class: "muted small", style: { marginBottom: "6px" } }, "Each farm is coloured by its calibrated bushels an acre: the satellite season index scaled to the county estimate. Click a farm for its band, soil and terrain."),
      yieldLegend(data),
    ),
  );

  const totals = totalsTable(data);
  if (totals) {
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "Landscape totals"),
        totals,
        Array.isArray(analytics?.landscape?.yield?.notes) && analytics.landscape.yield.notes.length
          ? h("p", { class: "muted small", style: { marginTop: "6px" } }, analytics.landscape.yield.notes.join(" · "))
          : null,
      ),
    );
  }

  if (data.ftw?.features?.length) {
    const toggle = h(
      "button",
      {
        class: "btn",
        type: "button",
        "aria-pressed": String(mapApi.ftwOn()),
        onclick: (event) => {
          const on = mapApi.setFtw(!mapApi.ftwOn());
          event.currentTarget.setAttribute("aria-pressed", String(on));
          event.currentTarget.textContent = on ? "Hide Fields of The World outlines" : "Show Fields of The World outlines";
        },
      },
      mapApi.ftwOn() ? "Hide Fields of The World outlines" : "Show Fields of The World outlines",
    );
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "Open field map"),
        h("div", { class: "row row-wrap" }, h("span", { class: "swatch swatch-ftw" }), toggle),
        h(
          "p",
          { class: "muted small", style: { marginTop: "6px" } },
          `Fields of The World is the open global field map; its ${fmtNum(data.ftw.features.length)} polygons for this box are drawn for comparison and are not the farms in the roster.`,
        ),
      ),
    );
  }

  if (data.fieldEnv?.landscape) {
    const land = data.fieldEnv.landscape;
    const textures = land.textures && typeof land.textures === "object" ? Object.entries(land.textures).map(([k, v]) => `${k} ${fmtNum(v)}`).join(", ") : null;
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "Soil and terrain across the landscape"),
        h(
          "div",
          { class: "milestones" },
          land.median_soc_g_kg != null ? stat("Organic carbon", `${fmtNum(land.median_soc_g_kg, 1)} g/kg`, "median, 0 to 30 cm") : null,
          land.median_clay_pct != null ? stat("Clay", `${fmtNum(land.median_clay_pct)}%`, land.median_sand_pct != null ? `sand ${fmtNum(land.median_sand_pct)}%` : null) : null,
          land.median_slope_pct != null ? stat("Slope", `${fmtNum(land.median_slope_pct, 1)}%`, land.median_elevation_m != null ? `${fmtNum(land.median_elevation_m)} m elevation` : null) : null,
        ),
        textures ? h("p", { class: "muted small", style: { marginTop: "6px" } }, `Textures: ${textures}.`) : null,
        Array.isArray(data.fieldEnv.sources) ? h("p", { class: "muted small", style: { marginTop: "4px" } }, `Sources: ${data.fieldEnv.sources.join("; ")}.`) : null,
      ),
    );
  }

  el.replaceChildren(...sections);
  return { update() {} };
}
