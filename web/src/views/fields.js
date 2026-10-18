import { h } from "../dom.js";
import { CHECK_KINDS, CHECK_LABEL, CROP_LABEL, FLAGS, FLAG_LABEL, SEVERITY, fmtHa, titleCase } from "../format.js";

const LIST_CAP = 300;

/** Farm ids that pass the current filters, in severity-then-id order. */
export function computeVisible(data, filters) {
  const query = filters.query.trim().toLowerCase();
  const ids = [];
  for (const id of data.farmIds) {
    const feature = data.featuresById.get(id);
    const plan = data.plansById.get(id);
    const flag = plan?.flag ?? "none";
    if (flag !== "none" && !filters.flags.has(flag)) continue;
    if (filters.crop !== "all" && (feature.properties.crop || "unknown") !== filters.crop) continue;
    if (filters.zone !== "all" && (plan?.zone_id ?? feature.properties.zone_id) !== filters.zone) continue;
    if (filters.check !== "any") {
      const check = plan?.checks?.find((c) => c.kind === filters.check);
      if (!check || check.status === "clear") continue;
    }
    if (query && !id.toLowerCase().includes(query)) continue;
    ids.push(id);
  }
  return ids;
}

function option(value, label, selected) {
  return h("option", { value, selected: selected ? true : null }, label);
}

export function renderFields(el, ctx) {
  const { data, store } = ctx;
  const filters = store.get().filters;

  const search = h("input", {
    class: "input",
    type: "search",
    id: "farm-search",
    placeholder: "Find a farm, e.g. F0042",
    autocomplete: "off",
    value: filters.query,
    "aria-label": "Find a farm by id",
    oninput: (event) => store.setFilters({ query: event.target.value }),
    onkeydown: (event) => {
      if (event.key === "Enter") {
        const first = computeVisible(data, store.get().filters)[0];
        if (first) ctx.select(first);
      }
    },
  });

  const legendRows = FLAGS.map((flag) =>
    h(
      "button",
      {
        class: "legend-row",
        type: "button",
        "aria-pressed": String(filters.flags.has(flag)),
        dataset: { flag },
        onclick: () => {
          const next = new Set(store.get().filters.flags);
          if (next.has(flag)) next.delete(flag);
          else next.add(flag);
          store.setFilters({ flags: next });
        },
      },
      h("span", { class: `swatch swatch-${flag}` }),
      h("span", {}, FLAG_LABEL[flag]),
      h("span", { class: "count", dataset: { count: flag } }, "0"),
    ),
  );
  const noPlanCount = data.fields.features.filter((f) => f.properties.flag === "none").length;
  if (noPlanCount) {
    legendRows.push(
      h(
        "div",
        { class: "legend-row legend-static" },
        h("span", { class: "swatch swatch-none" }),
        h("span", {}, FLAG_LABEL.none),
        h("span", { class: "count" }, String(noPlanCount)),
      ),
    );
  }
  if (data.zones) {
    legendRows.push(
      h(
        "div",
        { class: "legend-row legend-static" },
        h("span", { class: "swatch swatch-zone" }),
        h("span", {}, "Zone boundary"),
      ),
    );
  }
  if (data.realFields) {
    legendRows.push(
      h(
        "div",
        { class: "legend-row legend-static" },
        h("span", { class: "swatch swatch-outline" }),
        h("span", {}, "Real field outlines (plans pending)"),
        h("span", { class: "count" }, String(data.realFields.features?.length ?? "")),
      ),
    );
  }

  const crops = [...new Set(data.fields.features.map((f) => f.properties.crop || "unknown"))].sort();
  const cropSelect = h(
    "select",
    { class: "select", id: "crop-filter", onchange: (e) => store.setFilters({ crop: e.target.value }) },
    option("all", "All crops", filters.crop === "all"),
    ...crops.map((crop) => option(crop, CROP_LABEL[crop] || titleCase(crop), filters.crop === crop)),
  );

  const zoneIds = [...new Set(data.fields.features.map((f) => f.properties.zone_id).filter(Boolean))].sort();
  const zoneSelect = h(
    "select",
    { class: "select", id: "zone-filter", onchange: (e) => store.setFilters({ zone: e.target.value }) },
    option("all", "All zones", filters.zone === "all"),
    ...zoneIds.map((id) => option(id, data.zonesById.get(id)?.name || id, filters.zone === id)),
  );

  const checkSelect = h(
    "select",
    { class: "select", id: "check-filter", onchange: (e) => store.setFilters({ check: e.target.value }) },
    option("any", "Any check", filters.check === "any"),
    ...CHECK_KINDS.map((kind) => option(kind, `${CHECK_LABEL[kind]} needs attention`, filters.check === kind)),
  );

  const resetButton = h(
    "button",
    {
      class: "btn-link small",
      type: "button",
      onclick: () => {
        store.setFilters({ flags: new Set(FLAGS), crop: "all", zone: "all", check: "any", query: "" });
        search.value = "";
        cropSelect.value = "all";
        zoneSelect.value = "all";
        checkSelect.value = "any";
      },
    },
    "Reset",
  );

  const listTitle = h("div", { class: "section-title" }, "Farms", h("span", { class: "count" }));
  const list = h("ul", { class: "farm-list", id: "farm-list", "aria-label": "Farms in view" });
  const listNote = h("p", { class: "muted small", hidden: true });

  el.replaceChildren(
    h("div", { class: "section" }, search),
    h(
      "div",
      { class: "section" },
      h("div", { class: "row", style: { justifyContent: "space-between" } }, h("div", { class: "section-title", style: { marginBottom: 0 } }, "Legend and filters"), resetButton),
      h("div", { style: { marginTop: "6px" } }, ...legendRows),
      h(
        "div",
        { class: "grid-2", style: { marginTop: "10px" } },
        h("div", { class: "field" }, h("label", { for: "crop-filter" }, "Crop"), cropSelect),
        h("div", { class: "field" }, h("label", { for: "zone-filter" }, "Zone"), zoneSelect),
        h("div", { class: "field", style: { gridColumn: "1 / -1" } }, h("label", { for: "check-filter" }, "Check"), checkSelect),
      ),
    ),
    h("div", { class: "section" }, listTitle, listNote, list),
  );

  function update() {
    const state = store.get();
    const visible = computeVisible(data, state.filters);
    const counts = { act_now: 0, watch: 0, fine: 0 };
    for (const id of visible) {
      const flag = data.plansById.get(id)?.flag;
      if (counts[flag] != null) counts[flag] += 1;
    }
    for (const row of legendRows) {
      const flag = row.dataset?.flag;
      if (!flag) continue;
      row.setAttribute("aria-pressed", String(state.filters.flags.has(flag)));
      row.querySelector("[data-count]").textContent = String(counts[flag]);
    }
    listTitle.querySelector(".count").textContent = `${visible.length} of ${data.farmIds.length}`;

    const shown = visible.slice(0, LIST_CAP);
    listNote.hidden = visible.length <= LIST_CAP;
    listNote.textContent = `Showing the first ${LIST_CAP} of ${visible.length}. Narrow the filters to see the rest.`;

    if (!shown.length) {
      list.replaceChildren(h("li", { class: "empty" }, "No farms match these filters."));
      return;
    }
    list.replaceChildren(
      ...shown.map((id) => {
        const plan = data.plansById.get(id);
        const props = data.featuresById.get(id).properties;
        const flag = plan?.flag ?? "none";
        return h(
          "li",
          {},
          h(
            "button",
            {
              class: `farm-row${state.selected === id ? " is-selected" : ""}`,
              type: "button",
              dataset: { farm: id },
              "aria-current": state.selected === id ? "true" : null,
              onclick: () => ctx.select(id),
            },
            h("span", { class: "dot", style: { background: `var(--${flag === "act_now" ? "act" : flag})` }, title: FLAG_LABEL[flag] }),
            h(
              "span",
              { style: { minWidth: 0 } },
              h("div", { class: "row" }, h("span", { class: "farm-id" }, id), h("span", { class: "farm-meta" }, `${CROP_LABEL[props.crop] || titleCase(props.crop)} · ${fmtHa(props.area_ha)}`)),
              h("div", { class: "farm-today" }, plan?.today || "No plan for this farm."),
            ),
          ),
        );
      }),
    );
  }

  update();
  return {
    update(keys) {
      if (keys.includes("filters") || keys.includes("selected")) update();
      if (keys.includes("selected") && store.get().selected) {
        list.querySelector(`[data-farm="${store.get().selected}"]`)?.scrollIntoView({ block: "nearest" });
      }
    },
  };
}

export { SEVERITY };
