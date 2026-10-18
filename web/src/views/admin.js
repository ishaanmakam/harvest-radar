import { h } from "../dom.js";
import { CHANNEL_LABEL, ROLE_LABEL, chip, fmtNum, titleCase } from "../format.js";
import { NOT_CONFIGURED, adminAccounts, adminDeliveries, adminKeys, adminOverview, adminUpdate, configured } from "../backend.js";
import { csvCell, downloadText, errorNote, fmtDateTime, fmtDay, isAdmin, isStaff, loading, maskPhone, notice, select, signInPrompt, stat, statusLine } from "../ui.js";

const ROLES = ["farmer", "officer", "admin"];
// The ROSTER_PATH shape: tools.ROSTER_COLUMNS plus signup.OPTIONAL_COLUMNS.
const CSV_COLUMNS = ["roster_id", "farm_id", "name", "phone", "language", "role", "email", "send_time", "timezone", "address", "lon", "lat", "created"];
const DAY_MS = 864e5;

function lastDays(n) {
  const today = Date.now();
  return Array.from({ length: n }, (_, i) => new Date(today - (n - 1 - i) * DAY_MS).toISOString().slice(0, 10));
}

async function loadAll() {
  const [overview, profiles, deliveries, keys] = await Promise.all([adminOverview(), adminAccounts(), adminDeliveries(), adminKeys()]);
  const dates = (deliveries.runs || []).map((r) => r.run_date);
  // usage rows in the shape keysSection counts: one entry per call day with its count
  const usage = keys.flatMap((k) => Object.entries(k.by_day || {}).map(([day, n]) => ({ key_id: k.id, day, n })));
  return { profiles, signups: overview.signups_per_day || [], dates, keys, usage, overview };
}

function loadRun(runDate) {
  return adminDeliveries(runDate).then((d) => d.rows || []);
}

// ---------------------------------------------------------------- CSV (mirrors intelligence/accounts.Accounts.roster_rows)
function normalizePhone(phone) {
  const text = String(phone || "");
  const digits = text.replace(/\D/g, "");
  if (!digits) return "";
  return text.trim().startsWith("+") || digits.length > 10 ? `+${digits}` : digits;
}

function hhmm(value) {
  const text = String(value || "").trim();
  return text.length >= 5 && text[2] === ":" ? text.slice(0, 5) : "";
}

function num(value) {
  return value == null || value === "" ? "" : Number(value).toFixed(5);
}

function created(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : `${date.toISOString().slice(0, 16)}Z`;
}

function queueReady(p) {
  const phone = normalizePhone(p.phone);
  const email = String(p.email || "").trim().toLowerCase();
  const role = String(p.role || "farmer").trim() || "farmer";
  const farm = String(p.farm_id || "").trim();
  if (p.paused) return false;
  if (!phone && !email) return false;
  if (role === "farmer" && !farm) return false;
  return true;
}

export function rosterCsv(profiles, onlyReady) {
  const lines = [CSV_COLUMNS.join(",")];
  for (const p of profiles) {
    if (onlyReady && !queueReady(p)) continue;
    const record = {
      roster_id: String(p.roster_id || "").trim(),
      farm_id: String(p.farm_id || "").trim(),
      name: String(p.name || "").trim(),
      phone: normalizePhone(p.phone),
      language: (String(p.language || "en").trim().toLowerCase() || "en").slice(0, 2),
      role: String(p.role || "farmer").trim() || "farmer",
      email: String(p.email || "").trim().toLowerCase(),
      send_time: hhmm(p.send_time),
      timezone: String(p.timezone || "").trim(),
      address: String(p.address || "").trim(),
      lon: num(p.lon),
      lat: num(p.lat),
      created: created(p.created_at),
    };
    lines.push(CSV_COLUMNS.map((c) => csvCell(record[c])).join(","));
  }
  return `${lines.join("\r\n")}\r\n`;
}

// ---------------------------------------------------------------- sections
function countCards(profiles, signups) {
  const cutoff = lastDays(7)[0];
  const count = (fn) => profiles.filter(fn).length;
  const signups7 = signups.filter((s) => String(s.day).slice(0, 10) >= cutoff).reduce((sum, s) => sum + Number(s.signups || 0), 0);
  return h(
    "div",
    { class: "milestones" },
    stat("People", fmtNum(profiles.length)),
    stat("Farmers", fmtNum(count((p) => p.role === "farmer"))),
    stat("Officers", fmtNum(count((p) => p.role === "officer")), `${fmtNum(count((p) => p.role === "admin"))} admins`),
    stat("Consented", fmtNum(count((p) => p.sms_consent_at)), "text and voice consent on file"),
    stat("Paused", fmtNum(count((p) => p.paused))),
    stat("Sign-ups, 7 days", fmtNum(signups7)),
  );
}

function rosterSection(ctx, profiles) {
  const admin = isAdmin(ctx.auth.profile);
  const me = ctx.auth.user?.id;
  const status = statusLine();
  const filters = { query: "", role: "all", paused: "all" };
  const tbody = h("tbody", {});
  const countEl = h("span", { class: "count" });

  async function update(p, patch, revert) {
    status.set(`Saving ${p.roster_id || p.name}.`);
    try {
      const row = await adminUpdate(p.id, patch);
      Object.assign(p, row || patch);
      status.set(`Saved ${p.roster_id || p.name}.`, "ok");
    } catch (err) {
      revert();
      status.set(err.message, "warn");
    }
  }

  function roleCell(p) {
    if (!admin) return titleCase(p.role);
    const own = p.id === me;
    return select(`role-${p.id}`, ROLES.map((r) => [r, titleCase(r)]), p.role, {
      class: "select select-sm",
      "aria-label": `Role for ${p.name || p.roster_id}`,
      disabled: own ? true : null,
      title: own ? "Your own role changes in the SQL editor, not here" : null,
      onchange: (e) => {
        const before = p.role;
        update(p, { role: e.target.value }, () => (e.target.value = before));
      },
    });
  }

  function pausedCell(p) {
    if (!admin) return p.paused ? "paused" : "";
    return h("input", {
      type: "checkbox",
      checked: p.paused ? true : null,
      "aria-label": `Paused for ${p.name || p.roster_id}`,
      onchange: (e) => {
        const before = p.paused;
        update(p, { paused: e.target.checked }, () => (e.target.checked = before));
      },
    });
  }

  function visible() {
    const q = filters.query.trim().toLowerCase();
    return profiles.filter((p) => {
      if (filters.role !== "all" && p.role !== filters.role) return false;
      if (filters.paused === "paused" && !p.paused) return false;
      if (filters.paused === "active" && p.paused) return false;
      if (!q) return true;
      const digits = String(p.phone || "").replace(/\D/g, "");
      return [p.name, p.roster_id, p.farm_id, p.email, p.address, digits.slice(-4)].some((v) => String(v || "").toLowerCase().includes(q));
    });
  }

  function draw() {
    const list = visible();
    countEl.textContent = `${fmtNum(list.length)} of ${fmtNum(profiles.length)}`;
    tbody.replaceChildren(
      ...list.map((p) =>
        h(
          "tr",
          {},
          h("td", {}, p.name || h("span", { class: "muted" }, "no name")),
          h("td", { class: "mono" }, p.roster_id || ""),
          h("td", { class: "mono" }, p.farm_id || ""),
          h("td", { class: "mono" }, maskPhone(p.phone)),
          h("td", {}, p.language || ""),
          h("td", {}, (Array.isArray(p.channels) ? p.channels : []).join(", ")),
          h("td", { class: "mono" }, hhmm(p.send_time)),
          h("td", {}, p.timezone || ""),
          h("td", {}, p.sms_consent_at ? "yes" : ""),
          h("td", {}, pausedCell(p)),
          h("td", {}, roleCell(p)),
        ),
      ),
    );
    if (!list.length) tbody.append(h("tr", {}, h("td", { colspan: "11", class: "muted" }, "No one matches.")));
  }

  const search = h("input", { class: "input", type: "search", placeholder: "Search name, id, farm, email, last 4 digits", "aria-label": "Search the roster", oninput: (e) => {
    filters.query = e.target.value;
    draw();
  } });
  const roleFilter = select("adm-role", [["all", "All roles"], ...ROLES.map((r) => [r, titleCase(r)])], "all", { class: "select select-auto", "aria-label": "Role", onchange: (e) => {
    filters.role = e.target.value;
    draw();
  } });
  const pausedFilter = select("adm-paused", [["all", "Active and paused"], ["active", "Active"], ["paused", "Paused"]], "all", { class: "select select-auto", "aria-label": "Paused", onchange: (e) => {
    filters.paused = e.target.value;
    draw();
  } });
  draw();

  return h(
    "div",
    { class: "section" },
    h("div", { class: "section-title" }, "Roster", countEl),
    h("div", { class: "toolbar" }, search, roleFilter, pausedFilter),
    h(
      "div",
      { class: "table-wrap table-tall" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", {}, "Name"),
            h("th", {}, "Roster id"),
            h("th", {}, "Farm"),
            h("th", {}, "Phone"),
            h("th", {}, "Language"),
            h("th", {}, "Channels"),
            h("th", {}, "Send time"),
            h("th", {}, "Timezone"),
            h("th", {}, "Consent"),
            h("th", {}, "Paused"),
            h("th", {}, "Role"),
          ),
        ),
        tbody,
      ),
    ),
    h("p", { class: "muted small", style: { marginTop: "6px" } }, admin ? "Role and paused save as you change them. Nothing on this page sends a message; sends stay with the queue, dry-run first." : "Officers read the roster; admins change roles and pauses."),
    status.el,
  );
}

function deliveriesSection(dates) {
  const body = h("div", {});
  const section = h("div", { class: "section" }, h("div", { class: "section-title" }, "Deliveries"));
  if (!dates.length) {
    section.append(h("div", { class: "empty" }, "No morning runs have been logged yet. The queue writes here after each run (dry runs included) when ACCOUNTS_DB is set."));
    return section;
  }
  let alive = true;
  const picker = select("adm-run", dates.map((d) => [d, fmtDay(d)]), dates[0], { class: "select select-auto", "aria-label": "Morning run", onchange: (e) => show(e.target.value) });

  async function show(runDate) {
    body.replaceChildren(loading(2));
    let list;
    try {
      list = await loadRun(runDate);
    } catch (err) {
      if (alive) body.replaceChildren(errorNote(err, "Could not read that run"));
      return;
    }
    if (!alive) return;
    const byChannel = new Map();
    const byStatus = new Map();
    for (const row of list) {
      const channel = row.channel || "unknown";
      if (!byChannel.has(channel)) byChannel.set(channel, new Map());
      const c = byChannel.get(channel);
      c.set(row.status, (c.get(row.status) || 0) + 1);
      byStatus.set(row.status, (byStatus.get(row.status) || 0) + 1);
    }
    const dry = list.filter((r) => r.dry_run).length;
    body.replaceChildren(
      h("p", { class: "muted small" }, `${fmtNum(list.length)} rows for ${fmtDay(runDate)}${dry ? `, ${fmtNum(dry)} from a dry run` : ""}.`),
      h("div", { class: "counts", style: { marginTop: "8px" } }, ...[...byStatus].map(([s, n]) => chip(s, `${fmtNum(n)} ${s}`, { large: true }))),
      h(
        "div",
        { class: "channel-counts" },
        ...[...byChannel].map(([channel, c]) =>
          h(
            "div",
            { class: "outlook-card" },
            h("div", { class: "label" }, CHANNEL_LABEL[channel] || titleCase(channel)),
            h("div", { class: "value" }, fmtNum([...c.values()].reduce((a, b) => a + b, 0))),
            h("div", { class: "sub" }, [...c].map(([s, n]) => `${n} ${s}`).join(" / ")),
          ),
        ),
      ),
      h(
        "div",
        { class: "table-wrap table-tall", style: { marginTop: "10px" } },
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Roster id"), h("th", {}, "Farm"), h("th", {}, "Role"), h("th", {}, "Channel"), h("th", {}, "Status"), h("th", {}, "Detail"), h("th", {}, "Sent"))),
          h(
            "tbody",
            {},
            ...list.map((row) =>
              h(
                "tr",
                {},
                h("td", { class: "mono" }, row.roster_id || ""),
                h("td", { class: "mono" }, row.farm_id || ""),
                h("td", {}, ROLE_LABEL[row.role] || titleCase(row.role)),
                h("td", {}, CHANNEL_LABEL[row.channel] || titleCase(row.channel)),
                h("td", {}, chip(row.status)),
                h("td", { class: "muted" }, row.detail || ""),
                h("td", { class: "muted" }, fmtDateTime(row.sent_at)),
              ),
            ),
          ),
        ),
      ),
    );
  }

  section.append(h("div", { class: "toolbar" }, h("label", { class: "small muted", for: "adm-run" }, "Morning run"), picker), body);
  show(dates[0]);
  section.destroy = () => {
    alive = false;
  };
  return section;
}

function barList(items) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return h(
    "div",
    { class: "bars" },
    ...items.map((i) =>
      h(
        "div",
        { class: "bar-row" },
        h("span", { class: "bar-label mono"}, i.label),
        h("span", { class: "bar-track" }, h("span", { class: "bar", style: { width: `${Math.max(1, (100 * i.value) / max)}%` } })),
        h("span", { class: "bar-value" }, fmtNum(i.value)),
      ),
    ),
  );
}

function keysSection(ctx, keys, usage) {
  const live = keys.filter((k) => !k.revoked_at).length;
  const perDay = new Map(lastDays(7).map((d) => [d, 0]));
  for (const row of usage) {
    if (perDay.has(row.day)) perDay.set(row.day, perDay.get(row.day) + (row.n || 0));
  }
  const total = [...perDay.values()].reduce((a, b) => a + b, 0);
  return h(
    "div",
    { class: "section" },
    h("div", { class: "section-title" }, "Developer keys"),
    h("div", { class: "milestones" }, stat("Live keys", fmtNum(live), `${fmtNum(keys.length - live)} revoked`), stat("Calls, 7 days", fmtNum(total))),
    h("div", { style: { marginTop: "10px" } }, barList([...perDay].map(([day, value]) => ({ label: day.slice(5), value })))),
    isAdmin(ctx.auth.profile) ? null : h("p", { class: "muted small", style: { marginTop: "6px" } }, "Officers see their own keys only; admins see everyone's."),
  );
}

function exportSection(profiles) {
  const onlyReady = h("input", { type: "checkbox", checked: true });
  return h(
    "div",
    { class: "section" },
    h("div", { class: "section-title" }, "Export"),
    h("div", { class: "card" },
      h("p", { class: "small" }, "A CSV in the ROSTER_PATH shape, built in your browser from the rows you can see: ", h("span", { class: "mono muted" }, CSV_COLUMNS.join(", ")), "."),
      h("label", { class: "check-row", style: { marginTop: "8px" } }, onlyReady, h("span", {}, "Only rows the morning queue would use (not paused, a phone or an email, farmers with a farm)")),
      h("div", { class: "row row-wrap", style: { marginTop: "10px" } },
        h("button", {
          class: "btn btn-primary",
          type: "button",
          onclick: () => downloadText(`roster-${new Date().toISOString().slice(0, 10)}.csv`, rosterCsv(profiles, onlyReady.checked)),
        }, "Download the roster CSV"),
      ),
    ),
  );
}

export function renderAdmin(el, ctx) {
  if (!configured) {
    el.replaceChildren(notice(NOT_CONFIGURED));
    return {};
  }
  const { auth } = ctx;
  if (!auth.ready || (auth.user && !auth.profile && !auth.profileError)) {
    el.replaceChildren(loading(3));
    return {};
  }
  if (!auth.user) {
    el.replaceChildren(signInPrompt(ctx, "open the admin dashboard"));
    return {};
  }
  if (auth.profileError) {
    el.replaceChildren(errorNote(auth.profileError, "Could not load your profile"));
    return {};
  }
  if (!isStaff(auth.profile)) {
    el.replaceChildren(h("p", { class: "lede" }, "Officers and admins only."));
    return {};
  }

  let alive = true;
  let deliveries = null;
  el.replaceChildren(loading(5));
  loadAll().then(
    (state) => {
      if (!alive) return;
      deliveries = deliveriesSection(state.dates);
      el.replaceChildren(
        h("div", { class: "section" }, h("p", { class: "muted small" }, `Signed in as ${auth.profile.name || auth.user.email} (${auth.profile.role}).`), h("div", { style: { marginTop: "8px" } }, countCards(state.profiles, state.signups))),
        rosterSection(ctx, state.profiles),
        deliveries,
        keysSection(ctx, state.keys, state.usage),
        exportSection(state.profiles),
      );
    },
    (err) => {
      if (alive) el.replaceChildren(errorNote(err, "Could not load the dashboard"));
    },
  );
  return {
    destroy() {
      alive = false;
      deliveries?.destroy?.();
    },
  };
}
