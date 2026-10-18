import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import { loadData } from "./data.js";
import { renderPlan, renderRoute } from "./panel.js";

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
  const badge = document.getElementById("badge");
  badge.textContent = data.isMock ? "MOCK" : "REAL";
  badge.className = data.isMock ? "badge mock" : "badge real";
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
    setupTimelapse(map, data.frames, data.base);
    const bounds = data.frames?.bounds
      ? [[data.frames.bounds[0], data.frames.bounds[1]], [data.frames.bounds[2], data.frames.bounds[3]]]
      : fieldsBbox(data.fields);
    map.fitBounds(bounds, { padding: 24 });

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
}

start();
