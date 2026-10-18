import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import { loadData } from "./data.js";
import { renderPlan, renderRoute } from "./panel.js";
import { createGlobe } from "./globe/globe.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const FILL_COLOR = [
  "match",
  ["get", "flag"],
  "act_now",
  "#d7263d",
  "watch",
  "#f4a259",
  "fine",
  "#3bb273",
  "#888888",
];

function fieldsBbox(fields) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  const walk = (coords) => {
    if (typeof coords[0] === "number") {
      west = Math.min(west, coords[0]);
      east = Math.max(east, coords[0]);
      south = Math.min(south, coords[1]);
      north = Math.max(north, coords[1]);
      return;
    }
    for (const part of coords) walk(part);
  };
  for (const feature of fields.features) walk(feature.geometry.coordinates);
  return [[west, south], [east, north]];
}

function showError(message) {
  const error = document.getElementById("error");
  error.hidden = false;
  error.textContent = message;
}

function setStats(data) {
  const counts = { act_now: 0, watch: 0, fine: 0 };
  for (const plan of data.plans.plans) {
    if (counts[plan.flag] !== undefined) counts[plan.flag] += 1;
  }
  const stats = document.getElementById("stats");
  stats.textContent = `${data.plans.as_of} · ${data.fields.features.length} farms · ${counts.act_now} act now · ${counts.watch} watch · ${counts.fine} fine`;
  const imagery = data.sources.frames === "out" ? "REAL" : "MOCK";
  const farms = data.sources.fields === "out" && data.sources.plans === "out" ? "REAL" : "MOCK";
  const badge = document.getElementById("badge");
  badge.textContent = `Imagery: ${imagery} · Farms & plans: ${farms}`;
  badge.className = imagery === "REAL" && farms === "REAL" ? "badge real" : "badge mock";
}

function setupTimelapse(map, frames, base) {
  const box = document.getElementById("timelapse");
  if (!frames?.frames?.length || !frames.bounds) return;
  const [west, south, east, north] = frames.bounds;
  const coordinates = [[west, north], [east, north], [east, south], [west, south]];
  const list = frames.frames;
  let index = list.length - 1;
  let timer = null;

  map.addSource("frame", {
    type: "image",
    url: `${base}/${list[index].rgb}`,
    coordinates,
  });
  map.addLayer(
    {
      id: "frame",
      type: "raster",
      source: "frame",
      paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "nearest" },
    },
    "fields-fill",
  );

  const slider = document.getElementById("frame-slider");
  const dateEl = document.getElementById("frame-date");
  const play = document.getElementById("play");
  slider.max = String(list.length - 1);
  slider.value = String(index);
  dateEl.textContent = list[index].date;
  box.hidden = false;

  const show = (next) => {
    index = next;
    slider.value = String(next);
    dateEl.textContent = list[next].date;
    map.getSource("frame").updateImage({ url: `${base}/${list[next].rgb}`, coordinates });
  };

  const pause = () => {
    if (timer) clearInterval(timer);
    timer = null;
    play.textContent = "Play";
  };

  slider.addEventListener("input", () => {
    pause();
    show(Number(slider.value));
  });
  play.addEventListener("click", () => {
    if (timer) {
      pause();
      return;
    }
    play.textContent = "Pause";
    timer = setInterval(() => show((index + 1) % list.length), 800);
  });
}

function addFields(map, fields) {
  map.addSource("fields", { type: "geojson", data: fields });
  map.addLayer({
    id: "fields-fill",
    type: "fill",
    source: "fields",
    paint: { "fill-color": FILL_COLOR, "fill-opacity": 0.45 },
  });
  map.addLayer({
    id: "fields-line",
    type: "line",
    source: "fields",
    paint: { "line-color": "#ffffff", "line-width": 0.6, "line-opacity": 0.6 },
  });
  map.addLayer({
    id: "fields-selected",
    type: "line",
    source: "fields",
    filter: ["==", ["get", "field_id"], ""],
    paint: { "line-color": "#ffffff", "line-width": 2.5 },
  });
}

async function start() {
  const routeEl = document.getElementById("route");
  const planEl = document.getElementById("plan");
  renderPlan(planEl, null);

  let data;
  try {
    data = await loadData();
  } catch (err) {
    showError(err.message);
    return;
  }
  setStats(data);

  const featuresById = new Map(data.fields.features.map((feature) => [feature.properties.field_id, feature]));
  let selectedId = "";
  let routeLimit = 12;

  const drawRoute = () => {
    renderRoute(routeEl, data.route, {
      onSelect: selectFarm,
      limit: routeLimit,
      selectedId,
      officerDeliveries: data.officerDeliveries,
      onLimit: (next) => {
        routeLimit = next;
        drawRoute();
      },
    });
  };

  function highlight(id) {
    if (!map.isStyleLoaded() || !map.getLayer("fields-selected")) return;
    map.setFilter("fields-selected", ["==", ["get", "field_id"], id]);
  }

  function selectFarm(id, feature) {
    selectedId = id;
    const match = feature || featuresById.get(id);
    highlight(id);
    renderPlan(planEl, data.plansById.get(id) || null, match, data.deliveriesByFarm.get(id));
    drawRoute();
  }

  drawRoute();

  const frameBounds = data.frames?.bounds;
  const aoiBounds = frameBounds
    ? [[frameBounds[0], frameBounds[1]], [frameBounds[2], frameBounds[3]]]
    : fieldsBbox(data.fields);
  const pass = data.frames?.frames?.length ? data.frames.frames[data.frames.frames.length - 1].date : data.plans.as_of;
  const [, month, day] = (pass || "2026-10-17").split("-");
  let actNow = 0;
  for (const plan of data.plans.plans) if (plan.flag === "act_now") actNow += 1;
  const standIn = data.sources.plans === "out" ? "" : " (stand-in farms)";
  document.getElementById("intro-stats").textContent = `Story County, Iowa · latest clear pass ${MONTHS[Number(month) - 1]} ${Number(day)} · ${data.plans.plans.length} farms · ${actNow} act now${standIn}`;

  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        esri: {
          type: "raster",
          tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
          tileSize: 256,
          attribution: "Tiles © Esri",
        },
      },
      layers: [{ id: "esri", type: "raster", source: "esri" }],
    },
  });

  map.on("load", () => {
    addFields(map, data.fields);
    if (selectedId) highlight(selectedId);
    setupTimelapse(map, data.frames, data.framesBase);
    map.fitBounds(aoiBounds, { padding: 24 });

    map.on("mouseenter", "fields-fill", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "fields-fill", () => {
      map.getCanvas().style.cursor = "";
    });
    map.on("click", "fields-fill", (event) => {
      const feature = event.features[0];
      selectFarm(feature.properties.field_id, feature);
    });
  });

  const intro = document.getElementById("intro");
  const enterBtn = document.getElementById("enter");
  const orbitBtn = document.getElementById("orbit");
  const panel = document.getElementById("panel");
  const card = document.getElementById("place-card");
  const toast = document.getElementById("toast");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cardBody = "Sentinel-2 passes over this place every ~5 days, so Overfield can inspect farms here the same way. Our live demo landscape is Story County, Iowa: real imagery, real farm plans.";
  let globe = null;
  let moveToken = 0;
  let toastTimer = 0;
  let revealTimer = 0;

  function showFarmChrome(show) {
    panel.hidden = !show;
    const visibility = show ? "visible" : "none";
    for (const id of ["fields-fill", "fields-line", "fields-selected", "frame"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility);
    }
    const timelapse = document.getElementById("timelapse");
    if (!show) timelapse.hidden = true;
    else if (data.frames?.frames?.length) timelapse.hidden = false;
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
    map.fitBounds(aoiBounds, { padding: 24, duration: reducedMotion ? 0 : duration });
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
        document.getElementById("place-title").textContent = title;
        document.getElementById("place-body").textContent = cardBody;
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
    globe = createGlobe(document.getElementById("globe"), {
      bounds: frameBounds,
      onEnter: handoff,
      onToast: showToast,
    });
    globe.start();
  } catch (err) {
    console.error(err);
    intro.classList.add("is-hidden");
    orbitBtn.hidden = true;
  }

  enterBtn.addEventListener("click", () => {
    if (!globe || intro.classList.contains("is-leaving")) return;
    enterBtn.disabled = true;
    globe.flyIn();
  });
  orbitBtn.addEventListener("click", backToOrbit);
  document.getElementById("back-orbit").addEventListener("click", backToOrbit);
  document.getElementById("see-live").addEventListener("click", () => {
    card.hidden = true;
    showFarmChrome(false);
    map.fitBounds(aoiBounds, { padding: 24, duration: reducedMotion ? 0 : 2500 });
    if (reducedMotion) showFarmChrome(true);
    else whenMoved(atAoi, () => showFarmChrome(true));
  });
}

start();
