import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import { loadData } from "./data.js";
import { store } from "./state.js";
import { createMap } from "./map.js";
import { renderPlan } from "./panel.js";
import { renderHeadline, renderHeadlineSkeleton } from "./views/headline.js";
import { computeVisible, renderFields } from "./views/fields.js";
import { createTimelapse } from "./views/timelapse.js";
import { renderRoute } from "./views/route.js";
import { renderDeliveries } from "./views/deliveries.js";
import { renderSeason } from "./views/season.js";
import { createGlobe } from "./globe/globe.js";
import { h, skeleton } from "./dom.js";
import { CROP_LABEL, FLAG_LABEL, chip, fmtHa, isPhone, titleCase } from "./format.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const VIEWS = ["fields", "timelapse", "route", "deliveries", "season"];
const $ = (id) => document.getElementById(id);

function showError(message) {
  const error = $("error");
  error.hidden = false;
  error.replaceChildren(h("strong", {}, "Nothing to show."), message);
}

/** The badge says where the imagery and the farm group came from; with real outlines pending it toggles them. */
function setBadge(data, mapApi) {
  const imagery = data.sources.frames === "out" ? "REAL" : "MOCK";
  const farms = data.sources.fields === "out" && data.sources.plans === "out" ? "REAL" : "MOCK";
  const standIn = imagery !== "REAL" || farms !== "REAL";
  let badge = $("badge");

  if (data.realFields) {
    const button = h("button", { id: "badge", type: "button", class: "badge badge-standin badge-toggle" });
    badge.replaceWith(button);
    badge = button;
    const render = () => {
      const on = mapApi.realOutlinesOn();
      badge.textContent = `Imagery: ${imagery} · Farms & plans: ${farms} · Real field outlines: ${on ? "shown" : "hidden"}`;
      badge.setAttribute("aria-pressed", String(on));
      badge.title = on ? "Hide the real field outlines (plans pending)" : "Show the real field outlines (plans pending)";
    };
    badge.addEventListener("click", () => {
      mapApi.setRealOutlines(!mapApi.realOutlinesOn());
      render();
    });
    render();
    return;
  }

  badge.textContent = standIn ? `Imagery: ${imagery} · Farms & plans: ${farms}` : "Live season";
  badge.className = standIn ? "badge badge-standin" : "badge badge-live";
  badge.title = standIn ? "Stand-in season: at least one file came from data/mock" : "Every file came from data/out";
}

function setIntroStats(data) {
  const pass = data.frames?.frames?.length ? data.frames.frames[data.frames.frames.length - 1].date : data.plans.as_of;
  const [, month, day] = (pass || "2026-10-17").split("-");
  let actNow = 0;
  for (const plan of data.plans.plans) if (plan.flag === "act_now") actNow += 1;
  const standIn = data.sources.plans === "out" ? "" : " (stand-in farms)";
  $("intro-stats").textContent = `Story County, Iowa · latest clear pass ${MONTHS[Number(month) - 1]} ${Number(day)} · ${data.plans.plans.length} farms · ${actNow} act now${standIn}`;
}

async function start() {
  const signup = $("signup");
  signup.href = import.meta.env.VITE_SIGNUP_URL || "http://localhost:8788/signup";

  const headline = $("headline");
  const dock = $("dock");
  const dockBody = $("dock-body");
  const panel = $("panel");
  const tooltip = $("tooltip");
  const timelapseBox = $("timelapse");
  const stage = document.querySelector(".stage");
  const tabs = [...document.querySelectorAll(".tab")];
  const intro = $("intro");
  const enterBtn = $("enter");
  const orbitBtn = $("orbit");
  const card = $("place-card");
  const toast = $("toast");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  renderHeadlineSkeleton(headline);
  dockBody.replaceChildren(skeleton("100%", 36), skeleton("70%", 14), skeleton("90%", 14), skeleton("60%", 14));
  for (const tab of tabs) tab.addEventListener("click", () => store.set({ view: tab.dataset.view }));
  $("dock-handle").addEventListener("click", (event) => {
    const expanded = dock.classList.toggle("is-expanded");
    event.currentTarget.setAttribute("aria-expanded", String(expanded));
  });

  let data;
  try {
    data = await loadData();
  } catch (err) {
    showError(err.message);
    intro.classList.add("is-hidden");
    headline.hidden = true;
    dock.hidden = true;
    return;
  }
  renderHeadline(headline, data);
  setIntroStats(data);

  const mapApi = createMap({ container: "map", data, onSelect: selectFarm, onHover: showTooltip });
  const { map } = mapApi;
  setBadge(data, mapApi);
  const timelapse = createTimelapse({
    data,
    store,
    mapApi,
    els: {
      box: timelapseBox,
      play: $("play"),
      prev: $("frame-prev"),
      next: $("frame-next"),
      slider: $("frame-slider"),
      date: $("frame-date"),
      clear: $("frame-clear"),
      speed: $("frame-speed"),
      opacity: $("frame-opacity"),
    },
  });

  let chromeShown = false;
  let current = null;

  const ctx = {
    data,
    store,
    mapApi,
    select: selectFarm,
    close: closeFarm,
    step: stepFarm,
    position: (id) => data.routeByFarm.get(id) || null,
  };

  function currentList() {
    const state = store.get();
    if (state.view === "route" && data.route?.stops?.length) return data.route.stops.map((stop) => stop.farm_id);
    if (state.view === "fields") return computeVisible(data, state.filters);
    return data.farmIds;
  }

  function selectFarm(id) {
    if (!id) return;
    store.set({ selected: id, list: currentList() });
  }

  function closeFarm() {
    if (store.get().selected) store.set({ selected: null });
  }

  function stepFarm(delta) {
    const state = store.get();
    const list = state.list.length ? state.list : currentList();
    if (!list.length) return;
    const index = list.indexOf(state.selected);
    const next = index < 0 ? (delta > 0 ? list[0] : list[list.length - 1]) : list[(index + delta + list.length) % list.length];
    store.set({ selected: next, list });
  }

  function mapPadding() {
    const selected = Boolean(store.get().selected);
    if (isPhone()) {
      const sheet = Math.round(stage.clientHeight * (selected ? 0.6 : 0.42)) + 12;
      return { top: 90, left: 12, right: 12, bottom: sheet };
    }
    const dockWidth = dock.hidden ? 0 : dock.offsetWidth || 360;
    const panelWidth = selected ? panel.offsetWidth || 420 : 0;
    return { top: 96, left: dockWidth + 32, right: panelWidth + 32, bottom: timelapseBox.hidden ? 32 : 96 };
  }

  function syncChrome() {
    const state = store.get();
    headline.hidden = !chromeShown;
    dock.hidden = !chromeShown || state.view === "timelapse";
    panel.hidden = !chromeShown || !state.selected;
    if (!chromeShown) timelapseBox.hidden = true;
  }

  function renderView(name) {
    current?.api?.destroy?.();
    if (current?.name === "timelapse") timelapse.leave();
    for (const tab of tabs) {
      if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    dock.dataset.view = name;
    dock.classList.remove("is-expanded");
    dockBody.replaceChildren();
    dockBody.scrollTop = 0;
    let api = { update() {} };
    if (name === "fields") api = renderFields(dockBody, ctx);
    else if (name === "timelapse") {
      if (timelapse.enabled) {
        if (chromeShown) timelapse.enter();
      } else {
        dockBody.replaceChildren(
          h("div", { class: "section" }, h("h2", { class: "panel-title" }, "Season time-lapse")),
          h("div", { class: "empty" }, "No frames in this dataset. The pipeline writes frames/frames.json with one image per clear pass."),
        );
      }
    } else if (name === "route") api = renderRoute(dockBody, ctx);
    else if (name === "deliveries") api = renderDeliveries(dockBody, ctx);
    else if (name === "season") api = renderSeason(dockBody, ctx);
    mapApi.setRouteMarkers(name === "route" && data.route ? data.route.stops.filter((stop) => stop.action === "visit") : null);
    if (!chromeShown) mapApi.setChrome(false);
    current = { name, api };
    syncChrome();
  }

  function syncVisible() {
    const state = store.get();
    mapApi.setVisible(state.view === "fields" ? new Set(computeVisible(data, state.filters)) : null);
  }

  function renderDrawer() {
    const id = store.get().selected;
    if (!id) {
      syncChrome();
      return;
    }
    renderPlan(panel, ctx, id);
    panel.querySelector(".drawer-body").scrollTop = 0;
    syncChrome();
  }

  store.on((state, keys) => {
    if (keys.includes("view")) {
      renderView(state.view);
      syncVisible();
      if (chromeShown && !state.selected) mapApi.fitAll(mapPadding());
    }
    if (keys.includes("filters")) {
      syncVisible();
      state.list = currentList();
    }
    if (keys.includes("selected")) {
      mapApi.select(state.selected);
      renderDrawer();
      if (state.selected && chromeShown) mapApi.flyTo(state.selected, mapPadding());
      if (state.selected) panel.querySelector("#drawer-title")?.focus({ preventScroll: true });
    }
    current?.api?.update?.(keys);
    timelapse.update(keys);
  });

  document.addEventListener("keydown", (event) => {
    if (!chromeShown) return;
    const tag = event.target?.tagName;
    const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
    if (event.key === "Escape") {
      closeFarm();
      return;
    }
    if (typing || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault();
      stepFarm(1);
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault();
      stepFarm(-1);
    }
  });

  function showTooltip(id, point) {
    if (!id || isPhone()) {
      tooltip.hidden = true;
      return;
    }
    const plan = data.plansById.get(id);
    const props = data.featuresById.get(id)?.properties || {};
    tooltip.replaceChildren(
      h("div", { class: "tip-head" }, h("span", {}, id), chip(plan?.flag ?? "none")),
      h("div", { class: "tip-meta" }, `${CROP_LABEL[props.crop] || titleCase(props.crop)} · ${fmtHa(props.area_ha)}${plan ? "" : ` · ${FLAG_LABEL.none}`}`),
      plan ? h("div", { class: "tip-today" }, plan.today) : null,
    );
    tooltip.hidden = false;
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;
    const x = point.x + 16 + width > stage.clientWidth ? point.x - width - 12 : point.x + 16;
    const y = point.y + 16 + height > stage.clientHeight ? point.y - height - 12 : point.y + 16;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
  }

  const frameBounds = data.frames?.bounds;
  const aoiBounds = frameBounds
    ? [
        [frameBounds[0], frameBounds[1]],
        [frameBounds[2], frameBounds[3]],
      ]
    : [
        [data.bbox[0], data.bbox[1]],
        [data.bbox[2], data.bbox[3]],
      ];

  await mapApi.ready;
  renderView(store.get().view);
  syncVisible();

  // Everything below is the orbit intro and its hand-off to the imagery map.
  const cardBody =
    "Sentinel-2 passes over this place every ~5 days, so Overfield can inspect farms here the same way. Our live demo landscape is Story County, Iowa: real imagery, real farm plans.";
  let globe = null;
  let moveToken = 0;
  let toastTimer = 0;
  let revealTimer = 0;

  function showFarmChrome(show) {
    chromeShown = show;
    mapApi.setChrome(show);
    syncChrome();
    if (show && store.get().view === "timelapse" && timelapse.enabled) timelapse.enter();
    if (!show) timelapse.leave();
  }

  function showToast(message) {
    toast.hidden = false;
    toast.textContent = message;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 1600);
  }

  function atPoint(lng, lat) {
    const center = map.getCenter();
    return Math.abs(center.lng - lng) < 0.6 && Math.abs(center.lat - lat) < 0.6 && map.getZoom() >= 9;
  }

  function atAoi() {
    const center = map.getCenter();
    const west = aoiBounds[0][0];
    const south = aoiBounds[0][1];
    const east = aoiBounds[1][0];
    const north = aoiBounds[1][1];
    return center.lng >= west - 0.3 && center.lng <= east + 0.3 && center.lat >= south - 0.3 && center.lat <= north + 0.3;
  }

  function whenMoved(ready, cb) {
    const token = ++moveToken;
    const onEnd = () => {
      if (token !== moveToken) {
        map.off("moveend", onEnd);
        return;
      }
      if (!ready()) return;
      map.off("moveend", onEnd);
      cb();
    };
    map.on("moveend", onEnd);
  }

  function openLive(duration) {
    card.hidden = true;
    const west = aoiBounds[0][0];
    const south = aoiBounds[0][1];
    const east = aoiBounds[1][0];
    const north = aoiBounds[1][1];
    map.jumpTo({ center: [(west + east) / 2, (south + north) / 2], zoom: 9 });
    map.fitBounds(aoiBounds, { padding: mapPadding(), duration: reducedMotion ? 0 : duration });
    whenMoved(atAoi, () => showFarmChrome(true));
    if (reducedMotion) showFarmChrome(true);
  }

  function handoff(detail) {
    intro.classList.add("is-leaving");
    globe.stop();
    map.resize();
    card.hidden = true;
    if (!detail || detail.live) {
      showFarmChrome(true);
      openLive(1200);
    } else {
      showFarmChrome(false);
      map.jumpTo({ center: [detail.lng, detail.lat], zoom: 3 });
      map.flyTo({
        center: [detail.lng, detail.lat],
        zoom: 11,
        duration: reducedMotion ? 0 : 2200,
        essential: true,
      });
      const title = detail.name || `${detail.lat.toFixed(2)}, ${detail.lng.toFixed(2)}`;
      const reveal = () => {
        window.clearTimeout(revealTimer);
        $("place-title").textContent = title;
        $("place-body").textContent = cardBody;
        card.hidden = false;
      };
      if (reducedMotion) reveal();
      else {
        whenMoved(() => atPoint(detail.lng, detail.lat), reveal);
        revealTimer = window.setTimeout(reveal, 2400);
      }
    }
    window.setTimeout(() => {
      intro.classList.add("is-hidden");
      enterBtn.disabled = false;
    }, 500);
  }

  function backToOrbit() {
    if (!globe) return;
    card.hidden = true;
    moveToken += 1;
    window.clearTimeout(revealTimer);
    enterBtn.disabled = false;
    intro.classList.remove("is-hidden", "is-leaving");
    globe.start();
    globe.flyOut();
  }

  try {
    globe = createGlobe($("globe"), {
      bounds: frameBounds,
      onEnter: handoff,
      onToast: showToast,
    });
    globe.start();
  } catch (err) {
    console.error(err);
    intro.classList.add("is-hidden");
    orbitBtn.hidden = true;
    map.resize();
    showFarmChrome(true);
    map.fitBounds(aoiBounds, { padding: mapPadding(), duration: 0 });
  }

  enterBtn.addEventListener("click", () => {
    if (!globe || intro.classList.contains("is-leaving")) return;
    enterBtn.disabled = true;
    globe.flyIn();
  });
  orbitBtn.addEventListener("click", backToOrbit);
  $("back-orbit").addEventListener("click", backToOrbit);
  $("see-live").addEventListener("click", () => {
    card.hidden = true;
    showFarmChrome(false);
    map.fitBounds(aoiBounds, { padding: mapPadding(), duration: reducedMotion ? 0 : 2500 });
    if (reducedMotion) showFarmChrome(true);
    else whenMoved(atAoi, () => showFarmChrome(true));
  });

  window.addEventListener("resize", () => {
    tooltip.hidden = true;
  });
}

start();
