import maplibregl from "maplibre-gl";
import { h } from "./dom.js";
import { geometryBbox } from "./data.js";
import { reduceMotion } from "./format.js";

const FLAG_COLOR = ["match", ["get", "flag"], "act_now", "#d7263d", "watch", "#f2a93b", "fine", "#43b36b", "#8d969e"];
const IS_MATCH = ["boolean", ["get", "match"], true];
const FIELD_LAYERS = ["fields-fill", "fields-line", "fields-hover", "fields-selected", "fields-selected-inner", "zones-line", "frame"];
const YIELD_RAMP = ["#f6f0b5", "#c6e29a", "#8fd38a", "#49b36d", "#1b8a4d"];
export const YIELD_COLORS = YIELD_RAMP;
const YIELD_COLOR = ["match", ["get", "yield_bin"], 0, YIELD_RAMP[0], 1, YIELD_RAMP[1], 2, YIELD_RAMP[2], 3, YIELD_RAMP[3], 4, YIELD_RAMP[4], "#6b7378"];

/**
 * The imagery map: Esri basemap, the latest satellite frame under the farm polygons, zone outlines,
 * numbered route markers. The intro's fly-in and the place card drive the camera from main.js.
 */
export function createMap({ container, data, onSelect, onHover }) {
  const map = new maplibregl.Map({
    container,
    attributionControl: false,
    style: {
      version: 8,
      sources: {
        esri: {
          type: "raster",
          tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
          tileSize: 256,
          attribution: "Imagery © Esri",
          maxzoom: 18,
        },
      },
      layers: [{ id: "esri", type: "raster", source: "esri" }],
    },
    minZoom: 2,
    maxZoom: 18,
  });
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

  const markers = { zones: [], route: [] };
  const frames = data.frames?.frames?.length && data.frames.bounds ? data.frames.frames : null;
  const frameUrl = (index) => `${data.framesBase}/${frames[index].rgb}`;
  let dimmed = false;
  let chromeVisible = false;
  let realOutlines = Boolean(data.realFields);
  let ftwOutlines = false;
  let fillMode = "flag";

  function fieldOpacity() {
    return dimmed ? ["case", IS_MATCH, 0.22, 0.03] : ["case", IS_MATCH, 0.48, 0.06];
  }

  function addLayers() {
    if (frames) {
      const [west, south, east, north] = data.frames.bounds;
      map.addSource("frame", {
        type: "image",
        url: frameUrl(frames.length - 1),
        coordinates: [
          [west, north],
          [east, north],
          [east, south],
          [west, south],
        ],
      });
      map.addLayer({
        id: "frame",
        type: "raster",
        source: "frame",
        layout: { visibility: "none" },
        paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "nearest" },
      });
    }

    if (data.zones) {
      map.addSource("zones", { type: "geojson", data: data.zones });
      map.addLayer({
        id: "zones-line",
        type: "line",
        source: "zones",
        layout: { visibility: "none" },
        paint: { "line-color": "#ffffff", "line-width": 1.2, "line-opacity": 0.55, "line-dasharray": [2, 2] },
      });
      for (const feature of data.zones.features) {
        const { centroid, name, zone_id: zoneId } = feature.properties;
        const [west, south, east, north] = geometryBbox(feature.geometry);
        const lngLat = centroid || [(west + east) / 2, (south + north) / 2];
        const el = h("div", { class: "zone-label", hidden: true }, name || zoneId);
        markers.zones.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(lngLat).addTo(map));
      }
    }

    const hidden = { visibility: "none" };
    if (data.realFields) {
      // Real field outlines (plans pending): under the mock farms, no fill, no click.
      map.addSource("real-fields", { type: "geojson", data: data.realFields });
      map.addLayer({
        id: "real-outlines",
        type: "line",
        source: "real-fields",
        layout: hidden,
        paint: { "line-color": "#ffffff", "line-width": 1, "line-opacity": 0.5 },
      });
    }

    if (data.ftw?.features?.length) {
      // Fields of The World, the open global field map: outlines only, for comparison, no click.
      map.addSource("ftw", { type: "geojson", data: data.ftw });
      map.addLayer({
        id: "ftw-outlines",
        type: "line",
        source: "ftw",
        layout: hidden,
        paint: { "line-color": "#8fd3ff", "line-width": 1.3, "line-opacity": 0.85, "line-dasharray": [3, 2] },
      });
    }

    map.addSource("fields", { type: "geojson", data: data.fields });
    map.addLayer({ id: "fields-fill", type: "fill", source: "fields", layout: hidden, paint: { "fill-color": FLAG_COLOR, "fill-opacity": fieldOpacity() } });
    map.addLayer({
      id: "fields-line",
      type: "line",
      source: "fields",
      layout: hidden,
      paint: { "line-color": "#ffffff", "line-width": 0.7, "line-opacity": ["case", IS_MATCH, 0.7, 0.15] },
    });
    map.addLayer({ id: "fields-hover", type: "line", source: "fields", layout: hidden, filter: ["==", ["get", "field_id"], ""], paint: { "line-color": "#ffffff", "line-width": 2 } });
    map.addLayer({ id: "fields-selected", type: "line", source: "fields", layout: hidden, filter: ["==", ["get", "field_id"], ""], paint: { "line-color": "#ffffff", "line-width": 3.5 } });
    map.addLayer({
      id: "fields-selected-inner",
      type: "line",
      source: "fields",
      layout: hidden,
      filter: ["==", ["get", "field_id"], ""],
      paint: { "line-color": FLAG_COLOR, "line-width": 1.5 },
    });

    map.on("mousemove", "fields-fill", (event) => {
      const feature = event.features?.[0];
      if (!feature || !chromeVisible) return;
      map.getCanvas().style.cursor = "pointer";
      map.setFilter("fields-hover", ["==", ["get", "field_id"], feature.properties.field_id]);
      onHover?.(feature.properties.field_id, event.point);
    });
    map.on("mouseleave", "fields-fill", () => {
      map.getCanvas().style.cursor = "";
      map.setFilter("fields-hover", ["==", ["get", "field_id"], ""]);
      onHover?.(null);
    });
    map.on("click", "fields-fill", (event) => {
      const feature = event.features?.[0];
      if (feature && chromeVisible) onSelect?.(feature.properties.field_id);
    });
  }

  const ready = new Promise((resolve) => {
    map.on("load", () => {
      addLayers();
      resolve();
    });
  });

  const duration = (ms) => (reduceMotion() ? 0 : ms);

  function fitAll(padding, ms = 600) {
    const [west, south, east, north] = data.bbox;
    map.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding, duration: duration(ms) },
    );
  }

  function flyTo(farmId, padding) {
    const feature = data.featuresById.get(farmId);
    if (!feature) return;
    const [west, south, east, north] = geometryBbox(feature.geometry);
    map.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding, maxZoom: 14.5, duration: duration(700) },
    );
  }

  function select(farmId) {
    const filter = ["==", ["get", "field_id"], farmId || ""];
    if (!map.getLayer("fields-selected")) return;
    map.setFilter("fields-selected", filter);
    map.setFilter("fields-selected-inner", filter);
    for (const marker of markers.route) {
      marker.getElement().classList.toggle("is-selected", marker.getElement().dataset.farm === farmId);
    }
  }

  function setVisible(visibleIds) {
    for (const feature of data.fields.features) {
      feature.properties.match = visibleIds ? visibleIds.has(feature.properties.field_id) : true;
    }
    map.getSource("fields")?.setData(data.fields);
  }

  function setDimmed(value) {
    dimmed = value;
    if (map.getLayer("fields-fill")) map.setPaintProperty("fields-fill", "fill-opacity", fieldOpacity());
  }

  function setFrame(index, opacity) {
    if (!frames || !map.getSource("frame")) return;
    const clamped = Math.max(0, Math.min(frames.length - 1, index));
    map.getSource("frame").updateImage({ url: frameUrl(clamped) });
    if (opacity != null) map.setPaintProperty("frame", "raster-opacity", opacity);
  }

  function setRouteMarkers(stops) {
    for (const marker of markers.route) marker.remove();
    markers.route = [];
    if (!stops) return;
    stops.forEach((stop, index) => {
      const lngLat = data.centroids.get(stop.farm_id);
      if (!lngLat) return;
      const el = h(
        "button",
        {
          class: "route-marker",
          type: "button",
          dataset: { farm: stop.farm_id },
          "aria-label": `Stop ${index + 1}: ${stop.farm_id}`,
          onclick: (event) => {
            event.stopPropagation();
            onSelect?.(stop.farm_id);
          },
        },
        String(index + 1),
      );
      markers.route.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(lngLat).addTo(map));
    });
  }

  function syncRealOutlines() {
    if (map.getLayer("real-outlines")) {
      map.setLayoutProperty("real-outlines", "visibility", chromeVisible && realOutlines ? "visible" : "none");
    }
    if (map.getLayer("ftw-outlines")) {
      map.setLayoutProperty("ftw-outlines", "visibility", chromeVisible && ftwOutlines ? "visible" : "none");
    }
  }

  function setFtw(on) {
    ftwOutlines = Boolean(on);
    syncRealOutlines();
    return ftwOutlines;
  }

  /** "flag" colours the farms by today's flag; "yield" by calibrated bushels an acre. */
  function setFillMode(mode) {
    fillMode = mode;
    if (map.getLayer("fields-fill")) map.setPaintProperty("fields-fill", "fill-color", mode === "yield" ? YIELD_COLOR : FLAG_COLOR);
  }

  function setRealOutlines(on) {
    realOutlines = Boolean(on);
    syncRealOutlines();
    return realOutlines;
  }

  /** The farm layers, zone labels and route markers only show once the intro has flown in. */
  function setChrome(visible) {
    chromeVisible = visible;
    const visibility = visible ? "visible" : "none";
    for (const id of FIELD_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility);
    }
    syncRealOutlines();
    for (const marker of markers.zones) marker.getElement().hidden = !visible;
    for (const marker of markers.route) marker.getElement().hidden = !visible;
  }

  return {
    map,
    ready,
    frames,
    fitAll,
    flyTo,
    select,
    setVisible,
    setDimmed,
    setFrame,
    setRouteMarkers,
    setChrome,
    setRealOutlines,
    realOutlinesOn: () => realOutlines,
    setFtw,
    ftwOn: () => ftwOutlines,
    setFillMode,
    fillMode: () => fillMode,
  };
}
