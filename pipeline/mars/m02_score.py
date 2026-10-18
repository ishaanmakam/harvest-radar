"""M2: score every 0.125 degree cell of Mars between 60 S and 60 N, pick ten sites, grade SpaceX's six 2019 candidates.

    python pipeline/mars/m02_score.py

Reads the cached layers m01 wrote (MOLA, SWIM 0-1 m and 1-5 m, TES thermal inertia, TES albedo, USGS geology) and
pipeline/mars/config.json (every cutoff and weight). Four checks per cell, each go / watch / no_go with a 0..1 goodness;
a cell's status is its worst check, as a farm's flag is its worst check on Earth; the score is the weighted mean of
goodness. Refuses to run without the ice layer. Writes data/out/mars/sites.json after validating it against
contracts/mars_sites.schema.json, and the grids m03 draws from to data/cache/mars/grid.npz.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.mars import CACHE, CONTRACTS, MARS_SPHERE_CRS, OUT, bearing_deg, compass, config, great_circle_km, nearest_region, write_json_atomic  # noqa: E402
from pipeline.mars.m01_fetch import unit_age  # noqa: E402

CFG = config()
G = CFG["grid"]
CELL = G["cell_deg"]
LAT0, LAT1, LON0, LON1 = G["lat_min"], G["lat_max"], G["lon_min"], G["lon_max"]
NROW, NCOL = int(round((LAT1 - LAT0) / CELL)), int(round((LON1 - LON0) / CELL))
KM_DEG = CFG["km_per_degree_latitude"]
TRANSFORM = from_origin(LON0, LAT1, CELL, CELL)
SOURCES = [
    {"name": "MOLA MEGDR 16 pixels per degree", "url": "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg016/megt90n000eb.img",
     "citation": "Smith et al., MGS MOLA Mission Experiment Gridded Data Record, NASA PDS Geosciences Node", "terms": "NASA data, public domain"},
    {"name": "SWIM 2025 (SWIM4MIM) ice consistency maps", "url": "https://swim.psi.edu/SWIM4MIMProducts.php",
     "citation": "Subsurface Water Ice Mapping project, Planetary Science Institute, NASA-funded", "terms": "Public products from the SWIM project pages; cite the SWIM team (PSI)"},
    {"name": "MGS TES nighttime thermal inertia, 20 pixels per degree", "url": "https://se.psi.edu/inertia/nmap2003.bin",
     "citation": "Putzig, Mellon, Kretke and Arvidson, Icarus 173, 2005", "terms": "Public map from the authors (PSI); cite the paper"},
    {"name": "MGS TES albedo mosaic 7410 m", "url": "https://planetarymaps.usgs.gov/mosaic/Mars_MGS_TES_Albedo_mosaic_global_7410m.tif",
     "citation": "USGS Astrogeology Science Center", "terms": "USGS data, public domain"},
    {"name": "USGS Mars Global Geologic Map, SIM 3292", "url": "https://astrogeology.usgs.gov/pygeoapi/collections/mars/sim3292_global_geologic_map/units",
     "citation": "Tanaka et al. 2014, USGS Scientific Investigations Map 3292", "terms": "USGS data, public domain"},
    {"name": "HiRISE candidate landing site pages", "url": "https://hirise.lpl.arizona.edu/ESP_060706_2195",
     "citation": "NASA/JPL/University of Arizona, 2019", "terms": "Public web pages; NASA imagery is public domain"},
    {"name": "Space.com, SpaceX Studying Landing Sites for Mars Missions", "url": "https://www.space.com/36142-spacex-studying-mars-landing-sites.html",
     "citation": "Space.com, March 2017 (Paul Wooster's stated criteria)", "terms": "News article, cited for the criteria only"},
]
BLIND_SPOTS = [
    "no sunlight or power factor (SpaceX screens for it; we leave it to them)",
    "no rock-hazard factor at landing scale (HiRISE work)",
    "7 km cells are far coarser than a landing ellipse",
    "SWIM covers 60 S to 60 N only",
    "thermal inertia is a regional average; local boulders are invisible at this scale",
    "about 15 percent of the thermal inertia map between 60 S and 60 N is the authors' interpolated fill (TES mask); those cells are flagged in the detail text",
]


# ---------------------------------------------------------------- layers on the grid
def on_grid(path, resampling=Resampling.average, dtype="float32"):
    """Any cached raster resampled onto the scoring grid (degrees, east, north up); nodata becomes NaN."""
    with rasterio.open(path) as src:
        data = src.read(1).astype("float64")
        nod = src.nodata
        if nod is not None and not (isinstance(nod, float) and math.isnan(nod)):
            data[data == nod] = np.nan
        out = np.full((NROW, NCOL), np.nan, dtype="float32")
        reproject(data.astype("float32"), out, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=TRANSFORM, dst_crs=MARS_SPHERE_CRS, dst_nodata=np.nan, resampling=resampling)
    return out


def rasterize_units(doc, cell):
    nrow, ncol = int(round(180 / cell)), int(round(360 / cell))
    units = sorted({f["properties"]["unit"] for f in doc["features"] if f["properties"].get("unit")})
    index = {u: i + 1 for i, u in enumerate(units)}
    shapes = ((f["geometry"], index[f["properties"]["unit"]]) for f in doc["features"] if f["properties"].get("unit"))
    grid = rasterize(shapes, out_shape=(nrow, ncol), transform=from_origin(-180, 90, cell, cell), fill=0, dtype="int32")
    return grid, units


def box_max(mask, half_rows, half_cols_per_row):
    """Dilate a boolean grid with a box: fixed half-height, a half-width that varies by row (longitude wraps)."""
    nrow, ncol = mask.shape
    vert = np.zeros_like(mask)
    for dr in range(-half_rows, half_rows + 1):
        vert |= np.roll(mask, dr, axis=0) if dr else mask
    # rows near the poles pulled in by roll are harmless here: the grid is cut to 60 S..60 N afterwards
    out = np.zeros_like(mask)
    for r in range(nrow):
        hw = half_cols_per_row[r]
        row = vert[r]
        if not row.any():
            continue
        acc = row.copy()
        for dc in range(1, hw + 1):
            acc |= np.roll(row, dc) | np.roll(row, -dc)
        out[r] = acc
    return out


def science_reach(doc, cfg):
    """Distinct units within about reach_km of every coarse cell, and whether an old unit is among them."""
    coarse = cfg["coarse_cell_deg"]
    grid, units = rasterize_units(doc, coarse)
    ages = {u: unit_age(u) for u in units}
    old = {u for u, a in ages.items() if a.startswith("Noachian") or a.startswith("Hesperian")}
    nrow, ncol = grid.shape
    half_rows = max(1, int(round(cfg["reach_km"] / KM_DEG / coarse)))
    lats = 90 - (np.arange(nrow) + 0.5) * coarse
    half_cols = [min(ncol // 2, int(round(cfg["reach_km"] / KM_DEG / coarse / max(0.05, math.cos(math.radians(lat)))))) for lat in lats]
    count = np.zeros(grid.shape, dtype="int16")
    old_near = np.zeros(grid.shape, dtype=bool)
    for u in units:
        mask = grid == (units.index(u) + 1)
        near = box_max(mask, half_rows, half_cols)
        count += near
        if u in old:
            old_near |= near
    # cut to the scoring latitudes and upsample by nearest to the scoring grid
    r0, r1 = int((90 - LAT1) / coarse), int((90 - LAT0) / coarse)
    f = int(round(coarse / CELL))
    count = np.repeat(np.repeat(count[r0:r1], f, axis=0), f, axis=1)
    old_near = np.repeat(np.repeat(old_near[r0:r1], f, axis=0), f, axis=1)
    fine, _ = rasterize_units(doc, CELL)
    fr0, fr1 = int((90 - LAT1) / CELL), int((90 - LAT0) / CELL)
    unit_at = fine[fr0:fr1]
    return count, old_near, unit_at, units, ages


def slope_deg(elev):
    """Regional slope from the gridded elevation: gradient across neighbouring cells (about 15 km), in degrees."""
    lats = LAT1 - (np.arange(NROW) + 0.5) * CELL
    dy_km = CELL * KM_DEG
    dx_km = (CELL * KM_DEG * np.cos(np.radians(lats)))[:, None]
    gy, gx = np.gradient(elev / 1000.0)
    return np.degrees(np.arctan(np.hypot(gx / dx_km, gy / dy_km)))


# ---------------------------------------------------------------- checks
def status_from(go, watch):
    s = np.full(go.shape, 2, dtype="int8")                   # 0 go, 1 watch, 2 no_go
    s[watch] = 1
    s[go] = 0
    return s


def ice_check(c01, c15):
    k = CFG["factors"]["ice"]["cutoffs"]
    go = (c01 >= k["go_c01"]) | (c15 >= k["go_c15"])
    watch = (c01 >= k["watch_c01"]) | (c15 >= k["watch_c15"])
    good = np.maximum((c01 + 1) / 2, k["c15_factor"] * (c15 + 1) / 2)
    return status_from(go, watch), np.clip(good, 0, 1)


def elevation_check(z):
    k = CFG["factors"]["low_elevation"]["cutoffs"]
    go, watch = z <= k["go_m"], z <= k["watch_m"]
    good = np.clip((k["bad_m"] - z) / (k["bad_m"] - k["good_m"]), 0, 1)
    return status_from(go, watch), good


def plume_check(ti):
    k = CFG["factors"]["plume_safe_ground"]["cutoffs"]
    go = (ti >= k["go_lo"]) & (ti <= k["go_hi"])
    watch = (ti >= k["watch_lo"]) & (ti <= k["watch_hi"])
    good = np.where(ti < k["go_lo"], (ti - k["ramp_lo"]) / (k["go_lo"] - k["ramp_lo"]),
                    np.where(ti <= k["go_hi"], 1.0, (k["ramp_hi"] - ti) / (k["ramp_hi"] - k["go_hi"])))
    return status_from(go, watch), np.clip(good, 0, 1)


def science_check(count, old_near):
    k = CFG["factors"]["science_reach"]["cutoffs"]
    go = (count >= k["go_units"]) & old_near
    watch = count >= k["watch_units"]
    good = (1 - k["old_weight"]) * np.minimum(1.0, count / k["units_for_full"]) + k["old_weight"] * old_near
    return status_from(go, watch), np.clip(good, 0, 1)


STATUS_NAME = {0: "go", 1: "watch", 2: "no_go"}


def cell_of(lat, lon):
    r = int((LAT1 - lat) / CELL)
    c = int((lon - LON0) / CELL)
    return min(max(r, 0), NROW - 1), min(max(c, 0), NCOL - 1)


def latlon_of(r, c):
    return round(LAT1 - (r + 0.5) * CELL, 4), round(LON0 + (c + 0.5) * CELL, 4)


# ---------------------------------------------------------------- main
def main():
    print("M2 score")
    swim = {k: next(iter(sorted((CACHE / "swim").glob(f"*{v}*.tif"))), None) for k, v in {"c01": "Ci_0_1", "c15": "Ci_1_5"}.items()}
    if not swim["c01"] or not swim["c15"]:
        sys.exit("  ice layer missing: SWIM 0-1 m and 1-5 m GeoTIFFs are needed in data/cache/mars/swim (python pipeline/mars/m01_fetch.py prints the steps)")
    mola, ti, albedo = CACHE / "mola_16ppd.tif", CACHE / "ti_20ppd.tif", CACHE / "Mars_MGS_TES_Albedo_mosaic_global_7410m.tif"
    geo_path = CACHE / "geologic_units.geojson"
    for p in (mola, ti, albedo):
        if not p.exists():
            sys.exit(f"  {p.name} missing: run python pipeline/mars/m01_fetch.py")
    print("  layers onto the 0.125 degree grid")
    z = on_grid(mola)
    inertia = on_grid(ti)
    alb = on_grid(albedo)
    c01 = on_grid(swim["c01"])
    c15 = on_grid(swim["c15"])
    mask_path = CACHE / "ti_mask_20ppd.tif"
    ti_measured = on_grid(mask_path, Resampling.nearest) if mask_path.exists() else np.ones_like(inertia)
    for name, arr in (("elevation", z), ("thermal inertia", inertia), ("albedo", alb), ("ice 0-1 m", c01), ("ice 1-5 m", c15)):
        print(f"    {name:16} valid {np.isfinite(arr).mean() * 100:5.1f}%  mean {np.nanmean(arr):.3g}")
    geo_ok = geo_path.exists() and geo_path.stat().st_size > 1_000_000
    if geo_ok:
        doc = json.loads(geo_path.read_text(encoding="utf-8"))
        count, old_near, unit_at, units, ages = science_reach(doc, CFG["factors"]["science_reach"]["cutoffs"])
        print(f"    geology: {len(units)} units; reach counts 1..{int(count.max())}, old unit within reach on {old_near.mean() * 100:.1f}% of cells")
    else:
        print("    geology MISSING: science_reach is absent everywhere (not counted as no_go)")
        count = old_near = unit_at = units = ages = None

    print("  checks")
    st_ice, g_ice = ice_check(c01, c15)
    st_elev, g_elev = elevation_check(z)
    st_plume, g_plume = plume_check(inertia)
    checks = {"ice": (st_ice, g_ice, c01), "low_elevation": (st_elev, g_elev, z), "plume_safe_ground": (st_plume, g_plume, inertia)}
    if geo_ok:
        st_sci, g_sci = science_check(count, old_near)
        checks["science_reach"] = (st_sci, g_sci, count.astype("float32"))
    weights = {k: v for k, v in CFG["weights"]["without_magnetic"].items() if k in checks}
    wsum = sum(weights.values())
    weights = {k: v / wsum for k, v in weights.items()}
    valid = np.isfinite(c01) & np.isfinite(c15) & np.isfinite(z) & np.isfinite(inertia)
    status = np.zeros((NROW, NCOL), dtype="int8")
    score = np.zeros((NROW, NCOL), dtype="float32")
    for k, (st, g, _) in checks.items():
        status = np.maximum(status, st)
        score += weights[k] * np.nan_to_num(g, nan=0.0)
    status = np.where(valid, status, 3).astype("int8")      # 3 = no data
    score = np.where(valid, score, np.nan).astype("float32")
    counts = {STATUS_NAME.get(i, "no_data"): int((status == i).sum()) for i in range(4)}
    n = int(valid.sum())
    print(f"    cells scored {n} of {NROW * NCOL}: " + ", ".join(f"{k} {v} ({v / (NROW * NCOL) * 100:.1f}%)" for k, v in counts.items()))
    names = list(checks)
    corr = {}
    stack = np.vstack([np.nan_to_num(checks[k][1][valid], nan=0.0) for k in names])
    cm = np.corrcoef(stack)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if j > i:
                corr[f"{a} vs {b}"] = round(float(cm[i, j]), 3)
                print(f"    corr {a} vs {b}: {cm[i, j]:+.3f}")
    slope = slope_deg(np.nan_to_num(z, nan=np.nanmean(z)))
    lat_grid = (LAT1 - (np.arange(NROW) + 0.5) * CELL)[:, None].repeat(NCOL, axis=1)

    units_txt = {k: {"ice": "consistency 0-1 m", "low_elevation": "m", "plume_safe_ground": "J m^-2 K^-1 s^-1/2", "science_reach": "units within 100 km"}[k] for k in names}

    def detail(k, r, c):
        v = checks[k][2][r, c]
        if k == "ice":
            a, b = c01[r, c], c15[r, c]
            where = "within 1 m" if a >= CFG["factors"]["ice"]["cutoffs"]["go_c01"] else ("1 to 5 m down" if b >= CFG["factors"]["ice"]["cutoffs"]["go_c15"] else "uncertain")
            return f"Ice {where} (consistency {a:+.2f} at 0-1 m, {b:+.2f} at 1-5 m)"
        if k == "low_elevation":
            return f"{v:,.0f} m: {'thick air to brake in' if v <= -2500 else ('below the datum' if v <= 0 else 'thin air, high ground')}"
        if k == "plume_safe_ground":
            kind = "loose dust" if v < 120 else ("firm, packed ground" if 180 <= v <= 500 else ("rock and bedrock" if v > 700 else "mixed ground"))
            fill = "" if ti_measured[r, c] >= 0.5 else " (from the map's interpolated fill, not a direct TES measurement)"
            return f"Thermal inertia {v:.0f}: {kind}{fill}"
        if k == "science_reach":
            return f"{int(v)} rock unit{'s' if v != 1 else ''} within about 100 km" + (", one from the wet early eras" if old_near[r, c] else "")
        return ""

    def place(r, c):
        rows = []
        for k in names:
            st, g, _ = checks[k]
            v = checks[k][2][r, c]
            rows.append({"factor": k, "status": STATUS_NAME[int(st[r, c])], "value": None if not np.isfinite(v) else round(float(v), 3),
                         "unit": units_txt[k], "goodness": None if not np.isfinite(g[r, c]) else round(float(g[r, c]), 3), "detail": detail(k, r, c)})
        lat, lon = latlon_of(r, c)
        geo = {"unit": None, "age": None, "units_within_100km": None, "oldest_within_100km": None}
        if geo_ok:
            uid = int(unit_at[r, c])
            u = units[uid - 1] if uid > 0 else None
            geo = {"unit": u, "age": ages.get(u) if u else None, "units_within_100km": int(count[r, c]),
                   "oldest_within_100km": "Noachian or Hesperian" if old_near[r, c] else "Amazonian only"}
        return {"lat": lat, "lon": lon, "status": STATUS_NAME.get(int(status[r, c]), "no_data"), "score": round(float(score[r, c]), 3) if np.isfinite(score[r, c]) else 0.0,
                "checks": rows, "context": {"slope_deg": round(float(slope[r, c]), 2), "abs_latitude": round(abs(lat), 2),
                                            "albedo": None if not np.isfinite(alb[r, c]) else round(float(alb[r, c]), 3)}, "geology": geo}

    def reason(p):
        ok = [x for x in p["checks"] if x["goodness"] is not None]
        best = max(ok, key=lambda x: x["goodness"])
        worst = min(ok, key=lambda x: x["goodness"])
        plus = {"ice": "ice within reach", "low_elevation": "thick air to brake in", "plume_safe_ground": "firm ground", "science_reach": "many rock types within reach"}
        minus = {"ice": "ice is uncertain", "low_elevation": "high ground", "plume_safe_ground": "loose dust underfoot" if worst["value"] is not None and worst["value"] < 180 else "rocky ground",
                 "science_reach": "few rock types within reach"}
        return f"{plus[best['factor']].capitalize()}; {minus[worst['factor']]}."

    print("  top sites")
    order = np.lexsort((-np.nan_to_num(score, nan=-1).ravel(), status.ravel()))
    chosen = []
    k = CFG["sites"]
    for idx in order:
        r, c = divmod(int(idx), NCOL)
        if status[r, c] > 1:
            break
        lat, lon = latlon_of(r, c)
        if all(great_circle_km(lat, lon, s[0], s[1]) >= k["min_separation_km"] for s in chosen):
            chosen.append((lat, lon, r, c))
        if len(chosen) >= k["count"]:
            break
    cands = json.loads((CACHE / "spacex_candidates.json").read_text(encoding="utf-8"))
    sites = []
    for i, (lat, lon, r, c) in enumerate(chosen, start=1):
        p = place(r, c)
        near = min(((cd["id"], great_circle_km(lat, lon, cd["lat"], cd["lon"])) for cd in cands if cd.get("lat") is not None), key=lambda x: x[1])
        region = nearest_region(lat, lon, CFG.get("regions", []))
        p.update({"id": f"M{i:02d}", "rank": i, "reason": reason(p), "nearest_spacex": {"id": near[0], "km": round(near[1])},
                  "region": region["name"] if region else None, "thumb": f"thumbs/M{i:02d}.jpg", "thumb_source": None})
        sites.append(p)
        print(f"    M{i:02d} {lat:7.3f} {lon:8.3f} {p['status']:5} {p['score']:.3f}  {p['region']}: {p['reason']}")

    print("  SpaceX candidates")
    scored_scores = np.sort(score[valid])
    spacex = []
    for cd in cands:
        if cd.get("lat") is None:
            spacex.append({**cd, "missing": True, "status": None, "score": None, "percentile": None, "checks": [], "context": {}, "geology": {}, "best_within_200km": None, "thumb": None, "thumb_source": None})
            continue
        r, c = cell_of(cd["lat"], cd["lon"])
        p = place(r, c)
        pct = round(float(np.searchsorted(scored_scores, score[r, c]) / max(1, len(scored_scores)) * 100), 1) if np.isfinite(score[r, c]) else None
        half = int(k["best_within_km"] / KM_DEG / CELL) + 1
        best = None
        for rr in range(max(0, r - half), min(NROW, r + half + 1)):
            cosl = max(0.05, math.cos(math.radians(LAT1 - (rr + 0.5) * CELL)))
            hc = int(half / cosl) + 1
            for cc in range(c - hc, c + hc + 1):
                cm_ = cc % NCOL
                if status[rr, cm_] > 1:
                    continue
                la, lo = latlon_of(rr, cm_)
                d = great_circle_km(cd["lat"], cd["lon"], la, lo)
                if d > k["best_within_km"]:
                    continue
                key = (int(status[rr, cm_]), -float(score[rr, cm_]))
                if best is None or key < best[0]:
                    best = (key, {"lat": la, "lon": lo, "status": STATUS_NAME[int(status[rr, cm_])], "score": round(float(score[rr, cm_]), 3),
                                  "km": round(d), "bearing": compass(bearing_deg(cd["lat"], cd["lon"], la, lo))})
        for key_ in ("lat", "lon"):
            p[key_] = round(float(cd[key_]), 4)
        entry = {"id": cd["id"], "name": cd.get("name"), "region": cd["region"], "url": cd["url"], "acquired": cd.get("acquired"), **p,
                 "percentile": pct, "best_within_200km": best[1] if best else None, "thumb": f"thumbs/{cd['id']}.jpg", "thumb_source": None, "missing": False}
        spacex.append(entry)
        print(f"    {cd['id']} {p['status']:5} score {p['score']:.3f} pct {pct} | " + "; ".join(f"{x['factor']} {x['status']} {x['value']}" for x in p['checks'])
              + (f" | best within 200 km: {best[1]['status']} {best[1]['score']} {best[1]['km']} km {best[1]['bearing']}" if best else ""))
    graded = [s for s in spacex if not s.get("missing")]
    by = {}
    for f in names:
        by[f] = {"go": sum(1 for s in graded for x in s["checks"] if x["factor"] == f and x["status"] == "go"),
                 "no_go": sum(1 for s in graded for x in s["checks"] if x["factor"] == f and x["status"] == "no_go")}
    label = {"ice": "the ice check", "low_elevation": "the low-ground check", "plume_safe_ground": "our plume check", "science_reach": "our science-reach check"}
    parts = []
    for f in names:
        g, ng = by[f]["go"], by[f]["no_go"]
        if g == len(graded):
            parts.append(f"all {len(graded)} pass {label[f]}")
        elif ng:
            parts.append(f"{ng} of {len(graded)} fail {label[f]}")
        else:
            parts.append(f"{g} of {len(graded)} pass {label[f]} outright")
    overall = {s: sum(1 for x in graded if x["status"] == s) for s in ("go", "watch", "no_go")}
    summary = (f"Of SpaceX's {len(graded)} 2019 sites, {overall['go']} {'lands' if overall['go'] == 1 else 'land'} in the go band, {overall['watch']} in watch and {overall['no_go']} in no-go on our screen: "
               + "; ".join(parts) + ".")
    print("    " + summary)

    # trade-off table
    both = (status <= 1) & valid
    xs = g_ice[both]
    ours = np.nanmean(np.vstack([g_plume[both]] + ([g_sci[both]] if geo_ok else [])), axis=0)
    nb = CFG["tradeoff_bins"]
    hist, _, _ = np.histogram2d(xs, ours, bins=nb, range=[[0, 1], [0, 1]])
    g_ours_full = np.nanmean(np.vstack([g_plume[None]] + ([g_sci[None]] if geo_ok else [])), axis=0)
    points = [{"id": s["id"], "x": round(float(g_ice[cell_of(s["lat"], s["lon"])]), 3), "y": round(float(g_ours_full[cell_of(s["lat"], s["lon"])]), 3), "kind": "ours"} for s in sites]
    points += [{"id": s["id"], "x": round(float(g_ice[cell_of(s["lat"], s["lon"])]), 3), "y": round(float(g_ours_full[cell_of(s["lat"], s["lon"])]), 3), "kind": "spacex"} for s in graded]
    tradeoff = {"x": "ice goodness", "y": "our factors goodness (plume-safe ground and science reach)", "bins": nb,
                "counts": [[int(v) for v in row] for row in hist], "points": points}

    factors = []
    for fid, f in CFG["factors"].items():
        if fid not in checks:
            continue
        factors.append({"id": fid, "name": f["name"], "from": f["from"], "dataset": f["dataset"], "why": f["why"], "go": f["go"], "watch": f["watch"],
                        "no_go": f["no_go"], "goodness_rule": f["goodness_rule"], "weight": round(weights[fid], 3)})
    method = (f"Every {CELL} degree cell between {LAT0} and {LAT1} degrees latitude (about 7 km) gets four checks from public orbital data: ice within "
              "reach, low ground, plume-safe ground and science reach, all four set by us from the public record. Each check is go, watch or no-go with a "
              "0 to 1 goodness; a cell's status is its worst check, the same rule as a farm's flag on Earth; the score is the weighted mean of goodness "
              f"({', '.join(f'{k} {v:.2f}' for k, v in weights.items())}). Sunlight, latitude and landing-scale rock hazards are shown but not scored: "
              "SpaceX screens for them and HiRISE is the right tool. Sites are the best go cells at least 300 km apart.")
    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "title": "Where Starship should land", "method": method,
        "grid": {"cell_deg": CELL, "lat_min": LAT0, "lat_max": LAT1, "lon_convention": G["lon_convention"], "latitude": G["latitude"], "rows": NROW, "cols": NCOL},
        "factors": factors, "shown_not_scored": CFG["shown_not_scored"], "blind_spots": BLIND_SPOTS + ([] if geo_ok else ["geology layer missing in this run: science reach absent"]),
        "stats": {"cells_scored": n, **counts, "correlations": corr, "weights": weights},
        "sites": sites, "spacex_candidates": spacex, "spacex_summary": summary, "tradeoff": tradeoff, "landmarks": CFG["landmarks"],
        "assets": {"globe_color": "mars_color_2k.jpg", "globe_bump": "mars_bump_2k.png", "map_base": "mars_base_merc.jpg", "map_score": "mars_score_merc.png",
                   "map_corners": [[-180, 70], [180, 70], [180, -70], [-180, -70]], "slide": "score_map.png"},
        "sources": SOURCES, "next": ["magnetic shelter layer", "HiRISE rock counts for the top 3", "CTX 20 m terrain for the top 3",
                                     "SWIM uncertainty as a fifth factor", "a sunlight factor once a power model exists"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_json_atomic(OUT / "sites.json", doc, CONTRACTS / "mars_sites.schema.json")
    np.savez_compressed(CACHE / "grid.npz", status=status, score=score, elevation=z, inertia=inertia, albedo=alb, c01=c01, c15=c15,
                        g_ice=g_ice, g_elev=g_elev, g_plume=g_plume, g_sci=(g_sci if geo_ok else np.full_like(g_ice, np.nan)))
    print(f"  wrote {OUT / 'sites.json'} ({len(sites)} sites, {len(graded)} candidates graded), validated")


if __name__ == "__main__":
    main()
