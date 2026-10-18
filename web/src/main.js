import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import { loadData } from "./data.js";
import { renderPlan } from "./panel.js";

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
  const panel = document.getElementById("panel");
  renderPlan(panel, null);

  let data;
  try {
    data = await loadData();
  } catch (err) {
    showError(err.message);
    return;
  }
  setStats(data);

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
      const id = feature.properties.field_id;
      map.setFilter("fields-selected", ["==", ["get", "field_id"], id]);
      renderPlan(panel, data.plansById.get(id) || null, feature);
    });
  });
}

start();
