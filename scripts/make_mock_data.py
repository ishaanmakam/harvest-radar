#!/usr/bin/env python3
"""Generate realistic MOCK data for every pipeline output so the web app and the
intelligence layer can be built before real satellite data exists.

Writes to data/mock/: fields.geojson, zones.geojson, timeseries.json, weather.json,
fires.json, frames/*.jpg + frames.json, insights.json, and truth.json (the ground truth the
mock world was generated from, so intelligence/evaluate.py can score the model).
Then run `python intelligence/run_all.py --data data/mock` to produce status/demand/routing.

Deterministic (seeded) so everyone sees the same mock world.
"""
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "mock"
REGION = json.loads((ROOT / "config" / "region.json").read_text())

rng = random.Random(42)
np_rng = np.random.default_rng(42)

W, S, E, N = REGION["bbox"]
AS_OF = date.fromisoformat(REGION["as_of"])
SEASON_START = date.fromisoformat(REGION["season_start"])
GX, GY = REGION["zones_grid"]
LAT0 = (S + N) / 2
M_PER_DEG_LAT = 110_574
M_PER_DEG_LON = 111_320 * math.cos(math.radians(LAT0))


def d2s(d):
    return d.isoformat()


def zone_of(lon, lat):
    i = min(GX - 1, int((lon - W) / (E - W) * GX))
    j = min(GY - 1, int((N - lat) / (N - S) * GY))
    return f"Z{j * GX + i + 1:02d}"


def logistic(x):
    return 1 / (1 + math.exp(-x))


# ---------------- zones ----------------
zones = []
zone_meta = {}
for j in range(GY):
    for i in range(GX):
        zid = f"Z{j * GX + i + 1:02d}"
        x0 = W + (E - W) * i / GX
        x1 = W + (E - W) * (i + 1) / GX
        y1 = N - (N - S) * j / GY
        y0 = N - (N - S) * (j + 1) / GY
        c = [round((x0 + x1) / 2, 5), round((y0 + y1) / 2, 5)]
        name = f"Zone {chr(65 + j)}{i + 1}"
        zones.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
            "properties": {"zone_id": zid, "name": name, "centroid": c},
        })
        # village "core" near the centre (no fields), and a zone-level harvest timing
        zone_meta[zid] = {
            "village": (c[0] + rng.uniform(-0.005, 0.005), c[1] + rng.uniform(-0.005, 0.005)),
            "harvest_mean": date(2026, 10, 3) + timedelta(days=int(3 * (j * GX + i)) + rng.randint(-2, 2)),
            "burn_prob": rng.uniform(0.15, 0.45),
        }

# ---------------- fields ----------------
fields = []
truth = {}
row_h = 0.0022  # ~245 m
lat = N - 0.0004
fid_n = 0
while lat - row_h > S + 0.0004:
    lon = W + 0.0004
    while lon < E - 0.0004:
        w = rng.uniform(0.0012, 0.0022)
        x0, x1 = lon, min(lon + w, E - 0.0004)
        y1, y0 = lat, lat - row_h + 0.00012  # small gap = field bunds / tracks
        lon = x1 + 0.00010
        if x1 - x0 < 0.0006:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        zid = zone_of(cx, cy)
        vx, vy = zone_meta[zid]["village"]
        if math.hypot((cx - vx) * M_PER_DEG_LON, (cy - vy) * M_PER_DEG_LAT) < 330:
            continue  # village core
        if rng.random() < 0.05:
            continue  # orchard / fallow / road
        fid_n += 1
        fid = f"F{fid_n:04d}"
        area = (x1 - x0) * M_PER_DEG_LON * (y1 - y0) * M_PER_DEG_LAT / 10_000
        crop = "rice" if rng.random() < 0.92 else "other"
        fields.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[round(x0, 6), round(y0, 6)], [round(x1, 6), round(y0, 6)],
                                                             [round(x1, 6), round(y1, 6)], [round(x0, 6), round(y1, 6)],
                                                             [round(x0, 6), round(y0, 6)]]]},
            "properties": {"field_id": fid, "zone_id": zid, "area_ha": round(area, 2), "crop": crop, "source": "mock"},
        })
        zm = zone_meta[zid]
        harvest = zm["harvest_mean"] + timedelta(days=round(np_rng.normal(0, 6)))
        peak = harvest - timedelta(days=rng.randint(36, 44))
        burned = harvest <= AS_OF - timedelta(days=2) and rng.random() < zm["burn_prob"]
        burn_date = harvest + timedelta(days=rng.randint(1, 4)) if burned else None
        if burn_date and burn_date > AS_OF:
            burned, burn_date = False, None
        sown = (not burned) and harvest + timedelta(days=16) <= AS_OF and rng.random() < 0.6
        truth[fid] = dict(crop=crop, peak=peak, harvest=harvest, peak_ndvi=rng.uniform(0.72, 0.84),
                          burned=burned, burn_date=burn_date, sown=sown, cx=cx, cy=cy)
    lat -= row_h

# ---------------- satellite pass dates + clouds ----------------
dates = []
d = SEASON_START + timedelta(days=2)
while d <= AS_OF - timedelta(days=1):
    dates.append(d)
    d += timedelta(days=5)
scene_clear = {}
for d in dates:
    monsoon = date(2026, 7, 1) <= d <= date(2026, 9, 10)
    scene_clear[d] = rng.random() > (0.6 if monsoon else 0.12)


def ndvi_model(t, d):
    if t["crop"] != "rice":
        return 0.35 + 0.1 * math.sin((d - SEASON_START).days / 20)
    transplant = t["peak"] - timedelta(days=58)
    if d < transplant:
        return 0.16
    if d >= t["harvest"]:
        if t["burned"] and t["burn_date"] and d >= t["burn_date"]:
            return 0.10
        if t["sown"] and d >= t["harvest"] + timedelta(days=16):
            return min(0.42, 0.22 + 0.012 * (d - t["harvest"] - timedelta(days=16)).days)
        return 0.22
    x = (d - transplant).days
    up = 0.16 + (t["peak_ndvi"] - 0.16) * logistic((x - 30) / 6)
    if d > t["peak"]:
        frac = (d - t["peak"]).days / max(1, (t["harvest"] - t["peak"]).days)
        up = t["peak_ndvi"] - 0.25 * frac ** 1.6
    return up


def nbr_model(t, d, ndvi):
    if t["burned"] and t["burn_date"] and t["burn_date"] <= d < t["burn_date"] + timedelta(days=25):
        return -0.22
    if d >= t["harvest"] and not (t["sown"] and d >= t["harvest"] + timedelta(days=20)):
        return 0.08
    return 0.62 * ndvi - 0.05


ts_fields = {}
for f in fields:
    fid = f["properties"]["field_id"]
    t = truth[fid]
    nd, nb, cf = [], [], []
    for d in dates:
        clear = scene_clear[d] and rng.random() > 0.06
        frac = round(rng.uniform(0.7, 1.0), 2) if clear else round(rng.uniform(0.0, 0.4), 2)
        if clear:
            v = ndvi_model(t, d) + np_rng.normal(0, 0.025)
            nd.append(round(float(v), 3))
            nb.append(round(float(nbr_model(t, d, v) + np_rng.normal(0, 0.03)), 3))
        else:
            nd.append(None)
            nb.append(None)
        cf.append(frac)
    ts_fields[fid] = {"ndvi": nd, "nbr": nb, "clear_frac": cf}

# ---------------- weather ----------------
obs_dates, precip, tmax, tmin = [], [], [], []
d = SEASON_START
while d < AS_OF:
    monsoon = date(2026, 7, 1) <= d <= date(2026, 9, 15)
    rain = np_rng.gamma(0.8, 14) if monsoon and rng.random() < 0.45 else (np_rng.gamma(0.6, 5) if rng.random() < 0.06 else 0.0)
    doy = (d - date(2026, 1, 1)).days
    base = 33 - 8 * max(0, (doy - 240) / 60)
    obs_dates.append(d2s(d))
    precip.append(round(float(rain), 1))
    tmax.append(round(base + np_rng.normal(0, 1.5), 1))
    tmin.append(round(base - 10 + np_rng.normal(0, 1.5), 1))
    d += timedelta(days=1)
fc_dates = [d2s(AS_OF + timedelta(days=k)) for k in range(14)]
fc_precip = [0.0] * 14
fc_prob = [5] * 14
fc_precip[5], fc_prob[5] = 14.0, 65  # a rain event ~5 days out -> urgency
fc_precip[6], fc_prob[6] = 6.0, 55
weather = {
    "observed": {"source": "mock", "dates": obs_dates, "precip_mm": precip, "tmax_c": tmax, "tmin_c": tmin},
    "forecast": {"source": "mock", "dates": fc_dates, "precip_mm": fc_precip, "precip_prob": fc_prob},
}

# ---------------- fires ----------------
detections = []
for fid, t in truth.items():
    if t["burned"] and rng.random() < 0.55:  # satellites miss many small/timed burns
        detections.append({
            "lon": round(t["cx"] + rng.uniform(-0.0003, 0.0003), 5),
            "lat": round(t["cy"] + rng.uniform(-0.0003, 0.0003), 5),
            "date": d2s(t["burn_date"]), "time_utc": rng.choice(["0805", "0820", "2010"]),
            "satellite": rng.choice(["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]),
            "confidence": rng.choice(["n", "h"]), "frp_mw": round(rng.uniform(1.5, 9.0), 1), "field_id": fid,
        })
fires = {"source": "mock", "detections": detections}

# ---------------- frames (rendered "satellite" images) ----------------
(OUT / "frames").mkdir(parents=True, exist_ok=True)
IMG_H = 800
IMG_W = int(IMG_H * (E - W) * M_PER_DEG_LON / ((N - S) * M_PER_DEG_LAT))


def px(lon, lat):
    return ((lon - W) / (E - W) * IMG_W, (N - lat) / (N - S) * IMG_H)


def ndvi_rgb(v):
    if v is None:
        return (200, 200, 205)
    v = max(0.0, min(0.9, v))
    soil = np.array([150, 125, 95])
    veg = np.array([45, 110, 40])
    a = min(1, max(0, (v - 0.12) / 0.65))
    return tuple(int(x) for x in soil * (1 - a) + veg * a)


frames = []
for k, d in enumerate(dates):
    if not scene_clear[d]:
        continue
    img = Image.new("RGB", (IMG_W, IMG_H), (138, 120, 96))
    dr = ImageDraw.Draw(img)
    for zid, zm in zone_meta.items():
        vx, vy = px(*zm["village"])
        dr.ellipse([vx - 22, vy - 22, vx + 22, vy + 22], fill=(150, 145, 140))
    for f in fields:
        fid = f["properties"]["field_id"]
        v = ts_fields[fid]["ndvi"][k]
        col = (35, 32, 30) if (v is not None and ts_fields[fid]["nbr"][k] is not None and ts_fields[fid]["nbr"][k] < -0.1) else ndvi_rgb(v)
        ring = f["geometry"]["coordinates"][0]
        dr.polygon([px(*p) for p in ring], fill=col)
    dr.rectangle([8, 8, 190, 34], fill=(0, 0, 0))
    dr.text((14, 14), f"MOCK  {d2s(d)}", fill=(255, 255, 255))
    name = f"frames/{d2s(d)}_rgb.jpg"
    img.save(OUT / name, quality=82)
    frames.append({"date": d2s(d), "rgb": name, "ndvi": None, "clear_frac": 0.95})
frames_doc = {"bounds": [W, S, E, N], "width": IMG_W, "height": IMG_H, "timelapse": None, "frames": frames}

# ---------------- mock Perceptron insights ----------------
items = []
for z in zones:
    zid = z["properties"]["zone_id"]
    zf = [fid for fid, t in truth.items() if zone_of(t["cx"], t["cy"]) == zid]
    h = sum(1 for fid in zf if truth[fid]["harvest"] <= AS_OF)
    b = sum(1 for fid in zf if truth[fid]["burned"])
    items.append({
        "zone_id": zid, "kind": "timelapse",
        "summary": f"MOCK: {h} of {len(zf)} fields changed from green to bare since late September; {b} show dark burn scars.",
        "counts": {"harvested": h, "standing": len(zf) - h, "burned": b},
        "clips": [{"start": "2026-09-28", "end": d2s(AS_OF), "label": "harvest wave moving through zone"}],
    })
insights = {"model": "mock", "spent_usd": 0, "items": items}

# ---------------- write ----------------
OUT.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    with open(OUT / name, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"))


dump("fields.geojson", {"type": "FeatureCollection", "features": fields})
dump("zones.geojson", {"type": "FeatureCollection", "features": zones})
dump("timeseries.json", {"source": "mock", "dates": [d2s(d) for d in dates], "fields": ts_fields})
dump("weather.json", weather)
dump("fires.json", fires)
dump("frames/frames.json", frames_doc)
dump("insights.json", insights)
dump("truth.json", {
    "source": "mock", "as_of": d2s(AS_OF),
    "note": "Ground truth the mock world was generated from. Mock only; harvest_date can be after as_of (not yet harvested).",
    "fields": {fid: {"crop": t["crop"], "peak_date": d2s(t["peak"]), "peak_ndvi": round(t["peak_ndvi"], 3),
                     "harvest_date": d2s(t["harvest"]), "burned": t["burned"],
                     "burn_date": d2s(t["burn_date"]) if t["burn_date"] else None, "sown": t["sown"]}
               for fid, t in truth.items()},
})
print(f"mock: {len(fields)} fields, {len(dates)} passes ({len(frames)} clear frames), {len(detections)} fire detections -> {OUT}")
