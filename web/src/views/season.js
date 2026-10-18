import { h, s } from "../dom.js";
import { progressChart } from "../charts.js";
import { CHECK_LABEL, CROP_LABEL, fmtDate, fmtHa, fmtNum, fmtPct, titleCase } from "../format.js";

function isDateString(value) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value);
}

function stat(label, value, sub) {
  return h(
    "div",
    { class: "outlook-card" },
    h("div", { class: "label" }, label),
    h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null,
  );
}

/** Harvest progress from status.json alone, for datasets without the analytics layer. */
function satelliteProgress(data) {
  const status = data.status?.fields;
  if (!status?.length) return null;
  const known = status.filter((f) => f.stage && f.stage !== "unknown");
  const dates = [...new Set(known.map((f) => f.harvest_date).filter(isDateString))].sort();
  if (!dates.length) return null;
  const first = data.timeseries?.dates?.[0];
  const series = [];
  if (first && first < dates[0]) series.push({ date: first, pct: 0 });
  let count = 0;
  for (const date of dates) {
    count += known.filter((f) => f.harvest_date === date).length;
    series.push({ date, pct: (100 * count) / known.length });
  }
  return { series, harvested: count, total: known.length };
}

function cropCurve(entry) {
  if (!entry || !Array.isArray(entry.curve)) return null;
  const series = entry.curve
    .map((point) => (Array.isArray(point) ? { date: point[0], pct: point[1] } : { date: point?.date, pct: point?.pct ?? point?.pct_harvested }))
    .filter((p) => isDateString(p.date) && typeof p.pct === "number");
  return series.length ? series : null;
}

function cropMilestones(entry) {
  const list = [];
  for (const pct of [50, 75, 90]) {
    const crossed = entry[`crossed_${pct}_pct`];
    const projected = entry[`projected_${pct}_pct`];
    const date = isDateString(crossed) ? crossed : isDateString(projected) ? projected : null;
    if (date) list.push({ label: `${pct}%`, pct, date, crossed: isDateString(crossed) });
  }
  return list;
}

function demandChart(demand, today) {
  const days = Array.isArray(demand?.days) ? demand.days.filter((d) => isDateString(d.date) && typeof d.machine_days === "number") : [];
  if (!days.length) return null;
  const capacity = typeof demand.capacity_machines === "number" ? demand.capacity_machines : null;
  const W = 620;
  const H = 150;
  const left = 36;
  const right = 12;
  const top = 14;
  const bottom = 28;
  const max = Math.max(capacity || 0, ...days.map((d) => d.machine_days)) * 1.15 || 1;
  const slot = (W - left - right) / days.length;
  const y = (v) => top + (1 - v / max) * (H - top - bottom);
  const svg = s("svg", { class: "chart chart-demand", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Machine-days of combining needed per day" });
  for (const level of [0, Math.round(max / 2), Math.round(max)]) {
    svg.append(s("line", { class: "grid", x1: left, x2: W - right, y1: y(level), y2: y(level) }));
    svg.append(s("text", { class: "tick-label", x: left - 5, y: y(level) + 3, "text-anchor": "end" }, String(level)));
  }
  days.forEach((d, i) => {
    const x = left + i * slot + slot * 0.15;
    const width = slot * 0.7;
    svg.append(
      s("rect", {
        class: d.workable === false ? "bar-wet" : "bar",
        x,
        y: y(d.machine_days),
        width,
        height: Math.max(0, y(0) - y(d.machine_days)),
        rx: 2,
      }),
    );
    if (i % 2 === 0) svg.append(s("text", { class: "tick-label", x: x + width / 2, y: H - 10, "text-anchor": "middle" }, fmtDate(d.date)));
  });
  if (capacity != null) {
    svg.append(s("line", { class: "harvest-line", x1: left, x2: W - right, y1: y(capacity), y2: y(capacity) }));
    svg.append(s("text", { class: "label", x: W - right, y: y(capacity) - 4, "text-anchor": "end" }, `${capacity} machines`));
  }
  if (today) {
    const i = days.findIndex((d) => d.date === today);
    if (i >= 0) svg.append(s("text", { class: "label", x: left + i * slot + slot / 2, y: top - 2, "text-anchor": "middle" }, "today"));
  }
  svg.append(s("line", { class: "axis", x1: left, x2: W - right, y1: y(0), y2: y(0) }));
  const legend = h(
    "div",
    { class: "chart-legend" },
    h("span", {}, h("span", { class: "key key-bar" }), "Machine-days on a workable day"),
    h("span", {}, h("span", { class: "key key-wet" }), "Day the fields are too wet"),
    capacity != null ? h("span", {}, h("span", { class: "key key-cap" }), "Capacity") : null,
  );
  return h("div", {}, svg, legend);
}

function zoneTable(data) {
  const zones = data.analytics?.zones;
  if (!zones || typeof zones !== "object") return null;
  const rows = Object.entries(zones)
    .map(([zoneId, modules]) => ({ zoneId, land: modules?.landscape }))
    .filter((r) => r.land && typeof r.land === "object")
    .sort((a, b) => a.zoneId.localeCompare(b.zoneId));
  if (!rows.length) return null;
  const cell = (value, cls) => h("td", { class: cls }, value);
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h("tr", {}, h("th", {}, "Zone"), h("th", {}, "Farms"), h("th", {}, "Area"), h("th", {}, "Harvested"), h("th", {}, "Ready"), h("th", {}, "Act now"), h("th", {}, "Watch"), h("th", {}, "Fine"), h("th", {}, "Top reason")),
      ),
      h(
        "tbody",
        {},
        ...rows.map(({ zoneId, land }) =>
          h(
            "tr",
            {},
            cell(land.name || data.zonesById.get(zoneId)?.name || zoneId),
            cell(land.fields != null ? fmtNum(land.fields) : "", "num"),
            cell(land.ha != null ? fmtHa(land.ha) : "", "num"),
            cell(land.pct_harvested_area != null ? fmtPct(land.pct_harvested_area) : "", "num"),
            cell(land.pct_ready_area != null ? fmtPct(land.pct_ready_area) : "", "num"),
            cell(land.act_now != null ? fmtNum(land.act_now) : "", "num"),
            cell(land.watch != null ? fmtNum(land.watch) : "", "num"),
            cell(land.fine != null ? fmtNum(land.fine) : "", "num"),
            cell(land.top_kind ? CHECK_LABEL[land.top_kind] || titleCase(land.top_kind) : ""),
          ),
        ),
      ),
    ),
  );
}

function usdaSection(usda) {
  if (!usda || typeof usda !== "object") return null;
  const crops = usda.crops && typeof usda.crops === "object" ? Object.entries(usda.crops) : [];
  const rows = crops.filter(([, c]) => c && (c.usda_pct_harvested != null || c.ours_pct_harvested != null));
  if (!rows.length && !usda.note) return null;
  const standIn = usda.source && usda.source !== "nass_quickstats";
  return h(
    "div",
    { class: "section" },
    h("div", { class: "section-title" }, "Against USDA crop progress"),
    standIn ? h("div", { class: "note note-watch", style: { marginBottom: "8px" } }, "Stand-in USDA rows shaped like a QuickStats answer, not published figures.") : null,
    rows.length
      ? h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "table" },
            h("thead", {}, h("tr", {}, h("th", {}, "Crop"), h("th", {}, "USDA week"), h("th", {}, "USDA"), h("th", {}, "Overfield"), h("th", {}, "Difference"))),
            h(
              "tbody",
              {},
              ...rows.map(([crop, c]) =>
                h(
                  "tr",
                  {},
                  h("td", {}, CROP_LABEL[crop] || titleCase(crop)),
                  h("td", {}, c.week_ending ? `${fmtDate(c.week_ending)}${c.year ? ` ${c.year}` : ""}` : ""),
                  h("td", { class: "num" }, c.usda_pct_harvested != null ? fmtPct(c.usda_pct_harvested) : ""),
                  h("td", { class: "num" }, c.ours_pct_harvested != null ? fmtPct(c.ours_pct_harvested) : ""),
                  h("td", { class: "num" }, c.diff_pp != null ? `${c.diff_pp > 0 ? "+" : ""}${fmtNum(c.diff_pp, 1)} pp` : ""),
                ),
              ),
            ),
          ),
        )
      : null,
    usda.note ? h("p", { class: "muted small", style: { marginTop: "6px" } }, usda.note) : null,
  );
}

export function renderSeason(el, ctx) {
  const { data } = ctx;
  const analytics = data.analytics;
  const today = analytics?.as_of || data.plans?.as_of;

  if (!analytics) {
    const sat = satelliteProgress(data);
    el.replaceChildren(
      h("div", { class: "section" }, h("h2", { class: "panel-title" }, "The season so far")),
      h(
        "div",
        { class: "note note-info" },
        "The analytics layer has not run on this dataset, so there is no yield, economics or USDA comparison yet. It writes analytics.json beside plans.json.",
      ),
      ...(sat
        ? [
            h(
              "div",
              { class: "section" },
              h("div", { class: "section-title" }, "Harvest progress from the satellite"),
              h("p", { class: "muted small", style: { marginBottom: "8px" } }, `${sat.harvested} of ${sat.total} fields with a crop signal look harvested, by the date their greenness collapsed.`),
              progressChart({ series: sat.series, today }),
            ),
          ]
        : []),
    );
    return { update() {} };
  }

  const modules = analytics.landscape && typeof analytics.landscape === "object" ? analytics.landscape : {};
  const land = modules.landscape && typeof modules.landscape === "object" ? modules.landscape : {};
  const sections = [
    h(
      "div",
      { class: "section" },
      h("h2", { class: "panel-title" }, "The season so far"),
      h("p", { class: "muted small" }, `Analytics as of ${fmtDate(analytics.as_of || today, "long")}${land.latest_pass ? ` · latest pass ${fmtDate(land.latest_pass)}` : ""}.`),
    ),
  ];

  if (typeof land.narrative === "string") sections.push(h("div", { class: "section" }, h("p", { class: "narrative" }, land.narrative)));

  const tiles = [];
  if (land.fields != null) tiles.push(stat("Farms", fmtNum(land.fields), land.ha_total != null ? fmtHa(land.ha_total) : null));
  if (land.pct_harvested_area != null) tiles.push(stat("Harvested", fmtPct(land.pct_harvested_area), land.harvested_ha != null ? `${fmtHa(land.harvested_ha)} of area` : null));
  if (land.standing_ha != null) {
    const stages = land.standing_by_stage && typeof land.standing_by_stage === "object" ? Object.entries(land.standing_by_stage).map(([k, v]) => `${titleCase(k).toLowerCase()} ${fmtHa(v)}`).join(", ") : null;
    tiles.push(stat("Still standing", fmtHa(land.standing_ha), stages));
  }
  if (land.flags) tiles.push(stat("Today", `${fmtNum(land.flags.act_now ?? 0)} act now`, `${fmtNum(land.flags.watch ?? 0)} watch · ${fmtNum(land.flags.fine ?? 0)} fine`));
  if (tiles.length) sections.push(h("div", { class: "section" }, h("div", { class: "milestones" }, ...tiles)));

  const progress = land.harvest_progress && typeof land.harvest_progress === "object" ? land.harvest_progress : null;
  const crops = progress ? Object.entries(progress).filter(([, entry]) => cropCurve(entry)) : [];
  if (crops.length) {
    for (const [crop, entry] of crops) {
      const milestones = cropMilestones(entry);
      sections.push(
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, `${CROP_LABEL[crop] || titleCase(crop)} harvest progress`, entry.pct_harvested_today != null ? h("span", { class: "count" }, `${fmtPct(entry.pct_harvested_today)} today`) : null),
          milestones.length
            ? h(
                "div",
                { class: "milestones", style: { marginBottom: "10px" } },
                ...milestones.map((m) => stat(`${m.label} harvested`, fmtDate(m.date), m.crossed ? "crossed" : "projected")),
              )
            : null,
          progressChart({ series: cropCurve(entry), projected: milestones.filter((m) => !m.crossed), today }),
          h(
            "p",
            { class: "muted small", style: { marginTop: "6px" } },
            [
              entry.recent_rate_ha_per_day != null ? `Recent pace ${fmtHa(entry.recent_rate_ha_per_day)} a day.` : null,
              entry.fit_r2 != null ? `Fit r² ${fmtNum(entry.fit_r2, 2)}.` : null,
              entry.fit_caveat ? `${titleCase(entry.fit_caveat)}.` : null,
            ]
              .filter(Boolean)
              .join(" "),
          ),
        ),
      );
    }
  } else {
    const sat = satelliteProgress(data);
    if (sat) {
      sections.push(
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, "Harvest progress from the satellite"),
          progressChart({ series: sat.series, today }),
        ),
      );
    }
  }

  const usda = usdaSection(land.usda_comparison || land.usda);
  if (usda) sections.push(usda);

  const zones = zoneTable(data);
  if (zones) sections.push(h("div", { class: "section" }, h("div", { class: "section-title" }, "By zone"), zones));

  const demand = demandChart(land.combine_demand, today);
  if (demand) {
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "Combining the next two weeks"),
        h("p", { class: "muted small", style: { marginBottom: "6px" } }, "Machine-days of combining the standing crop asks for each day, against the simulated depot capacity."),
        demand,
      ),
    );
  }

  const extras = [];
  if (typeof modules.water?.narrative === "string") extras.push(["Soil water", modules.water.narrative]);
  if (modules.economics?.savings_usd_total != null) {
    const counts = modules.economics.counts || {};
    extras.push([
      "Harvest timing",
      `${fmtNum(modules.economics.fields_evaluated ?? 0)} standing fields priced: ${fmtNum(counts.harvest_now ?? 0)} harvest now, ${fmtNum(counts.wait ?? 0)} wait, ${fmtNum(counts.either ?? 0)} either. Following the recommendation saves about $${fmtNum(modules.economics.savings_usd_total)} in drying and shrink.`,
    ]);
  }
  if (modules.anomaly?.n_flagged != null) {
    const counts = modules.anomaly.counts || {};
    extras.push([
      "Fields out of step",
      `${fmtNum(modules.anomaly.n_flagged)} of ${fmtNum(modules.anomaly.n_compared ?? modules.anomaly.n_fields ?? 0)} fields differ from their cohort${counts.sudden_drop != null ? `: ${fmtNum(counts.sudden_drop)} sudden drops, ${fmtNum(counts.early_senescence ?? 0)} early senescence, ${fmtNum(counts.chronic_low ?? 0)} chronically low` : ""}.`,
    ]);
  }
  if (modules.events && (modules.events.burned_fields != null || modules.events.flooded_fields != null)) {
    extras.push([
      "Burn, flood and fire",
      `${fmtNum(modules.events.burned_fields ?? 0)} burn scars, ${fmtNum(modules.events.flooded_fields ?? 0)} flooded fields, ${fmtNum(modules.events.recent_hotspots ?? 0)} recent hotspots.`,
    ]);
  }
  if (modules.coverage?.clear_rate_season != null) {
    extras.push([
      "Coverage",
      `${fmtNum(modules.coverage.passes_total ?? 0)} passes this season, ${fmtPct(modules.coverage.clear_rate_season * 100)} of field looks clear; latest clear look ${modules.coverage.latest_clear_date ? fmtDate(modules.coverage.latest_clear_date) : "unknown"}.`,
    ]);
  }
  if (extras.length) {
    sections.push(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "Also in the analytics"),
        h("dl", { class: "kv" }, ...extras.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      ),
    );
  }

  el.replaceChildren(...sections);
  return { update() {} };
}
