import { h } from "../dom.js";
import { fmtNum } from "../format.js";
import { API_BASE, API_BASE_PLACEHOLDER, TERMS_VERSION, acceptTerms, configured, issueApiKey, listKeys, revokeApiKey, termsStatus } from "../backend.js";
import { codeBlock, copyButton, errorNote, fmtDateTime, loading, notice, signInPrompt, statusLine } from "../ui.js";

// The endpoints of intelligence/accounts_api.py (served by the inbound server).
const ENDPOINTS = [
  ["GET /v1/health", "No key needed. as_of and the endpoint list."],
  ["GET /v1/plans", "Every farm's plan for the latest morning (the published plans.json)."],
  ["GET /v1/farms/{farm_id}/plan", "One farm's plan."],
  ["GET /v1/route", "The officer route."],
  ["GET /v1/landscape", "The county analytics block and the forecast source."],
  ["GET /v1/sources", "The data sources list."],
];
const ATTRIBUTION = "Overfield (ishaanmakam/harvest-radar). Imagery: Copernicus Sentinel-2. Weather: Open-Meteo. Yields: USDA NASS.";
const LICENCE = "Plans and analytics: CC BY 4.0. Underlying sources keep their own terms; see /v1/sources.";
const TERMS = [
  "Use the API for lawful purposes only.",
  "Attribute Overfield and the underlying sources wherever you publish what you got from it.",
  "Plans are estimates from satellite imagery and weather, not measurements, and must not be the sole basis for a safety decision.",
  "Keys are personal. Do not share them; Overfield may revoke a key at any time.",
  "Calls are logged: the path, the status and the time, against your key.",
  "No resale of the raw feed. Build on it; do not relay it as is for money.",
  "No warranty. The service may change or stop without notice.",
];

function docs() {
  const base = API_BASE || API_BASE_PLACEHOLDER;
  const envelope = JSON.stringify(
    { served_at: "2026-10-18T11:30:00.000Z", as_of: "2026-10-18", attribution: ATTRIBUTION, licence: LICENCE, data: { "fields": "the plan, route, county block or sources list" } },
    null,
    2,
  );
  return [
    h(
      "div",
      { class: "section" },
      h(
        "p",
        { class: "lede" },
        "The Overfield API is a key-gated, versioned front for the plan files this site publishes. The files themselves are open data, served beside the map. A key buys a stable, documented endpoint, usage counts, a daily limit and a recorded acceptance of the terms.",
      ),
      h(
        "p",
        { class: "small", style: { marginTop: "10px" } },
        "Base URL ",
        h("code", { class: "inline-code" }, base),
        API_BASE ? null : h("span", { class: "muted" }, " (shown as a placeholder until the backend is deployed)"),
      ),
      h("p", { class: "small muted", style: { marginTop: "4px" } }, "Send the key as ", h("code", { class: "inline-code" }, "Authorization: Bearer ovf_<key>"), " or as ", h("code", { class: "inline-code" }, "?key=ovf_<key>"), "."),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Endpoints"),
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Endpoint"), h("th", {}, "Returns"))),
          h("tbody", {}, ...ENDPOINTS.map(([path, what]) => h("tr", {}, h("td", { class: "mono" }, path), h("td", {}, what)))),
        ),
      ),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Example"),
      codeBlock(`curl -H "Authorization: Bearer ovf_<key>" ${base}/v1/farms/F0001/plan`),
      h("p", { class: "small muted", style: { margin: "8px 0 6px" } }, "Every response is the same envelope: when the data was served, which morning it describes, how to attribute it, and the licence."),
      codeBlock(envelope),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Limits"),
      h(
        "ul",
        { class: "small explainer-list" },
        h("li", {}, "401 when the key is missing, unknown or revoked."),
        h("li", {}, "429 past the key's daily limit: 1000 calls by default, counted over the last 24 hours."),
        h("li", {}, "404 for an unknown farm or endpoint; 503 while a file is not published yet."),
        h("li", {}, "GET only. Responses are cached for up to five minutes. CORS is open, so a browser can call it."),
        h("li", {}, "At most five live keys per holder."),
      ),
    ),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "Developer terms", h("span", { class: "count mono" }, TERMS_VERSION)),
      h("ol", { class: "small terms-list" }, ...TERMS.map((t) => h("li", {}, t))),
    ),
  ];
}

async function loadKeys() {
  const [terms, keys] = await Promise.all([termsStatus(), listKeys()]);
  const calls = new Map(keys.map((k) => [k.id, k.usage_7d || 0]));
  return { accepted: terms.accepted || null, keys, calls };
}

function keysTable(keys, calls, onRevoke) {
  if (!keys.length) return h("div", { class: "empty" }, "You have not created a key.");
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Prefix"), h("th", {}, "Created"), h("th", {}, "Last used"), h("th", {}, "Calls, 7 days"), h("th", {}, ""))),
      h(
        "tbody",
        {},
        ...keys.map((k) =>
          h(
            "tr",
            { class: k.revoked_at ? "is-revoked" : null },
            h("td", {}, k.name),
            h("td", { class: "mono" }, k.key_prefix),
            h("td", { class: "muted" }, fmtDateTime(k.created_at)),
            h("td", { class: "muted" }, k.last_used_at ? fmtDateTime(k.last_used_at) : "never"),
            h("td", { class: "num" }, fmtNum(calls.get(k.id) || 0)),
            h(
              "td",
              {},
              k.revoked_at
                ? h("span", { class: "muted small" }, `revoked ${fmtDateTime(k.revoked_at)}`)
                : h("button", { class: "btn btn-link", type: "button", onclick: () => onRevoke(k) }, "Revoke key"),
            ),
          ),
        ),
      ),
    ),
  );
}

function keysSection(ctx, el) {
  const { auth } = ctx;
  const section = h("div", { class: "section" }, h("div", { class: "section-title" }, "Your keys"));
  const body = h("div", {});
  section.append(body);
  let alive = true;
  let newKey = null;

  if (!configured) {
    body.append(notice("Keys need the Overfield server, and this copy of the site does not know where it is; the docs above still describe the API. Setup steps: backend/README.md."));
    return { section, destroy() {} };
  }
  if (!auth.ready) {
    body.append(loading(2));
    return { section, destroy() {} };
  }
  if (!auth.user) {
    body.append(h("p", { class: "small muted" }, "Keys are issued by the Overfield team for now."));
    return { section, destroy() {} };
  }

  const status = statusLine();

  async function draw() {
    body.replaceChildren(loading(2));
    let state;
    try {
      state = await loadKeys();
    } catch (err) {
      if (alive) body.replaceChildren(errorNote(err, "Could not read your keys"));
      return;
    }
    if (!alive) return;

    if (!state.accepted) {
      body.replaceChildren(
        h("div", { class: "card" },
          h("p", { class: "small" }, `Creating a key records that you accepted the developer terms above (version ${TERMS_VERSION}), with the time and your browser's user agent.`),
          h("div", { class: "row row-wrap", style: { marginTop: "10px" } },
            h("button", {
              class: "btn btn-primary",
              type: "button",
              onclick: async (event) => {
                event.currentTarget.disabled = true;
                status.set("Recording your acceptance.");
                try {
                  await acceptTerms();
                  status.set("");
                  await draw();
                } catch (err) {
                  status.set(err.message, "warn");
                  event.currentTarget.disabled = false;
                }
              },
            }, "Accept the developer terms"),
          ),
          status.el,
        ),
      );
      return;
    }

    const nameInput = h("input", { id: "dev-key-name", class: "input", type: "text", maxlength: "60", placeholder: "What this key is for (for example my-dashboard)" });
    const createButton = h("button", { class: "btn btn-primary", type: "submit" }, "Create key");
    const createForm = h(
      "form",
      {
        class: "row row-wrap create-row",
        onsubmit: async (event) => {
          event.preventDefault();
          createButton.disabled = true;
          status.set("Creating the key.");
          try {
            newKey = await issueApiKey(nameInput.value);
            status.set("");
            await draw();
          } catch (err) {
            status.set(err.message, "warn");
            createButton.disabled = false;
          }
        },
      },
      nameInput,
      createButton,
    );

    const shown = newKey;
    newKey = null;
    body.replaceChildren(
      h("p", { class: "small muted" }, `You accepted the developer terms (${TERMS_VERSION}) on ${fmtDateTime(state.accepted)}.`),
      shown
        ? h(
            "div",
            { class: "key-box" },
            h("div", { class: "section-title" }, "Your new key"),
            h("div", { class: "row row-wrap" }, h("code", { class: "key-value" }, shown), copyButton(() => shown, "Copy the key")),
            h("p", { class: "small", style: { marginTop: "6px" } }, h("strong", {}, "Copy it now. "), "It will not be shown again; only its prefix stays visible here."),
          )
        : null,
      h("div", { class: "card", style: { marginTop: "10px" } }, h("div", { class: "section-title" }, "New key"), createForm, status.el),
      h("div", { style: { marginTop: "12px" } },
        keysTable(state.keys, state.calls, async (k) => {
          status.set(`Revoking ${k.key_prefix}.`);
          try {
            await revokeApiKey(k.id);
            status.set("");
            await draw();
          } catch (err) {
            status.set(err.message, "warn");
          }
        }),
      ),
    );
  }

  draw();
  return {
    section,
    destroy() {
      alive = false;
    },
  };
}

export function renderDeveloper(el, ctx) {
  const keys = keysSection(ctx, el);
  el.replaceChildren(...docs(), keys.section);
  return { destroy: keys.destroy };
}
