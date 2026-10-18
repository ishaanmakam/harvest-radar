"""Soil and terrain per field from two free services: the auxiliary inputs the YieldSAT benchmark pairs with Sentinel-2.

    python intelligence/field_env.py --data data/out            # one SoilGrids query per field, elevations in batches

SoilGrids 2.0 (ISRIC, no key): organic carbon, clay, sand and pH over 0-30 cm, depth-weighted from the 0-5, 5-15 and
15-30 cm layers, converted from the mapped integer units with the d_factor the service reports. Open-Meteo elevation
(no key, Copernicus DEM): height at the field centroid and a slope from a 100 m stencil (north, south, east, west).
Responses are cached per field under data/cache/field_env/ so a rerun costs nothing; a field whose lookup fails keeps
None values and a note, never costing the others. Written to field_env.json (additive, optional) for yield_model.py,
the Outlook words and the map.
"""
import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, read_json, write_json  # noqa: E402
from providers import _urllib_http  # noqa: E402

SOILGRIDS = "https://rest.isric.org/soilgrids/v2.0/properties/query"
ELEVATION = "https://api.open-meteo.com/v1/elevation"
PROPERTIES = ("soc", "clay", "sand", "phh2o")
DEPTHS = ("0-5cm", "5-15cm", "15-30cm")
STENCIL_M = 100.0                 # half-width of the slope stencil
M_PER_DEG_LAT = 111_320.0
ELEVATION_BATCH = 100             # points per elevation request (the service's limit)
SOIL_KEYS = ("soc_g_kg", "clay_pct", "sand_pct", "ph", "texture")
SOURCES = ["SoilGrids 2.0 (ISRIC) properties/query, 0-30 cm depth-weighted means",
           "Open-Meteo elevation API (Copernicus DEM 90 m), 100 m slope stencil"]
COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


# ---------------------------------------------------------------- geometry
def centroid(feature):
    """(lon, lat): mean of the outer ring's vertices (a Polygon, or the first polygon of a MultiPolygon)."""
    geom = (feature or {}).get("geometry") or {}
    coords = geom.get("coordinates") or []
    ring = coords[0] if geom.get("type") == "Polygon" else (coords[0][0] if coords and coords[0] else [])
    pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def stencil(lon, lat):
    """Centre, north, south, east, west points STENCIL_M apart."""
    dlat = STENCIL_M / M_PER_DEG_LAT
    dlon = STENCIL_M / (M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    return [(lon, lat), (lon, lat + dlat), (lon, lat - dlat), (lon + dlon, lat), (lon - dlon, lat)]


# ---------------------------------------------------------------- soil
def texture_class(clay, sand):
    """USDA texture class from clay and sand percent (silt is the remainder); None when either is unknown."""
    if clay is None or sand is None:
        return None
    silt = max(0.0, 100.0 - clay - sand)
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50 and clay < 27:
        return "silt loam"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    if clay >= 40:
        return "clay"
    return "loam"


def _weighted(layer):
    """Depth-weighted mean of a SoilGrids layer's reported depths (weight = layer thickness), in target units."""
    total, weight = 0.0, 0.0
    d_factor = float(((layer.get("unit_measure") or {}).get("d_factor")) or 1.0)
    for depth in layer.get("depths") or []:
        v = (depth.get("values") or {}).get("mean")
        rng = depth.get("range") or {}
        try:
            thick = float(rng.get("bottom_depth", 0)) - float(rng.get("top_depth", 0))
        except (TypeError, ValueError):
            thick = 0.0
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and thick > 0:
            total += v * thick
            weight += thick
    return (total / weight / d_factor) if weight else None


def parse_soilgrids(doc):
    out = {k: None for k in SOIL_KEYS}
    layers = ((doc or {}).get("properties") or {}).get("layers") if isinstance(doc, dict) else None
    by_name = {l.get("name"): l for l in (layers or []) if isinstance(l, dict)}
    soc, clay, sand, ph = (by_name.get(n) for n in PROPERTIES)
    if soc:
        v = _weighted(soc)
        out["soc_g_kg"] = round(v, 2) if v is not None else None
    if clay:
        v = _weighted(clay)
        out["clay_pct"] = round(v, 2) if v is not None else None
    if sand:
        v = _weighted(sand)
        out["sand_pct"] = round(v, 2) if v is not None else None
    if ph:
        v = _weighted(ph)
        out["ph"] = round(v, 2) if v is not None else None
    out["texture"] = texture_class(out["clay_pct"], out["sand_pct"])
    return out


def fetch_soil(lon, lat, http=None):
    http = http or _urllib_http
    query = [("lon", f"{lon:.4f}"), ("lat", f"{lat:.4f}")] + [("property", p) for p in PROPERTIES] + \
            [("depth", d) for d in DEPTHS] + [("value", "mean")]
    url = SOILGRIDS + "?" + urllib.parse.urlencode(query)
    status, body = http("GET", url, {"Accept": "application/json"}, None, 60)
    if status != 200:
        raise RuntimeError(f"SoilGrids returned HTTP {status}")
    return parse_soilgrids(json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body))


# ---------------------------------------------------------------- terrain
def fetch_elevations(points, http=None, retries=3, retry_sleep_s=1.0):
    """Elevation in metres for a list of (lon, lat), batched; None for every point of a batch that keeps failing
    (one bad batch never costs the others)."""
    http = http or _urllib_http
    out = []
    for i in range(0, len(points), ELEVATION_BATCH):
        batch = points[i:i + ELEVATION_BATCH]
        url = ELEVATION + "?" + urllib.parse.urlencode({"latitude": ",".join(f"{p[1]:.5f}" for p in batch),
                                                        "longitude": ",".join(f"{p[0]:.5f}" for p in batch)}, safe=",")
        vals = None
        for attempt in range(retries):
            try:
                status, body = http("GET", url, {"Accept": "application/json"}, None, 60)
                if status == 200:
                    doc = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body) or {}
                    vals = list(doc.get("elevation") or [])
                    break
            except Exception:
                pass
            if retry_sleep_s:
                time.sleep(retry_sleep_s * (attempt + 1))
        if vals is None:
            out += [None] * len(batch)
            continue
        vals = vals + [None] * (len(batch) - len(vals))
        out += [float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None for v in vals[:len(batch)]]
    return out


def terrain_from_stencil(z):
    """elevation, slope (%) and the downhill compass direction from [centre, north, south, east, west] heights."""
    c, n, s, e, w = (list(z) + [None] * 5)[:5]
    out = {"elevation_m": round(c, 1) if c is not None else None, "slope_pct": None, "aspect": None}
    if None in (n, s, e, w):
        return out
    gx = (e - w) / (2 * STENCIL_M)            # rises to the east
    gy = (n - s) / (2 * STENCIL_M)            # rises to the north
    slope = math.hypot(gx, gy) * 100
    out["slope_pct"] = round(slope, 2)
    if slope < 0.1:
        out["aspect"] = "flat"
        return out
    bearing = (math.degrees(math.atan2(-gx, -gy)) + 360) % 360      # downhill direction, clockwise from north
    out["aspect"] = COMPASS[int((bearing + 22.5) // 45) % 8]
    return out


# ---------------------------------------------------------------- words
def zone_notes(fields):
    """One plain line per field against its zone: texture, organic carbon above or below the zone, a steeper slope."""
    by_zone = {}
    for fid, f in fields.items():
        by_zone.setdefault(f.get("zone_id"), []).append(f)
    med = {}
    for zid, rows in by_zone.items():
        socs = [r["soc_g_kg"] for r in rows if r.get("soc_g_kg") is not None]
        slopes = [r["slope_pct"] for r in rows if r.get("slope_pct") is not None]
        med[zid] = (statistics.median(socs) if socs else None, statistics.median(slopes) if slopes else None)
    notes = {}
    for fid, f in fields.items():
        parts = []
        if f.get("texture"):
            parts.append(f["texture"][0].upper() + f["texture"][1:])
        soc_med, slope_med = med.get(f.get("zone_id"), (None, None))
        flags = []
        if f.get("soc_g_kg") is not None and soc_med:
            if f["soc_g_kg"] < 0.8 * soc_med:
                flags.append(f"organic carbon below the zone ({f['soc_g_kg']:.0f} vs {soc_med:.0f} g/kg)")
            elif f["soc_g_kg"] > 1.2 * soc_med:
                flags.append(f"organic carbon above the zone ({f['soc_g_kg']:.0f} vs {soc_med:.0f} g/kg)")
        if f.get("slope_pct") is not None and slope_med is not None and f["slope_pct"] >= 3 and f["slope_pct"] > 2 * slope_med:
            flags.append(f"steeper than the zone ({f['slope_pct']:.1f}% slope{', facing ' + f['aspect'] if f.get('aspect') else ''})")
        if flags:
            parts.append("; ".join(flags))
        elif parts:
            parts.append("typical for the zone")
        notes[fid] = "; ".join(parts) if parts else ""
    return notes


# ---------------------------------------------------------------- run
def _cache_path(cache_dir, fid):
    return Path(cache_dir) / f"{fid}.json"


def run(data_dir, cache_dir=None, http=None, fetched_at=None, limit=None, sleep_s=0.0, write=True, retry_sleep_s=1.0):
    data_dir = Path(data_dir)
    cache_dir = Path(cache_dir) if cache_dir else ROOT / "data" / "cache" / "field_env"
    cache_dir.mkdir(parents=True, exist_ok=True)
    http = http or _urllib_http
    feats = read_json(data_dir, "fields.geojson").get("features") or []
    if limit:
        feats = feats[:limit]
    fields, notes_extra, pending_terrain = {}, {}, []
    for feat in feats:
        props = feat.get("properties") or {}
        fid = props.get("field_id")
        c = centroid(feat)
        if not fid or c is None:
            continue
        cached = {}
        cp = _cache_path(cache_dir, fid)
        if cp.is_file():
            try:
                cached = json.loads(cp.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                cached = {}
        soil = cached.get("soil")
        if not soil:
            try:
                soil = fetch_soil(c[0], c[1], http=http)
                cached["soil"] = soil
                cp.write_text(json.dumps(cached), encoding="utf-8")
                if sleep_s:
                    time.sleep(sleep_s)
            except Exception as e:  # keep going: this field gets None values and a note
                soil = {k: None for k in SOIL_KEYS}
                notes_extra[fid] = "soil data not available for this field yet"
        terrain = cached.get("terrain")
        if not terrain:
            pending_terrain.append((fid, c))
        fields[fid] = {"zone_id": props.get("zone_id"), "lon": round(c[0], 5), "lat": round(c[1], 5), **soil,
                       **(terrain or {"elevation_m": None, "slope_pct": None, "aspect": None})}
    if pending_terrain:
        points = [p for _, c in pending_terrain for p in stencil(c[0], c[1])]
        heights = fetch_elevations(points, http=http, retry_sleep_s=retry_sleep_s)
        for i, (fid, _) in enumerate(pending_terrain):
            terrain = terrain_from_stencil(heights[i * 5:(i + 1) * 5])
            fields[fid].update(terrain)
            if terrain["elevation_m"] is None:
                notes_extra[fid] = (notes_extra.get(fid, "") + "; " if notes_extra.get(fid) else "") + "elevation not available for this field yet"
            if terrain["elevation_m"] is not None:
                cp = _cache_path(cache_dir, fid)
                try:
                    cached = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
                except (ValueError, OSError):
                    cached = {}
                cached["terrain"] = terrain
                cp.write_text(json.dumps(cached), encoding="utf-8")
    notes = zone_notes(fields)
    for fid, f in fields.items():
        f["note"] = "; ".join(x for x in (notes.get(fid, ""), notes_extra.get(fid, "")) if x) or "no soil or terrain data"
    landscape = {}
    for key in ("soc_g_kg", "clay_pct", "sand_pct", "ph", "elevation_m", "slope_pct"):
        vals = [f[key] for f in fields.values() if f.get(key) is not None]
        landscape[f"median_{key}"] = round(statistics.median(vals), 2) if vals else None
    landscape["textures"] = dict(sorted(((t, sum(1 for f in fields.values() if f.get("texture") == t))
                                         for t in {f.get("texture") for f in fields.values() if f.get("texture")}), key=lambda kv: -kv[1]))
    landscape["fields"] = len(fields)
    zones = {}
    for zid in sorted({f.get("zone_id") for f in fields.values() if f.get("zone_id")}):
        rows = [f for f in fields.values() if f.get("zone_id") == zid]
        zones[zid] = {k: (round(statistics.median([r[k] for r in rows if r.get(k) is not None]), 2)
                          if any(r.get(k) is not None for r in rows) else None) for k in ("soc_g_kg", "clay_pct", "slope_pct")}
        zones[zid]["fields"] = len(rows)
    doc = {"fetched_at": fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "sources": list(SOURCES),
           "fields": fields, "landscape": landscape, "zones": zones}
    if write:
        write_json(data_dir, "field_env.json", doc)
    return doc


def load_field_env(data_dir):
    """The file as a dict, or None when absent, unreadable or without a fields block."""
    p = Path(data_dir) / "field_env.json"
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("fields"), dict) else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Soil and terrain per field (SoilGrids + elevation) -> field_env.json")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.25, help="seconds between SoilGrids queries (be polite)")
    a = ap.parse_args()
    out = run(a.data, limit=a.limit, sleep_s=a.sleep)
    print(f"  {out['landscape']['fields']} fields -> {Path(a.data) / 'field_env.json'}; textures {out['landscape']['textures']}")
