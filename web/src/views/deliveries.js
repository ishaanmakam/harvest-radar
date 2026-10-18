import { h } from "../dom.js";
import { CHANNEL_LABEL, ROLE_LABEL, chip, fmtDate, titleCase } from "../format.js";

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
    );
    return { update() {} };
  }

  const byStatus = { sent: 0, pending: 0, failed: 0 };
  const byChannel = new Map();
  for (const row of rows) {
    if (byStatus[row.status] != null) byStatus[row.status] += 1;
    byChannel.set(row.channel, (byChannel.get(row.channel) || 0) + 1);
  }
  const date = rows[0]?.date;

  el.replaceChildren(
    h(
      "div",
      { class: "section" },
      h("h2", { class: "panel-title" }, "Morning deliveries"),
      h("p", { class: "muted small" }, date ? `${rows.length} messages queued for ${fmtDate(date, "long")}.` : `${rows.length} messages queued.`),
      h(
        "div",
        { class: "counts", style: { marginTop: "10px" } },
        chip("sent", `${byStatus.sent} sent`, { large: true }),
        chip("pending", `${byStatus.pending} pending`, { large: true }),
        chip("failed", `${byStatus.failed} failed`, { large: true }),
        ...[...byChannel].map(([channel, count]) => chip("none", `${count} ${CHANNEL_LABEL[channel] || titleCase(channel)}`.toLowerCase(), { large: true })),
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
  );
  return { update() {} };
}
