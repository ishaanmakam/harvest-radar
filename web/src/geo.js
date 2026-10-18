// Address to field, the same rule as intelligence/signup.py: the field whose polygon holds the point,
// else the nearest field by centroid within MAX_MATCH_KM, else none.

export const MAX_MATCH_KM = 5;
const NOMINATIM = "https://nominatim.openstreetmap.org/search";

export function haversineKm(lon1, lat1, lon2, lat2) {
  const r = 6371;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

function ringContains(ring, lon, lat) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

function polygonContains(rings, lon, lat) {
  if (!rings?.length || !ringContains(rings[0], lon, lat)) return false;
  for (let i = 1; i < rings.length; i += 1) if (ringContains(rings[i], lon, lat)) return false;
  return true;
}

export function geometryContains(geometry, lon, lat) {
  if (!geometry) return false;
  if (geometry.type === "Polygon") return polygonContains(geometry.coordinates, lon, lat);
  if (geometry.type === "MultiPolygon") return geometry.coordinates.some((poly) => polygonContains(poly, lon, lat));
  return false;
}

function ringCentroid(ring) {
  let area = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    area += cross;
    cx += (ring[j][0] + ring[i][0]) * cross;
    cy += (ring[j][1] + ring[i][1]) * cross;
  }
  if (Math.abs(area) < 1e-12) {
    const n = ring.length || 1;
    return [ring.reduce((s, p) => s + p[0], 0) / n, ring.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * area), cy / (3 * area)];
}

/** Area centroid of the largest outer ring, close enough to shapely's centroid for field-sized polygons. */
export function geometryCentroid(geometry) {
  if (!geometry) return null;
  if (geometry.type === "Polygon") return ringCentroid(geometry.coordinates[0]);
  if (geometry.type === "MultiPolygon") {
    let best = null;
    let bestLen = -1;
    for (const poly of geometry.coordinates) {
      if (poly[0].length > bestLen) {
        bestLen = poly[0].length;
        best = poly[0];
      }
    }
    return best ? ringCentroid(best) : null;
  }
  return null;
}

/** { field_id, zone_id, distance_km, inside } or null when no field is within MAX_MATCH_KM. */
export function matchField(lon, lat, fields) {
  let best = null;
  for (const feature of fields?.features || []) {
    const props = feature.properties || {};
    if (geometryContains(feature.geometry, lon, lat)) {
      return { field_id: props.field_id, zone_id: props.zone_id ?? null, distance_km: 0, inside: true };
    }
    const c = geometryCentroid(feature.geometry);
    if (!c) continue;
    const km = haversineKm(lon, lat, c[0], c[1]);
    if (!best || km < best.distance_km) {
      best = { field_id: props.field_id, zone_id: props.zone_id ?? null, distance_km: Math.round(km * 100) / 100, inside: false };
    }
  }
  return best && best.distance_km <= MAX_MATCH_KM ? best : null;
}

/** "42.03, -93.62" (lat, lon) to [lon, lat]; null when the text is not two numbers. */
export function parseLatLon(text) {
  const m = String(text || "")
    .trim()
    .match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return [lon, lat];
}

/** One Nominatim call: { lon, lat, display_name } or null. */
export async function geocode(query) {
  const url = `${NOMINATIM}?format=json&limit=1&q=${encodeURIComponent(String(query || "").trim())}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`The address lookup failed (${res.status}).`);
  const hits = await res.json();
  const hit = Array.isArray(hits) ? hits[0] : null;
  if (!hit) return null;
  const lon = Number(hit.lon);
  const lat = Number(hit.lat);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  return { lon, lat, display_name: String(hit.display_name || "") };
}
