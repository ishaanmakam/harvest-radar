import { h, s } from "./dom.js";

export const FLAGS = ["act_now", "watch", "fine"];

export const FLAG_LABEL = {
  act_now: "Act now",
  watch: "Watch",
  fine: "Fine",
  none: "No plan",
  clear: "Clear",
};

export const SEVERITY = { act_now: 0, watch: 1, fine: 2, clear: 2, none: 3 };

export const CHECK_LABEL = {
  harvest_ready: "Harvest readiness",
  harvest_before_rain: "Harvest before rain",
  establishment: "Establishment",
  burn_scar: "Burn scar",
  fire: "Fire",
  flood: "Flood",
  stress: "Stress",
  visible_change: "Visible change",
};

export const CHECK_KINDS = Object.keys(CHECK_LABEL);

export const CROP_LABEL = {
  corn: "Corn",
  soybean: "Soybeans",
  other: "Other crop",
  unknown: "Unknown crop",
};

export const CHANNEL_LABEL = {
  grok_voice: "Voice call",
  photon_sms: "Text",
  email: "Email",
};

export const ROLE_LABEL = { farmer: "Farmer", officer: "Officer" };

export const SOURCE_LABEL = {
  vegetation_index: "Vegetation index",
  perceptron: "Perceptron read",
};

export const STAGE_LABEL = {
  growing: "Growing",
  maturing: "Maturing",
  ready: "Ready",
  harvested: "Harvested",
  unknown: "Unknown",
};

export function titleCase(value) {
  if (value == null) return "";
  const text = String(value).replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function parseDate(iso) {
  if (!iso || typeof iso !== "string") return null;
  const date = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtDate(iso, style = "short") {
  const date = parseDate(iso);
  if (!date) return iso || "";
  const options =
    style === "long"
      ? { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }
      : style === "month"
        ? { month: "short", timeZone: "UTC" }
        : { month: "short", day: "numeric", timeZone: "UTC" };
  return date.toLocaleDateString("en-US", options);
}

export function fmtWindow(window) {
  if (!window || (!window.start && !window.end)) return "";
  if (window.start && window.end && window.start !== window.end) {
    return `${fmtDate(window.start)} to ${fmtDate(window.end)}`;
  }
  return fmtDate(window.start || window.end);
}

export function fmtNum(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return "";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtPct(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return "";
  return `${fmtNum(value, digits)}%`;
}

export function fmtHa(value) {
  if (value == null) return "";
  return `${fmtNum(value, 1)} ha`;
}

export function fmtKm(value) {
  if (value == null) return "";
  return `${fmtNum(value, 1)} km`;
}

export function fmtHours(value) {
  if (value == null) return "";
  const hours = Number(value);
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  return `${fmtNum(hours, 1)} h`;
}

function iconPath(kind) {
  switch (kind) {
    case "act_now":
    case "failed":
    case "visit":
      return s("path", { d: "M6 1.3 11.2 10.6H.8Z", fill: "currentColor" });
    case "watch":
    case "call":
    case "pending":
      return [
        s("circle", { cx: 6, cy: 6, r: 4.4, fill: "none", stroke: "currentColor", "stroke-width": 1.6 }),
        s("circle", { cx: 6, cy: 6, r: 1.7, fill: "currentColor" }),
      ];
    case "fine":
    case "clear":
    case "sent":
      return s("path", {
        d: "M2.3 6.4 4.9 9l4.8-6",
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 1.9,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      });
    default:
      return s("path", { d: "M2.5 6h7", stroke: "currentColor", "stroke-width": 1.9, "stroke-linecap": "round" });
  }
}

export function icon(kind) {
  return s("svg", { viewBox: "0 0 12 12", "aria-hidden": "true" }, iconPath(kind));
}

/** A status chip: colour plus icon plus label, so the state never rests on colour alone. */
export function chip(status, label, { large = false } = {}) {
  const key = status || "none";
  const text = label ?? FLAG_LABEL[key] ?? titleCase(key);
  return h(
    "span",
    { class: `chip chip-${key}${large ? " chip-lg" : ""}` },
    icon(key),
    h("span", { class: "chip-label" }, text),
  );
}

export function flagColor(flag) {
  switch (flag) {
    case "act_now":
      return "var(--act)";
    case "watch":
      return "var(--watch)";
    case "fine":
      return "var(--fine)";
    default:
      return "var(--none)";
  }
}

export const reduceMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export const isPhone = () => typeof matchMedia === "function" && matchMedia("(max-width: 767px)").matches;
