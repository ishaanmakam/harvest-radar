import { h } from "../dom.js";
import { fmtNum, titleCase } from "../format.js";
import { fmtDay, loading } from "../ui.js";

const OPEN_LICENCE = /\b(cc[ -]?by|cc0|public domain|odbl|open|free|mit|apache|no restrictions|unrestricted)\b/i;
const EMPTY = "The sources list is being written.";

const KIND_LABEL = {
  imagery: "Satellite imagery",
  weather: "Weather",
  fire: "Fire",
  reference: "Reference data",
  soil: "Soil and terrain",
  yield: "Yields and crop statistics",
  fields: "Field boundaries",
  basemap: "Base maps",
  model: "Estimates",
  terrain: "Terrain",
  geocoding: "Geocoding",
  vision: "Image reading",
  delivery: "Texts and messages",
  voice: "Voice calls",
  email: "Email",
  llm: "Translation and questions",
  validation: "Validation against official numbers",
  hosting: "Hosting",
  other: "The globe",
  mars: "Mars",
};

async function loadSources() {
  try {
    const res = await fetch(new URL("data/sources.json", document.baseURI), { cache: "no-cache" });
    if (!res.ok) return null;
    const type = res.headers.get("content-type") || "";
    if (type.includes("html")) return null;
    const text = await res.text();
    if (!/^\s*[{[]/.test(text)) return null;
    const doc = JSON.parse(text);
    return Array.isArray(doc?.sources) && doc.sources.length ? doc : null;
  } catch {
    return null;
  }
}

function yesNo(value) {
  if (value === true || value === "yes") return "yes";
  if (value === false || value === "no" || value == null || value === "") return "no";
  return String(value);
}

function sourceTable(rows) {
  const td = (content, cls) => h("td", { class: cls || null }, content);
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Source"),
          h("th", {}, "Provider"),
          h("th", {}, "Use"),
          h("th", {}, "Cadence"),
          h("th", {}, "Resolution"),
          h("th", {}, "Licence"),
          h("th", {}, "Key needed"),
          h("th", {}, "Live or cached"),
        ),
      ),
      h(
        "tbody",
        {},
        ...rows.map((src) =>
          h(
            "tr",
            {},
            td(src.url ? h("a", { href: src.url, target: "_blank", rel: "noopener" }, src.name || src.url) : src.name || ""),
            td(src.provider || ""),
            td([
              src.used_for || "",
              src.why ? h("div", { class: "muted small", style: { marginTop: "4px" } }, src.why) : null,
              Array.isArray(src.where_in_code) && src.where_in_code.length
                ? h(
                    "details",
                    { class: "small", style: { marginTop: "4px" } },
                    h("summary", { class: "muted" }, `In the code: ${src.where_in_code.length} ${src.where_in_code.length === 1 ? "place" : "places"}`),
                    h("div", { class: "mono muted", style: { marginTop: "3px" } }, src.where_in_code.join(", ")),
                  )
                : null,
            ]),
            td(src.cadence || ""),
            td(src.resolution || ""),
            td(src.licence || ""),
            td(yesNo(src.key_required)),
            td(src.live_or_offline || ""),
          ),
        ),
      ),
    ),
  );
}

export function renderData(el) {
  let alive = true;
  el.replaceChildren(loading(4));

  loadSources().then((doc) => {
    if (!alive) return;
    if (!doc) {
      el.replaceChildren(h("p", { class: "muted" }, EMPTY));
      return;
    }
    const sources = doc.sources;
    const byKind = new Map();
    for (const src of sources) {
      const kind = String(src.kind || "other").toLowerCase();
      if (!byKind.has(kind)) byKind.set(kind, []);
      byKind.get(kind).push(src);
    }
    const total = sources.length;
    const open = sources.filter((s) => OPEN_LICENCE.test(String(s.licence || ""))).length;
    const keyed = sources.filter((s) => s.key_required === true || s.key_required === "yes").length;

    el.replaceChildren(
      h(
        "div",
        { class: "section" },
        h(
          "p",
          { class: "lede" },
          "Every check in a morning plan names the source it came from. The tables below list each source Overfield reads, what it feeds, how often it updates, its resolution and its licence. Imagery says what a field looks like today, and weather says what is coming. Fire and flood feeds say what happened overnight. Reference data says what is normal for this place and this crop. Every check in a morning plan traces back to the sources below.",
        ),
        h(
          "p",
          { class: "muted small", style: { marginTop: "8px" } },
          `Counts: ${fmtNum(total)} sources, ${fmtNum(open)} free and open (by licence), ${fmtNum(keyed)} need a key.`,
          doc.generated ? ` List generated ${fmtDay(doc.generated)}.` : "",
        ),
      ),
      ...[...byKind].map(([kind, rows]) =>
        h(
          "div",
          { class: "section" },
          h("div", { class: "section-title" }, KIND_LABEL[kind] || titleCase(kind), h("span", { class: "count" }, String(rows.length))),
          sourceTable(rows),
        ),
      ),
    );
  });

  return {
    destroy() {
      alive = false;
    },
  };
}
