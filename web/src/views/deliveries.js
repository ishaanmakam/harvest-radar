import { h } from "../dom.js";
import { CHANNEL_LABEL, ROLE_LABEL, chip, fmtDate, fmtNum, titleCase } from "../format.js";

/** How a farmer gets the plan: the Photon text and voice loop, in plain words, with the sign-up link. */
function explainer(ctx) {
  return h(
    "div",
    { class: "explainer" },
    h("div", { class: "section-title" }, "How a farmer gets this"),
    h(
      "ul",
      { class: "small explainer-list" },
      h("li", {}, "Every morning the plan goes out as a text and a voice call, in the farmer's own language, to the number in the roster."),
      h("li", {}, "Anyone can sign up with a farm address and a phone number or email; the plan starts the next morning."),
      h("li", {}, "A farmer can call or text back about their own farm; replying STOP ends the messages."),
      h("li", {}, "Sends go only to numbers in the roster, and the queue is dry-run before every real send."),
    ),
    h("a", { class: "btn btn-primary", href: ctx.signupUrl, target: "_blank", rel: "noopener" }, "Get your farm's morning plan"),
  );
}

export function renderDeliveries(el, ctx) {
  const { data } = ctx;
  const rows = data.deliveries?.deliveries || [];

  if (!data.deliveries) {
    el.replaceChildren(
      h("div", { class: "section" }, h("h2", { class: "panel-title" }, "Morning deliveries")),
      h(
        "div",
        { class: "note note-info" },
        "No delivery log in this dataset yet. The morning queue writes deliveries.json after a dry run or a real send; until then nothing has gone out.",
      ),
      h("div", { class: "section" }, explainer(ctx)),
    );
    return { update() {} };
  }

  // Counts for the plan date: the day's rows, or every row when the log holds one day only.
  const planDate = data.plans?.as_of;
  const dates = [...new Set(rows.map((r) => r.date).filter(Boolean))].sort();
  const forDate = dates.includes(planDate) ? planDate : dates[dates.length - 1];
  const dayRows = forDate ? rows.filter((r) => r.date === forDate) : rows;

  const byStatus = { sent: 0, pending: 0, failed: 0 };
  const byChannel = new Map();
  for (const row of dayRows) {
    if (byStatus[row.status] != null) byStatus[row.status] += 1;
    const channel = row.channel || "unknown";
    if (!byChannel.has(channel)) byChannel.set(channel, { sent: 0, pending: 0, failed: 0, total: 0 });
    const c = byChannel.get(channel);
    if (c[row.status] != null) c[row.status] += 1;
    c.total += 1;
  }
  const farmers = dayRows.filter((r) => r.role === "farmer").length;
  const officers = dayRows.filter((r) => r.role === "officer").length;

  el.replaceChildren(
    h(
      "div",
      { class: "section" },
      h("h2", { class: "panel-title" }, "Morning deliveries"),
      h(
        "p",
        { class: "muted small" },
        forDate
          ? `${fmtNum(dayRows.length)} messages for ${fmtDate(forDate, "long")}: ${fmtNum(farmers)} to farmers, ${fmtNum(officers)} to the officer.${dates.length > 1 ? ` The log holds ${dates.length} days.` : ""}`
          : `${fmtNum(rows.length)} messages queued.`,
      ),
      h(
        "div",
        { class: "counts", style: { marginTop: "10px" } },
        chip("sent", `${byStatus.sent} sent`, { large: true }),
        chip("pending", `${byStatus.pending} pending`, { large: true }),
        chip("failed", `${byStatus.failed} failed`, { large: true }),
      ),
      h(
        "div",
        { class: "channel-counts" },
        ...[...byChannel].map(([channel, c]) =>
          h(
            "div",
            { class: "outlook-card" },
            h("div", { class: "label" }, CHANNEL_LABEL[channel] || titleCase(channel)),
            h("div", { class: "value" }, String(c.total)),
            h("div", { class: "sub" }, `${c.sent} sent · ${c.pending} pending · ${c.failed} failed`),
          ),
        ),
      ),
    ),
    rows.length
      ? h(
          "div",
          { class: "section" },
          h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              { class: "table" },
              h("thead", {}, h("tr", {}, h("th", {}, "Role"), h("th", {}, "Farm"), h("th", {}, "Channel"), h("th", {}, "Status"), h("th", {}, "Detail"))),
              h(
                "tbody",
                {},
                ...rows.map((row) =>
                  h(
                    "tr",
                    {
                      class: row.farm_id ? "is-clickable" : null,
                      tabindex: row.farm_id ? "0" : null,
                      onclick: row.farm_id ? () => ctx.select(row.farm_id) : null,
                      onkeydown: row.farm_id
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              ctx.select(row.farm_id);
                            }
                          }
                        : null,
                    },
                    h("td", {}, ROLE_LABEL[row.role] || titleCase(row.role)),
                    h("td", { class: "mono" }, row.farm_id || "—"),
                    h("td", {}, CHANNEL_LABEL[row.channel] || titleCase(row.channel)),
                    h("td", {}, chip(row.status)),
                    h("td", { class: "muted" }, row.detail || ""),
                  ),
                ),
              ),
            ),
          ),
        )
      : h("div", { class: "empty" }, "The delivery log is empty."),
    h("div", { class: "section" }, explainer(ctx)),
  );
  return { update() {} };
}
