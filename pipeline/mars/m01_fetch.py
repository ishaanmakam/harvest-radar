"""M1: download, cache, inventory and check every Mars layer.

    python pipeline/mars/m01_fetch.py

Sources (all public, no key): MOLA MEGDR 16 ppd elevation (NASA PDS), SWIM ice consistency GeoTIFFs (PSI; the
product page's own download form), MGS TES nighttime thermal inertia 20 ppd (Putzig and Mellon, PSI), MGS TES albedo
mosaic (USGS Astrogeology), USGS SIM 3292 geologic units (USGS pygeoapi), the six 2019 HiRISE candidate pages
(University of Arizona). Everything lands in data/cache/mars (gitignored); reruns download nothing that is already there.
Each layer is also written as a simple-cylindrical GeoTIFF on a Mars sphere so m02 reads every raster the same way.
Layer checks print PASS or CHECK and never stop the run. A failed download leaves the layer missing, with the steps
to fetch it by hand printed; nothing is substituted.
"""
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.mars import CACHE, MARS_SPHERE_CRS, USER_AGENT, config, to_east180  # noqa: E402

MOLA_URL = "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg016/megt90n000eb.img"
MOLA_LBL = MOLA_URL.replace(".img", ".lbl")
TI_URL = "https://se.psi.edu/inertia/nmap2003.bin"
TI_MASK_URL = "https://se.psi.edu/inertia/nmsk2003.bin"
ALBEDO_URL = "https://planetarymaps.usgs.gov/mosaic/Mars_MGS_TES_Albedo_mosaic_global_7410m.tif"
SWIM_PAGES = ["https://swim.psi.edu/SWIM4MIMProducts.php", "https://swim.psi.edu/SWIM2Products.php"]
GEO_BASE = "https://astrogeology.usgs.gov/pygeoapi"
GEOLOGY_ITEMS = GEO_BASE + "/collections/mars/sim3292_global_geologic_map/units/items?f=json&limit=100"
HIRISE = "https://hirise.lpl.arizona.edu/{id}"
SWIM_DIR = CACHE / "swim"
SWIM_LAYERS = {"c01": "Ci_0_1", "c15": "Ci_1_5", "c5": "Ci_5"}


def http_get(url, *, stream=False, timeout=600, retries=3, **kw):
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=stream, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:120]
        print(f"    attempt {attempt} failed for {url}: {last}")
        time.sleep(3 * attempt)
    return None


def download(url, path):
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        print(f"  cached  {path.name}  {path.stat().st_size / 1e6:.1f} MB")
        return True
    print(f"  fetch   {url}")
    r = http_get(url, stream=True)
    if r is None:
        print(f"  MISSING {path.name}: download failed three times")
        return False
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    tmp.replace(path)
    print(f"  saved   {path.name}  {path.stat().st_size / 1e6:.1f} MB")
    return True


# ---------------------------------------------------------------- MOLA
def mola_to_geotiff():
    out = CACHE / "mola_16ppd.tif"
    if out.exists():
        return out
    raw = np.fromfile(CACHE / "megt90n000eb.img", dtype=">i2").reshape(2880, 5760)
    east = np.roll(raw, 2880, axis=1)                     # column 0 was 0 E; now -180 E
    with rasterio.open(out, "w", driver="GTiff", height=2880, width=5760, count=1, dtype="int16", crs=MARS_SPHERE_CRS,
                       transform=from_origin(-180, 90, 1 / 16, 1 / 16), compress="deflate", nodata=None) as dst:
        dst.write(east.astype("int16"), 1)
    print(f"  wrote   {out.name} (-180..180 east, north up)")
    return out


# ---------------------------------------------------------------- thermal inertia
def read_ti_header():
    header = open(CACHE / "nmap2003.bin", "rb").read(7200).decode("ascii", "replace").replace("\x00", "")
    print("  thermal inertia header says:")
    for line in header.splitlines():
        if any(k in line for k in ("Longitude", "Latitudes", "Resolution", "no-data", "maximum", "minimum", "records")):
            print("    " + line.strip())
    return header


def ti_to_geotiff(mola_path):
    out = CACHE / "ti_20ppd.tif"
    if out.exists():
        return out
    header = read_ti_header()
    west_positive = "West" in header
    raw = np.fromfile(CACHE / "nmap2003.bin", dtype=">i2", offset=7200).reshape(3600, 7200).astype("float32")
    mask_path = CACHE / "nmsk2003.bin"
    mask = None
    if mask_path.exists() and mask_path.stat().st_size >= 7200 + 7200 * 3600:
        mask = np.fromfile(mask_path, dtype="u1", offset=7200).reshape(3600, 7200).astype("float32")
        print(f"  TES mask read: {(mask == 1).mean() * 100:.1f}% measured, the rest is the authors' interpolated fill (kept, flagged in m02)")
    raw[raw <= 0] = np.nan
    if west_positive:
        # column c holds longitude c/20 West; east longitude e sits at column (7200 - e*20) % 7200
        east = np.concatenate([raw[:, :1], raw[:, :0:-1]], axis=1)
        print("  header longitude runs 0..360 West: columns flipped to east-positive")
    else:
        east = raw
    east = np.roll(east, 3600, axis=1)                         # 0 E first -> -180 E first
    # Row order is not stated in the header. Try both row orders and both longitude readings and keep the one where
    # Syrtis Major (firm, dark) is clearly warmer at night than Tharsis (dust); print every candidate so it can be checked.
    cfg = config()["checks"]["thermal_inertia"]

    def box_mean(arr, box):
        lat0, lat1, lon0, lon1 = box
        lon0, lon1 = to_east180(lon0), to_east180(lon1)
        r0, r1 = int((90 - lat1) * 20), int((90 - lat0) * 20)
        c0, c1 = int((lon0 + 180) * 20), int((lon1 + 180) * 20)
        return float(np.nanmean(arr[r0:r1, c0:c1]))
    as_is = np.roll(raw, 3600, axis=1)                         # if the header's "West" were really east-positive
    variants = {"west-flipped, row 0 = 90 N": east, "west-flipped, row 0 = 90 S": east[::-1],
                "as stored, row 0 = 90 N": as_is, "as stored, row 0 = 90 S": as_is[::-1]}
    scores = {}
    for label, arr in variants.items():
        sy, th = box_mean(arr, cfg["syrtis_box"]), box_mean(arr, cfg["tharsis_box"])
        scores[label] = sy - th
        print(f"    {label:28} Syrtis {sy:5.0f}  Tharsis {th:5.0f}")
    best = max(scores, key=scores.get)
    chosen = variants[best]
    print(f"  row order and longitude reading chosen: {best}")
    with rasterio.open(out, "w", driver="GTiff", height=3600, width=7200, count=1, dtype="float32", crs=MARS_SPHERE_CRS,
                       transform=from_origin(-180, 90, 1 / 20, 1 / 20), compress="deflate", nodata=np.nan) as dst:
        dst.write(chosen.astype("float32"), 1)
    print(f"  wrote   {out.name}")
    if mask is not None:
        m = mask
        if best.startswith("west-flipped"):
            m = np.concatenate([m[:, :1], m[:, :0:-1]], axis=1)
        m = np.roll(m, 3600, axis=1)
        if best.endswith("90 S"):
            m = m[::-1]
        with rasterio.open(CACHE / "ti_mask_20ppd.tif", "w", driver="GTiff", height=3600, width=7200, count=1, dtype="float32", crs=MARS_SPHERE_CRS,
                           transform=from_origin(-180, 90, 1 / 20, 1 / 20), compress="deflate", nodata=np.nan) as dst:
            dst.write(m.astype("float32"), 1)
        print("  wrote   ti_mask_20ppd.tif (1 = TES measured, 0 = interpolated fill)")
    return out


# ---------------------------------------------------------------- SWIM
def fetch_swim():
    """The SWIM product page is a GET form: the GeoTIFF checkboxes plus DL-Sel=True answer with SWIMProducts.zip."""
    SWIM_DIR.mkdir(parents=True, exist_ok=True)
    have = {k: sorted(SWIM_DIR.glob(f"*{v}*.tif")) for k, v in SWIM_LAYERS.items()}
    if all(have.values()):
        for k, v in have.items():
            print(f"  cached  swim {k}: {v[0].name}")
        return {k: v[0] for k, v in have.items()}
    # One layer per request: asking for several at once makes the server cut the zip off after 48 KB.
    found = {}
    for k, layer in SWIM_LAYERS.items():
        if have.get(k):
            found[k] = have[k][0]
            continue
        zpath = CACHE / f"swim_{layer}.zip"
        if not (zpath.exists() and zpath.stat().st_size > 100_000):
            page = SWIM_PAGES[0]
            print(f"  fetch   {page} ({layer} GeoTIFF box + DL-Sel)")
            r = http_get(page, params={f"{layer}-GeoTIFF": "True", "DL-Sel": "True"}, stream=True, timeout=1800)
            if r is not None and "zip" in (r.headers.get("Content-Type") or ""):
                tmp = zpath.with_suffix(".part")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                tmp.replace(zpath)
                print(f"  saved   {zpath.name}  {zpath.stat().st_size / 1e6:.1f} MB")
            else:
                zpath.unlink(missing_ok=True)
        if zpath.exists():
            try:
                with zipfile.ZipFile(zpath) as z:
                    for n in z.namelist():
                        if layer in Path(n).name and n.lower().endswith((".tif", ".tiff")):
                            target = SWIM_DIR / Path(n).name
                            if not target.exists():
                                target.write_bytes(z.read(n))
                            found[k] = target
                            print(f"  swim {k}: {target.name}  {target.stat().st_size / 1e6:.1f} MB")
                            break
            except zipfile.BadZipFile:
                print(f"  {zpath.name} is not a valid archive (a truncated download); removed")
                zpath.unlink(missing_ok=True)
    missing = [SWIM_LAYERS[k] for k in SWIM_LAYERS if k not in found]
    if missing:
        print("  SWIM is MISSING. Manual steps:")
        print("    1. Open https://swim.psi.edu/SWIM4MIMProducts.php (or SWIM2Products.php for SWIM 2.0).")
        print("    2. Tick the GeoTIFF box for Ice Consistency Map (0-1 m), (1-5 m) and (>5 m), press Download Selected Files.")
        print(f"    3. Unzip and put the .tif files into {SWIM_DIR} (names contain {', '.join(missing)}).")
        print("    4. Rerun python pipeline/mars/m01_fetch.py; m02 refuses to score without the ice layer.")
    return found


# ---------------------------------------------------------------- geology
def fetch_geology():
    out = CACHE / "geologic_units.geojson"
    if out.exists() and out.stat().st_size > 1_000_000:
        doc = json.loads(out.read_text(encoding="utf-8"))
        print(f"  cached  {out.name}: {len(doc['features'])} features")
        return doc
    url, feats, pages = GEOLOGY_ITEMS, [], 0
    print(f"  fetch   {GEOLOGY_ITEMS} (paged)")
    while url and pages < 1000:
        r = http_get(url, timeout=240, retries=5)
        if r is None:
            print("  geology MISSING: the USGS API did not answer; science_reach will be absent")
            return None
        d = r.json()
        feats += d.get("features", [])
        pages += 1
        nxt = [(GEO_BASE + l["href"]) if l["href"].startswith("/") else l["href"] for l in d.get("links", []) if l.get("rel") == "next"]   # relative to the API base
        url = nxt[0] if nxt and d.get("features") else None
    doc = {"type": "FeatureCollection", "features": feats}
    out.write_text(json.dumps(doc), encoding="utf-8")
    print(f"  saved   {out.name}: {len(feats)} features in {pages} pages")
    return doc


EPOCH = {"N": "Noachian", "H": "Hesperian", "A": "Amazonian"}


def unit_age(symbol):
    """Epoch letters at the start of a SIM 3292 unit symbol: N, H, A and transitions such as HN, AH, eN, lH."""
    s = re.sub(r"^[elm]", "", symbol or "")             # early / late / middle prefixes
    letters = [c for c in s if c in EPOCH]
    if not letters:
        return "unknown"
    ages = [EPOCH[c] for c in letters[:2]]
    return ages[0] if len(ages) == 1 or ages[0] == ages[1] else f"{ages[0]}-{ages[1]} transition"


def geology_summary(doc):
    if not doc:
        return
    props = list(doc["features"][0]["properties"].keys())
    units = sorted({f["properties"].get("unit") for f in doc["features"] if f["properties"].get("unit")})
    lons = [c[0] for f in doc["features"][:200] for ring in (f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "MultiPolygon" else f["geometry"]["coordinates"]) for c in ring[:5]]
    print(f"  properties: {props}")
    print(f"  {len(doc['features'])} features, {len(units)} distinct unit symbols; longitudes span {min(lons):.1f}..{max(lons):.1f} "
          f"({'-180..180' if min(lons) < 0 else '0..360'})")
    print("  symbol -> age:")
    for u in units:
        print(f"    {u:8} {unit_age(u)}")
    return {u: unit_age(u) for u in units}


# ---------------------------------------------------------------- HiRISE candidates
def fetch_candidates():
    out = CACHE / "spacex_candidates.json"
    if out.exists():
        doc = json.loads(out.read_text(encoding="utf-8"))
        print(f"  cached  {out.name}: {sum(1 for c in doc if c.get('lat') is not None)} of {len(doc)} parsed")
        return doc
    cfg = config()["spacex_candidates"]
    rows = []
    for cid, meta in cfg.items():
        url = HIRISE.format(id=cid)
        r = http_get(url, timeout=60)
        row = {"id": cid, "url": url, "region": meta["region"], "lat": None, "lon": None, "acquired": None, "name": None}
        if r is not None:
            html = r.text
            title = re.search(r"<title>\s*HiRISE \| ([^<(]+)", html)
            lat = re.search(r"Latitude \(centered\)</strong><br />\s*(-?[0-9.]+)", html)
            lon = re.search(r"Longitude \(East\)</strong><br />\s*(-?[0-9.]+)", html)
            acq = re.search(r"Acquisition date</strong><br />\s*([0-9]{1,2} [A-Za-z]+ [0-9]{4})", html)
            if title:
                row["name"] = title.group(1).strip()
            if lat and lon:
                row["lat"], row["lon"] = float(lat.group(1)), to_east180(float(lon.group(1)))
                row["lon_east_360"] = float(lon.group(1))
            if acq:
                row["acquired"] = acq.group(1)
        if row["lat"] is None:
            print(f"  {cid}: page could not be parsed; listed as missing")
        else:
            print(f"  {cid}: {row['lat']:.3f} N, {row['lon_east_360']:.3f} E ({row['lon']:.3f}), {row['acquired']}, {row['name']}")
        rows.append(row)
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


# ---------------------------------------------------------------- inventory and checks
def raster_info(path):
    with rasterio.open(path) as ds:
        arr = ds.read(1, masked=True)
        b = ds.bounds
        unit = "deg" if (ds.crs is None or ds.crs.is_geographic) else "m"
        return {"path": str(path.relative_to(CACHE.parents[2])), "shape": [ds.height, ds.width], "pixel": [abs(ds.transform.a), abs(ds.transform.e)], "pixel_unit": unit,
                "bounds": [b.left, b.bottom, b.right, b.top], "nodata": None if ds.nodata is None or np.isnan(ds.nodata) else float(ds.nodata),
                "min": float(arr.min()), "max": float(arr.max()), "mean": float(arr.mean()),
                "lon_convention": "-180..180" if b.left < 0 else "0..360", "dtype": str(ds.dtypes[0])}


def raster_xy(ds, lon, lat):
    """Longitude/latitude (degrees, east) to the raster's own coordinates: degrees for geographic rasters, metres on the
    Mars sphere for the equirectangular SWIM and albedo files."""
    if ds.crs is None or ds.crs.is_geographic:
        b = ds.bounds
        if b.left >= 0 and lon < 0:
            lon += 360
        return lon, lat
    from pyproj import Transformer
    tr = Transformer.from_crs(MARS_SPHERE_CRS, ds.crs, always_xy=True)
    return tr.transform(lon, lat)


def sample_box(path, box):
    """Mean of valid pixels over [lat0, lat1, lon0, lon1] (lon in any convention)."""
    lat0, lat1, lon0, lon1 = box
    lon0, lon1 = to_east180(lon0), to_east180(lon1)
    with rasterio.open(path) as ds:
        x0, y0 = raster_xy(ds, lon0, lat0)
        x1, y1 = raster_xy(ds, lon1, lat1)
        ra, ca = ds.index(x0, y0)
        rb, cb = ds.index(x1, y1)
        r0, r1, c0, c1 = min(ra, rb), max(ra, rb), min(ca, cb), max(ca, cb)
        r0, c0 = max(r0, 0), max(c0, 0)
        win = rasterio.windows.Window(c0, r0, max(1, c1 - c0), max(1, r1 - r0))
        arr = ds.read(1, window=win, masked=True)
        return float(arr.mean()) if arr.count() else float("nan")


def sample_point(path, lat, lon):
    with rasterio.open(path) as ds:
        x, y = raster_xy(ds, to_east180(lon), lat)
        r, c = ds.index(x, y)
        r, c = min(max(r, 0), ds.height - 1), min(max(c, 0), ds.width - 1)
        v = ds.read(1, window=rasterio.windows.Window(c, r, 1, 1), masked=True)
        return None if v.mask.all() else float(v[0, 0])


def check(name, ok, detail):
    print(f"  {'PASS ' if ok else 'CHECK'} {name}: {detail}")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    cfg = config()
    print("M1 fetch")
    print("A. MOLA MEGDR 16 ppd")
    mola_ok = download(MOLA_URL, CACHE / "megt90n000eb.img") and download(MOLA_LBL, CACHE / "megt90n000eb.lbl")
    mola = mola_to_geotiff() if mola_ok else None
    print("C. TES thermal inertia")
    ti_ok = download(TI_URL, CACHE / "nmap2003.bin")
    download(TI_MASK_URL, CACHE / "nmsk2003.bin")
    ti = ti_to_geotiff(mola) if ti_ok else None
    print("D. TES albedo")
    albedo = CACHE / Path(ALBEDO_URL).name if download(ALBEDO_URL, CACHE / Path(ALBEDO_URL).name) else None
    print("B. SWIM ice consistency")
    swim = fetch_swim()
    print("E. USGS SIM 3292 geology")
    geology = fetch_geology()
    ages = geology_summary(geology)
    print("G. HiRISE candidate pages")
    candidates = fetch_candidates()
    print("H. magnetic field grid: no ready public grid fetched in this run; listed under next")

    print("Inventory")
    inventory = {}
    for key, path in {"mola": mola, "thermal_inertia": ti, "albedo": albedo, **{f"swim_{k}": v for k, v in swim.items()}}.items():
        if path and Path(path).exists():
            inventory[key] = raster_info(Path(path))
            i = inventory[key]
            print(f"  {key:12} {i['shape']}  {i['pixel'][0]:.4g} {i['pixel_unit']}  lon {i['lon_convention']}  nodata {i['nodata']}  "
                  f"min {i['min']:.3g} max {i['max']:.3g} mean {i['mean']:.3g}")
    inventory["geology"] = {"features": len(geology["features"]) if geology else 0, "unit_ages": ages or {}}
    inventory["candidates"] = candidates
    (CACHE / "inventory.json").write_text(json.dumps(inventory, indent=1), encoding="utf-8")

    print("Layer checks")
    ck = cfg["checks"]
    if mola:
        oly = sample_point(mola, 18.65, -133.8)
        hel = sample_point(mola, -42.4, 70.5)
        check("MOLA Olympus Mons summit", oly is not None and oly > ck["mola"]["olympus_min_m"], f"{oly} m (expect > {ck['mola']['olympus_min_m']})")
        check("MOLA Hellas floor", hel is not None and hel < ck["mola"]["hellas_max_m"], f"{hel} m (expect < {ck['mola']['hellas_max_m']})")
    if swim.get("c01"):
        a = sample_box(swim["c01"], ck["swim"]["arcadia_box"])
        t = sample_box(swim["c01"], ck["swim"]["tharsis_box"])
        check("SWIM Arcadia 0-1 m consistency", a > 0, f"mean {a:.3f} (expect > 0)")
        check("SWIM Tharsis 0-1 m consistency", t < 0, f"mean {t:.3f} (expect < 0)")
    if ti:
        th = sample_box(ti, ck["thermal_inertia"]["tharsis_box"])
        sy = sample_box(ti, ck["thermal_inertia"]["syrtis_box"])
        check("TI Tharsis", th < ck["thermal_inertia"]["tharsis_max"], f"mean {th:.0f} (expect < {ck['thermal_inertia']['tharsis_max']})")
        check("TI Syrtis Major", sy > ck["thermal_inertia"]["syrtis_min"], f"mean {sy:.0f} (expect > {ck['thermal_inertia']['syrtis_min']})")
    if albedo:
        th = sample_box(albedo, ck["thermal_inertia"]["tharsis_box"])
        sy = sample_box(albedo, ck["thermal_inertia"]["syrtis_box"])
        check("Albedo Tharsis", th > ck["albedo"]["tharsis_min"], f"mean {th:.3f} (expect > {ck['albedo']['tharsis_min']})")
        check("Albedo Syrtis Major", sy < ck["albedo"]["syrtis_max"], f"mean {sy:.3f} (expect < {ck['albedo']['syrtis_max']})")
    if mola:
        for c in candidates:
            if c.get("lat") is None:
                continue
            e = sample_point(mola, c["lat"], c["lon"])
            check(f"candidate {c['id']} elevation", e is not None and e < ck["candidates_below_m"], f"{e} m at {c['lat']:.3f}, {c['lon']:.3f}")
    print("M1 done")


if __name__ == "__main__":
    main()
