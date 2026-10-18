import { h, s } from "./dom.js";
import { fmtDate, fmtNum, parseDate } from "./format.js";

function timeScale(dates, x0, x1) {
  const times = dates.map((d) => parseDate(d)?.getTime()).filter((t) => t != null);
  const min = Math.min(...times);
  const max = Math.max(...times);
  const span = max - min || 1;
  return (iso) => {
    const t = parseDate(iso)?.getTime();
    if (t == null) return null;
    return x0 + ((t - min) / span) * (x1 - x0);
  };
}

function monthTicks(dates) {
  const seen = new Set();
  const ticks = [];
  for (const iso of dates) {
    const key = iso.slice(0, 7);
    if (seen.has(key)) continue;
    seen.add(key);
    ticks.push(`${key}-01`);
  }
  return ticks;
}

function pathFromPoints(points) {
  let d = "";
  let pen = false;
  for (const point of points) {
    if (!point) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${point[0].toFixed(1)} ${point[1].toFixed(1)}`;
    pen = true;
  }
  return d;
}

/**
 * NDVI through the season for one field. Null passes become gaps and get a mark on the baseline;
 * the peak is dotted; the harvest date or the predicted window is drawn when status knows it.
 */
export function ndviSparkline({ dates, ndvi, clearFrac, status }) {
  const W = 360;
  const H = 130;
  const left = 30;
  const right = 8;
  const top = 18;
  const bottom = 28;
  // The date axis also covers the harvest date or predicted window, which can sit after the last pass.
  const extraDates = [status?.harvest_date, status?.predicted_harvest_start, status?.predicted_harvest_end].filter((d) => parseDate(d));
  const x = timeScale([...dates, ...extraDates], left, W - right);
  const y = (v) => top + (1 - Math.max(0, Math.min(1, v))) * (H - top - bottom);
  const baseline = H - bottom;

  const svg = s("svg", { class: "chart chart-ndvi", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "NDVI through the season" });

  for (const level of [0, 0.5, 1]) {
    svg.append(s("line", { class: "grid", x1: left, x2: W - right, y1: y(level), y2: y(level) }));
    svg.append(s("text", { class: "tick-label", x: left - 4, y: y(level) + 3, "text-anchor": "end" }, level.toFixed(1)));
  }
  for (const tick of monthTicks(dates)) {
    const tx = x(tick);
    if (tx == null || tx < left || tx > W - right) continue;
    svg.append(s("text", { class: "tick-label", x: tx, y: H - 10, "text-anchor": "middle" }, fmtDate(tick, "month")));
  }

  const harvestDate = status?.harvest_date;
  const predictedStart = status?.predicted_harvest_start;
  const predictedEnd = status?.predicted_harvest_end;
  if (!harvestDate && (predictedStart || predictedEnd)) {
    const xs = x(predictedStart || predictedEnd);
    const xe = x(predictedEnd || predictedStart);
    if (xs != null && xe != null) {
      const a = Math.max(left, Math.min(xs, xe, W - right - 2));
      const b = Math.min(W - right, Math.max(xs, xe, a + 2));
      if (b > a) {
        svg.append(s("rect", { class: "window-band", x: a, y: top, width: b - a, height: baseline - top }));
        svg.append(s("text", { class: "label", x: Math.min(a, W - right - 80), y: top - 6 }, "predicted harvest"));
      }
    }
  }

  const points = dates.map((iso, i) => {
    const v = ndvi[i];
    if (v == null) return null;
    const px = x(iso);
    return px == null ? null : [px, y(v)];
  });
  svg.append(s("path", { class: "line-ndvi", d: pathFromPoints(points) }));

  dates.forEach((iso, i) => {
    const px = x(iso);
    if (px == null) return;
    if (ndvi[i] == null) {
      svg.append(s("line", { class: "gap-mark", x1: px, x2: px, y1: baseline - 3, y2: baseline + 3 }));
      return;
    }
    const cloudy = clearFrac && clearFrac[i] != null && clearFrac[i] < 0.6;
    svg.append(s("circle", { class: cloudy ? "dot-cloudy" : "dot", cx: px, cy: y(ndvi[i]), r: cloudy ? 2.6 : 2 }));
  });

  let peakIndex = -1;
  if (status?.peak_date) peakIndex = dates.indexOf(status.peak_date);
  if (peakIndex < 0) {
    let best = -Infinity;
    ndvi.forEach((v, i) => {
      if (v != null && v > best) {
        best = v;
        peakIndex = i;
      }
    });
  }
  if (peakIndex >= 0 && ndvi[peakIndex] != null) {
    const px = x(dates[peakIndex]);
    const py = y(ndvi[peakIndex]);
    svg.append(s("circle", { class: "peak", cx: px, cy: py, r: 4 }));
    const anchor = px > W - 70 ? "end" : "start";
    svg.append(s("text", { class: "label", x: px + (anchor === "end" ? -7 : 7), y: py - 6, "text-anchor": anchor }, `peak ${ndvi[peakIndex].toFixed(2)}`));
  }

  if (harvestDate) {
    const hx = x(harvestDate);
    if (hx != null) {
      svg.append(s("line", { class: "harvest-line", x1: hx, x2: hx, y1: top, y2: baseline }));
      const anchor = hx > W - 70 ? "end" : "start";
      svg.append(s("text", { class: "label", x: hx + (anchor === "end" ? -5 : 5), y: baseline - 4, "text-anchor": anchor }, `harvested ${fmtDate(harvestDate)}`));
    }
  }

  svg.append(s("line", { class: "axis", x1: left, x2: W - right, y1: baseline, y2: baseline }));
  return svg;
}

/**
 * County yield hindcast: measured against predicted by year, with the model's band around the
 * prediction, and this season's estimate at the end.
 */
export function hindcastChart({ rows, rmse, target, targetYear }) {
  const W = 620;
  const H = 210;
  const left = 44;
  const right = 12;
  const top = 16;
  const bottom = 28;
  const years = rows.map((r) => r.year);
  const labels = [...years, targetYear || "now"];
  const halfBand = rmse != null ? 1.64 * rmse : 0;
  const values = rows.flatMap((r) => [r.actual, r.predicted - halfBand, r.predicted + halfBand]).filter((v) => typeof v === "number");
  if (target != null) values.push(target - halfBand, target + halfBand);
  const min = Math.floor(Math.min(...values) / 10) * 10;
  const max = Math.ceil(Math.max(...values) / 10) * 10;
  const slot = (W - left - right) / labels.length;
  const x = (i) => left + slot * (i + 0.5);
  const y = (v) => top + (1 - (v - min) / (max - min || 1)) * (H - top - bottom);
  const baseline = H - bottom;

  const svg = s("svg", { class: "chart chart-hindcast", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "County yield, measured against predicted, by year" });
  const ticks = 4;
  for (let i = 0; i <= ticks; i += 1) {
    const level = min + ((max - min) * i) / ticks;
    svg.append(s("line", { class: "grid", x1: left, x2: W - right, y1: y(level), y2: y(level) }));
    svg.append(s("text", { class: "tick-label", x: left - 5, y: y(level) + 3, "text-anchor": "end" }, fmtNum(level)));
  }
  labels.forEach((label, i) => svg.append(s("text", { class: "tick-label", x: x(i), y: H - 10, "text-anchor": "middle" }, String(label))));

  if (halfBand > 0) {
    const upper = rows.map((r, i) => [x(i), y(r.predicted + halfBand)]);
    const lower = rows.map((r, i) => [x(i), y(r.predicted - halfBand)]).reverse();
    svg.append(s("path", { class: "band-model", d: `${pathFromPoints(upper)}${pathFromPoints(lower).replace(/^M/, "L")}Z` }));
  }
  svg.append(s("path", { class: "line-predicted", d: pathFromPoints(rows.map((r, i) => [x(i), y(r.predicted)])) }));
  svg.append(s("path", { class: "line-actual", d: pathFromPoints(rows.map((r, i) => [x(i), y(r.actual)])) }));
  rows.forEach((r, i) => svg.append(s("circle", { class: "dot-actual", cx: x(i), cy: y(r.actual), r: 3 })));

  if (target != null) {
    const i = rows.length;
    if (halfBand > 0) {
      svg.append(s("rect", { class: "band-model", x: x(i) - slot * 0.25, y: y(target + halfBand), width: slot * 0.5, height: Math.max(0, y(target - halfBand) - y(target + halfBand)), rx: 3 }));
    }
    svg.append(s("circle", { class: "peak", cx: x(i), cy: y(target), r: 4 }));
    svg.append(s("text", { class: "label", x: x(i), y: y(target + halfBand) - 6, "text-anchor": "middle" }, `${fmtNum(target)} est.`));
  }
  svg.append(s("line", { class: "axis", x1: left, x2: W - right, y1: baseline, y2: baseline }));

  const legend = h(
    "div",
    { class: "chart-legend" },
    h("span", {}, h("span", { class: "key key-actual" }), "Measured county yield"),
    h("span", {}, h("span", { class: "key key-projected" }), "Predicted, that year held out"),
    halfBand > 0 ? h("span", {}, h("span", { class: "key key-band" }), "90 percent band") : null,
    target != null ? h("span", {}, h("span", { class: "key key-peak" }), "This season's estimate") : null,
  );
  return h("div", {}, svg, legend);
}

/**
 * Harvest progress through the season: an area for the observed share, an optional comparison
 * series (USDA), optional projected milestones and a marker for today.
 */
export function progressChart({ series, comparison, projected, today, comparisonLabel = "USDA" }) {
  const W = 620;
  const H = 220;
  const left = 36;
  const right = 12;
  const top = 16;
  const bottom = 30;
  const allDates = [
    ...series.map((p) => p.date),
    ...(comparison || []).map((p) => p.date),
    ...(projected || []).map((p) => p.date),
    today,
  ].filter(Boolean);
  const x = timeScale(allDates, left, W - right);
  const y = (v) => top + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - top - bottom);
  const baseline = H - bottom;

  const svg = s("svg", { class: "chart chart-progress", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Harvest progress" });
  for (const level of [0, 25, 50, 75, 100]) {
    svg.append(s("line", { class: "grid", x1: left, x2: W - right, y1: y(level), y2: y(level) }));
    svg.append(s("text", { class: "tick-label", x: left - 5, y: y(level) + 3, "text-anchor": "end" }, `${level}%`));
  }
  for (const tick of monthTicks([...allDates].sort())) {
    const tx = x(tick);
    if (tx == null || tx < left || tx > W - right) continue;
    svg.append(s("text", { class: "tick-label", x: tx, y: H - 10, "text-anchor": "middle" }, fmtDate(tick, "month")));
  }

  const pts = series.map((p) => [x(p.date), y(p.pct)]).filter((p) => p[0] != null);
  if (pts.length) {
    const area = `${pathFromPoints(pts)}L${pts[pts.length - 1][0].toFixed(1)} ${baseline}L${pts[0][0].toFixed(1)} ${baseline}Z`;
    svg.append(s("path", { class: "area-progress", d: area }));
    svg.append(s("path", { class: "line-progress", d: pathFromPoints(pts) }));
  }

  if (projected?.length && pts.length) {
    const last = pts[pts.length - 1];
    const projPts = [last, ...projected.map((p) => [x(p.date), y(p.pct)]).filter((p) => p[0] != null)];
    svg.append(s("path", { class: "line-projected", d: pathFromPoints(projPts) }));
    for (const p of projected) {
      const px = x(p.date);
      if (px == null) continue;
      svg.append(s("line", { class: "milestone", x1: px, x2: px, y1: y(p.pct), y2: baseline }));
      svg.append(s("circle", { class: "peak", cx: px, cy: y(p.pct), r: 3.5 }));
      svg.append(s("text", { class: "label", x: px, y: y(p.pct) - 8, "text-anchor": "middle" }, `${p.label} ${fmtDate(p.date)}`));
    }
  }

  if (comparison?.length) {
    const cpts = comparison.map((p) => [x(p.date), y(p.pct)]).filter((p) => p[0] != null);
    svg.append(s("path", { class: "line-usda", d: pathFromPoints(cpts) }));
  }

  if (today) {
    const tx = x(today);
    if (tx != null) {
      svg.append(s("line", { class: "today-line", x1: tx, x2: tx, y1: top, y2: baseline }));
      svg.append(s("text", { class: "label", x: tx + 4, y: top + 10 }, "today"));
    }
  }
  svg.append(s("line", { class: "axis", x1: left, x2: W - right, y1: baseline, y2: baseline }));

  const legend = h(
    "div",
    { class: "chart-legend" },
    h("span", {}, h("span", { class: "key" }), "Satellite"),
    comparison?.length ? h("span", {}, h("span", { class: "key key-usda" }), comparisonLabel) : null,
    projected?.length ? h("span", {}, h("span", { class: "key key-projected" }), "Projected") : null,
    today ? h("span", {}, h("span", { class: "key key-today" }), "Today") : null,
  );
  return h("div", {}, svg, legend);
}
