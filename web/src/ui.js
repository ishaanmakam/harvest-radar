// Small building blocks shared by the account, developer, admin and data pages.
import { h, skeleton } from "./dom.js";

export function notice(text, kind = "info") {
  return h("div", { class: `note note-${kind}` }, text);
}

export function errorNote(err, prefix) {
  const message = err?.message || String(err);
  return notice(prefix ? `${prefix}: ${message}` : message, "watch");
}

export function card(title, ...children) {
  return h("div", { class: "card" }, title ? h("div", { class: "section-title" }, title) : null, ...children);
}

export function stat(label, value, sub) {
  return h(
    "div",
    { class: "outlook-card" },
    h("div", { class: "label" }, label),
    h("div", { class: "value" }, value == null || value === "" ? "0" : String(value)),
    sub ? h("div", { class: "sub" }, sub) : null,
  );
}

export function loading(lines = 3) {
  return h("div", { "aria-busy": "true" }, skeleton("40%", 22), ...Array.from({ length: lines }, (_, i) => skeleton(`${92 - i * 14}%`, 14)));
}

/** A live status line for forms: set(text, kind) with kind "" | "ok" | "warn". */
export function statusLine() {
  const el = h("p", { class: "status-line small", role: "status", "aria-live": "polite" });
  return {
    el,
    set(text, kind = "") {
      el.textContent = text || "";
      el.className = `status-line small${kind ? ` status-${kind}` : ""}`;
    },
  };
}

export function fmtDateTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function fmtDay(iso) {
  if (!iso) return "";
  const date = new Date(`${String(iso).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export function copyButton(getText, label = "Copy") {
  const button = h("button", { class: "btn", type: "button" }, label);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    window.setTimeout(() => {
      button.textContent = label;
    }, 1600);
  });
  return button;
}

export function codeBlock(text) {
  return h("pre", { class: "code" }, h("code", {}, text));
}

export function signInPrompt(ctx, what) {
  return h(
    "div",
    { class: "card" },
    h("p", {}, `Sign in to ${what}.`),
    h("p", { class: "muted small", style: { margin: "4px 0 10px" } }, "No password is needed: a link in your email signs you in."),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => ctx.go("account") }, "Sign in"),
  );
}

export function field(label, control, hint) {
  return h("div", { class: "field" }, h("label", { for: control.id || null }, label), control, hint ? h("span", { class: "hint" }, hint) : null);
}

export function select(id, options, value, attrs = {}) {
  return h(
    "select",
    { id, class: "select", ...attrs },
    ...options.map(([v, label]) => h("option", { value: v, selected: String(v) === String(value) ? true : null }, label)),
  );
}

export function csvCell(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function downloadText(filename, text, type = "text/csv;charset=utf-8") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function maskPhone(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (!digits) return "";
  return `•••• ${digits.slice(-4)}`;
}

export const isStaff = (profile) => profile?.role === "officer" || profile?.role === "admin";
export const isAdmin = (profile) => profile?.role === "admin";
