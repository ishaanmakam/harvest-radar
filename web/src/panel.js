import { h, s } from "./dom.js";
import { ndviSparkline } from "./charts.js";
import {
  CHANNEL_LABEL,
  CHECK_LABEL,
  CROP_LABEL,
  SEVERITY,
  SOURCE_LABEL,
  STAGE_LABEL,
  chip,
  fmtDate,
  fmtHa,
  fmtNum,
  fmtPct,
  fmtWindow,
  titleCase,
} from "./format.js";

function closeIcon() {
  return s(
    "svg",
    { viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": "true" },
    s("path", { d: "M3 3l10 10M13 3L3 13", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round" }),
  );
}

function chevron(direction) {
  const d = direction === "prev" ? "M10 3 5 8l5 5" : "M6 3l5 5-5 5";
  return s(
    "svg",
    { viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": "true" },
    s("path", { d, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }),
  );
}

function weatherIcon() {
  return s(
    "svg",
    { viewBox: "0 0 16 16", "aria-hidden": "true" },
    s("path", {
      d: "M4.5 11.5a3 3 0 0 1-.4-6 4 4 0 0 1 7.7-.9 2.8 2.8 0 0 1 .7 5.5M6 13l-.8 1.6M9 13l-.8 1.6M12 13l-.8 1.6",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 1.4,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  );
}

function joinParts(parts) {
  return parts.filter((p) => p != null && p !== "").join(" / ");
}

function card(label, value, sub, { span = false } = {}) {
  if (value == null && !sub) return null;
  return h(
    "div",
    { class: `outlook-card${span ? " span-2" : ""}` },
    h("div", { class: "label" }, label),
    value != null ? h("div", { class: "value" }, value) : null,
    sub ? h("div", { class: "sub" }, sub) : null,
  );
}

function pctOf(value) {
  if (value == null) return null;
  return fmtPct(value <= 1 ? value * 100 : value);
}

/**
 * The Outlook block reads the per-field modules of analytics.json plus the field's soil and terrain
 * from field_env.json. Every module and key is optional.
 */
function outlookBlock(modules, env) {
  const m = modules && typeof modules === "object" ? modules : {};
  const cards = [];

  const y = m.yield;
  if (y && typeof y.bu_per_acre_calibrated === "number") {
    cards.push(
      card(
        "Calibrated yield",
        `${fmtNum(y.bu_per_acre_calibrated, 1)} bu/acre`,
        joinParts([
          Array.isArray(y.interval_bu_per_acre) ? `band ${fmtNum(y.interval_bu_per_acre[0])} to ${fmtNum(y.interval_bu_per_acre[1])}` : null,
          y.calibration ? `from the ${y.calibration}` : null,
          y.band ? `${y.band} for its zone` : null,
          y.index != null ? `relative index ${fmtNum(y.index, 2)}` : null,
          y.confidence != null ? `${typeof y.confidence === "number" ? fmtPct(y.confidence * 100) : y.confidence} confidence, from the yield estimate` : null,
        ]),
      ),
    );
  } else if (y && (y.band || y.index != null || y.estimate)) {
    cards.push(
      card(
        "Yield outlook",
        y.band ? `${titleCase(y.band)} for its zone` : null,
        joinParts([
          y.index != null ? `relative index ${fmtNum(y.index, 2)}` : null,
          y.estimate?.bu_per_acre != null ? `about ${fmtNum(y.estimate.bu_per_acre)} bu/acre (reference only)` : null,
          y.estimate?.t_per_ha != null ? `${fmtNum(y.estimate.t_per_ha, 1)} t/ha` : null,
          y.production?.bu != null ? `${fmtNum(y.production.bu)} bu on the field` : null,
          y.confidence != null ? `${typeof y.confidence === "number" ? fmtPct(y.confidence * 100) : y.confidence} confidence, from the yield estimate` : null,
        ]),
      ),
    );
  }

  const soil = y?.soil || env || null;
  const terrain = y?.terrain || env || null;
  const envNote = y?.env_note || env?.note || null;
  if (soil && (soil.texture || soil.soc_g_kg != null)) {
    cards.push(
      card(
        "Soil",
        soil.texture ? titleCase(soil.texture) : null,
        joinParts([
          soil.soc_g_kg != null ? `organic carbon ${fmtNum(soil.soc_g_kg, 1)} g/kg` : null,
          soil.clay_pct != null ? `clay ${fmtNum(soil.clay_pct)}%` : null,
          soil.sand_pct != null ? `sand ${fmtNum(soil.sand_pct)}%` : null,
          soil.ph != null ? `pH ${fmtNum(soil.ph, 1)}` : null,
          envNote,
        ]),
      ),
    );
  }
  if (terrain && (terrain.slope_pct != null || terrain.elevation_m != null)) {
    cards.push(
      card(
        "Terrain",
        terrain.slope_pct != null ? `${fmtNum(terrain.slope_pct, 1)}% slope` : null,
        joinParts([
          terrain.elevation_m != null ? `${fmtNum(terrain.elevation_m)} m elevation` : null,
          terrain.aspect ? `faces ${terrain.aspect}` : null,
        ]),
      ),
    );
  }

  const e = modules.economics;
  if (e && (e.recommendation || e.reason)) {
    const label = { harvest_now: "Harvest now", wait: "Wait", either: "Either timing" }[e.recommendation] || titleCase(e.recommendation);
    cards.push(
      card(
        "Harvest now or wait",
        e.recommendation ? label : null,
        joinParts([
          e.reason,
          e.savings_usd != null && !String(e.reason || "").includes("$") ? `about $${fmtNum(e.savings_usd)} at stake` : null,
          e.moisture_today_pct != null ? `moisture today ${fmtPct(e.moisture_today_pct, 1)}` : null,
        ]),
      ),
    );
  }

  const w = modules.workability;
  if (w && (w.p_finish_before_rain != null || w.words)) {
    cards.push(card("Finish before the rain", pctOf(w.p_finish_before_rain), w.words || null));
  }

  const water = modules.water;
  if (water && (water.water_status || water.depletion_today_mm != null)) {
    cards.push(
      card(
        "Soil water",
        water.water_status ? titleCase(water.water_status) : null,
        joinParts([
          water.depletion_today_mm != null ? `${fmtNum(water.depletion_today_mm)} mm depleted` : null,
          water.stress_days != null ? `${fmtNum(water.stress_days)} stress days` : null,
          water.days_since_rain != null ? `${fmtNum(water.days_since_rain)} days since rain` : null,
        ]),
      ),
    );
  }

  const ph = modules.phenology;
  if (ph && (ph.cohort || ph.green_up_date || ph.curve_harvest_date)) {
    cards.push(
      card(
        "Season curve",
        ph.cohort && ph.cohort !== "none" ? `${titleCase(ph.cohort)} cohort` : ph.stage ? STAGE_LABEL[ph.stage] || titleCase(ph.stage) : null,
        joinParts([
          ph.green_up_date ? `green-up ${fmtDate(ph.green_up_date)}` : null,
          ph.senescence_onset ? `senescence ${fmtDate(ph.senescence_onset)}` : null,
          ph.curve_harvest_date ? `curve harvest ${fmtDate(ph.curve_harvest_date)}` : null,
          ph.agreement ? `${typeof ph.agreement === "number" ? fmtNum(ph.agreement, 2) : titleCase(ph.agreement)} with the lag estimate` : null,
        ]),
      ),
    );
  }

  const a = modules.anomaly;
  if (a && (a.anomaly_type || a.note)) {
    cards.push(
      card(
        "Against its peers",
        a.anomaly_type ? (a.anomaly_type === "none" ? "In step" : titleCase(a.anomaly_type)) : null,
        joinParts([a.note, a.latest_z != null && !String(a.note || "").includes("z ") ? `z ${fmtNum(a.latest_z, 1)}` : null]),
        { span: true },
      ),
    );
  }

  const ev = modules.events;
  if (ev) {
    const scar = ev.scar_type ?? ev.burn?.scar_type;
    const floods = ev.flood_passes ?? ev.flood?.flood_passes;
    const fire = ev.fire_exposure ?? ev.fire?.fire_exposure;
    if (scar || (floods != null && floods > 0) || (fire && fire !== "none")) {
      cards.push(
        card(
          "Events",
          scar ? titleCase(scar) : null,
          joinParts([
            floods != null && floods > 0 ? `${fmtNum(floods)} flood passes` : null,
            fire && fire !== "none" ? `fire exposure ${typeof fire === "number" ? fmtNum(fire, 2) : titleCase(fire)}` : null,
          ]),
        ),
      );
    }
  }

  const c = modules.coverage;
  if (c && (c.staleness || c.clear_passes != null)) {
    cards.push(
      card(
        "Coverage",
        c.staleness ? titleCase(c.staleness) : null,
        c.note ||
          joinParts([
            c.clear_passes != null ? `${fmtNum(c.clear_passes)} clear passes` : null,
            c.days_since_last_clear != null ? `last clear look ${fmtNum(c.days_since_last_clear)} days ago` : null,
          ]),
      ),
    );
  }

  const filled = cards.filter(Boolean);
  if (!filled.length) return null;
  return h("div", { class: "section" }, h("div", { class: "section-title" }, "Outlook"), h("div", { class: "outlook" }, ...filled));
}

function sheetHandle(el) {
  return h(
    "button",
    {
      class: "sheet-handle",
      type: "button",
      "aria-label": "Expand panel",
      "aria-expanded": String(el.classList.contains("is-expanded")),
      onclick: (event) => {
        const expanded = el.classList.toggle("is-expanded");
        event.currentTarget.setAttribute("aria-expanded", String(expanded));
      },
    },
    h("span"),
  );
}

/** The farm plan: today line, outlook, NDVI season, the eight checks, risks and delivery status. */
export function renderPlan(el, ctx, farmId) {
  const { data } = ctx;
  const feature = data.featuresById.get(farmId);
  const plan = data.plansById.get(farmId);
  const props = feature?.properties || {};
  const status = data.statusById.get(farmId);
  const zone = data.zonesById.get(plan?.zone_id || props.zone_id);
  const position = ctx.position(farmId);

  const head = h(
    "div",
    { class: "drawer-head" },
    h(
      "div",
      { class: "titles" },
      h("div", { class: "row row-wrap" }, h("h2", { id: "drawer-title", tabindex: "-1" }, farmId), chip(plan?.flag ?? "none", undefined, { large: true })),
      h(
        "div",
        { class: "meta" },
        joinParts([
          zone?.name || plan?.zone_id || props.zone_id,
          CROP_LABEL[props.crop] || titleCase(props.crop),
          props.area_ha != null ? fmtHa(props.area_ha) : null,
          status?.stage ? STAGE_LABEL[status.stage] || titleCase(status.stage) : null,
        ]),
      ),
    ),
    h(
      "div",
      { class: "drawer-nav" },
      h("button", { class: "btn btn-icon", type: "button", "aria-label": "Previous farm", title: "Previous farm (arrow up)", onclick: () => ctx.step(-1) }, chevron("prev")),
      h("button", { class: "btn btn-icon", type: "button", "aria-label": "Next farm", title: "Next farm (arrow down)", onclick: () => ctx.step(1) }, chevron("next")),
      h("button", { class: "btn btn-icon", type: "button", "aria-label": "Close farm plan", title: "Close (Escape)", onclick: () => ctx.close() }, closeIcon()),
    ),
  );

  const body = h("div", { class: "drawer-body" });

  if (!plan) {
    body.append(h("div", { class: "empty" }, "No morning plan for this farm in today's file."));
  } else {
    body.append(
      h(
        "div",
        { class: "section" },
        h("p", { class: "today-line" }, plan.today),
        h(
          "p",
          { class: "muted small", style: { marginTop: "6px" } },
          joinParts([
            `Plan for ${fmtDate(plan.as_of, "long")}`,
            SOURCE_LABEL[plan.source] || titleCase(plan.source),
            position ? `Stop ${position.index + 1} on the route (${position.action})` : null,
          ]),
        ),
        plan.harvest_outlook?.summary ? h("div", { class: "weather-line" }, weatherIcon(), h("span", {}, plan.harvest_outlook.summary)) : null,
      ),
    );

    const outlook = outlookBlock(data.analytics?.fields?.[farmId], data.fieldEnv?.fields?.[farmId]);
    if (outlook) body.append(outlook);

    const series = data.timeseries?.fields?.[farmId];
    if (series?.ndvi && data.timeseries.dates) {
      body.append(
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, "NDVI this season"),
          ndviSparkline({ dates: data.timeseries.dates, ndvi: series.ndvi, clearFrac: series.clear_frac, status }),
          h("p", { class: "muted small", style: { marginTop: "4px" } }, "Hollow dots are hazy passes. Ticks on the baseline are cloudy passes with no reading."),
        ),
      );
    }

    const checks = [...(plan.checks || [])].sort((a, b) => SEVERITY[a.status] - SEVERITY[b.status]);
    body.append(
      h(
        "div",
        { class: "section" },
        h("div", { class: "section-title" }, "The eight checks"),
        h(
          "div",
          { class: "checks" },
          ...checks.map((check) =>
            h(
              "div",
              { class: `check check-${check.status}` },
              h("div", { class: "check-head" }, h("span", {}, CHECK_LABEL[check.kind] || titleCase(check.kind)), chip(check.status)),
              check.detail ? h("div", { class: "check-detail" }, check.detail) : null,
              check.where || fmtWindow(check.window)
                ? h("div", { class: "check-meta" }, joinParts([check.where, fmtWindow(check.window) ? `Window ${fmtWindow(check.window)}` : null]))
                : null,
            ),
          ),
        ),
      ),
    );

    if (plan.risks?.length) {
      body.append(
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, "Risks"),
          h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, ...plan.risks.map((risk) => h("li", {}, risk))),
        ),
      );
    }
  }

  const deliveries = data.deliveriesByFarm.get(farmId) || [];
  body.append(
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Delivery today"),
      deliveries.length
        ? h(
            "div",
            { class: "delivery-badges" },
            ...deliveries.map((d) => h("span", { class: "delivery-badge", title: d.detail || "" }, CHANNEL_LABEL[d.channel] || titleCase(d.channel), chip(d.status))),
          )
        : h("p", { class: "muted small" }, data.deliveries ? "Nothing queued for this farm today." : "No delivery log in this dataset."),
    ),
  );

  el.replaceChildren(sheetHandle(el), head, body);
}
