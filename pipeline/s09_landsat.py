"""Landsat 8/9 Collection 2 Level-2 per-field NDVI and NBR, plus agreement with Sentinel-2.

Microsoft Planetary Computer hosts landsat-c2-l2 without requester-pays S3. Earth Search
serves the same collection from requester-pays buckets, so this step does not use it.
"""
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from urllib.parse import urlparse

import numpy as np
import odc.geo.xr  # registers Dataset.odc
import odc.stac
import requests
from jsonschema import Draft202012Validator
from odc.geo.geobox import GeoBox
from pystac import ItemCollection
from pystac_client import Client
from scipy.ndimage import binary_erosion

from common import CACHE, OUT, ROOT, load_region, log, write_json
from geo import dates_of, item_date, region_geobox, resample_plane, to_reflectance
from s04_timeseries import raster_labels, sample_date

STEP = "s09"
SOURCE = "landsat-c2-l2 (Landsat 8/9, Microsoft Planetary Computer), pipeline/s09_landsat.py"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "landsat-c2-l2"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2"
BANDS = ("red", "nir08", "swir22", "qa_pixel")
REFLECTANCE = ("red", "nir08", "swir22")
KEEP_PLATFORMS = ("landsat-8", "landsat-9")
DEFAULT_SCALE = 0.0000275
DEFAULT_OFFSET = -0.2
QA_MASK = 0b111111  # fill, dilated cloud, cirrus, cloud, cloud shadow, snow
TOKEN_TTL_S = 30 * 60
PAIR_WINDOW_DAYS = 3
MIN_CLEAR_DATES = 8
ERODE_PX = 3
ERODE_MIN_PX = 50
CATALOG = CACHE / "landsat" / "items.json"
FRAMES = CACHE / "landsat" / "frames"
OUT_PATH = OUT / "timeseries_landsat.json"


def fail(msg):
    log(STEP, f"ERROR {msg}")
    sys.exit(1)


def landsat_geobox(region):
    west, south, east, north = region["bbox"]
    return GeoBox.from_bbox((west, south, east, north), "utm", resolution=30)


def platform_of(item):
    return str(item.properties.get("platform") or "").lower().replace("_", "-")


def scene_ids(items):
    return sorted(item.id for item in items)


def search_items(region):
    if CATALOG.exists():
        items = list(ItemCollection.from_file(str(CATALOG)))
        log(STEP, f"reusing {CATALOG} ({len(items)} scenes)")
        return items
    log(STEP, f"searching {STAC_URL} collection {COLLECTION}")
    client = Client.open(STAC_URL)
    found = client.search(
        collections=[COLLECTION],
        bbox=region["bbox"],
        datetime=f"{region['season_start']}/{region['as_of']}",
        query={"eo:cloud_cover": {"lt": 80}},
    )
    raw = list(found.items())
    dropped = [item.id for item in raw if platform_of(item) not in KEEP_PLATFORMS]
    items = [item for item in raw if platform_of(item) in KEEP_PLATFORMS]
    if dropped:
        log(STEP, f"dropped {len(dropped)} scenes that are not Landsat 8 or 9")
    missing = [item.id for item in items if any(band not in item.assets for band in BANDS)]
    if missing:
        keys = sorted(items[0].assets) if items else []
        fail(f"{len(missing)} scenes lack red/nir08/swir22/qa_pixel. First asset keys: {keys}")
    if not items:
        fail("no Landsat 8/9 scenes matched the season")
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    ItemCollection(items).save_object(str(CATALOG))
    log(STEP, f"cached {len(items)} scenes at {CATALOG}")
    return items


def print_scenes(items):
    grouped = defaultdict(list)
    for item in items:
        grouped[platform_of(item)].append(item_date(item))
    for platform in KEEP_PLATFORMS:
        days = sorted(grouped.get(platform, []))
        if not days:
            log(STEP, f"{platform}: 0 scenes")
            continue
        log(STEP, f"{platform}: {len(days)} scenes, dates {', '.join(days)}")


class Signer:
    def __init__(self):
        self.token = None
        self.fetched = 0.0
        self.url = TOKEN_URL

    def __call__(self, href):
        if "sig=" in href.split("?", 1)[-1]:
            return href
        token = self._token(href)
        joiner = "&" if "?" in href else "?"
        return f"{href}{joiner}{token}"

    def _token(self, href):
        if self.token and time.time() - self.fetched < TOKEN_TTL_S:
            return self.token
        token, status, body = self._fetch(self.url)
        if status == 404 and self.url == TOKEN_URL:
            account, container = _azure_account(href)
            self.url = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
            log(STEP, f"collection SAS endpoint returned 404, trying {account}/{container}")
            token, status, body = self._fetch(self.url)
        if not token:
            fail(f"signing failed status={status} url={self.url} body={body[:800]}")
        self.token = token
        self.fetched = time.time()
        log(STEP, "refreshed Planetary Computer SAS token")
        return token

    @staticmethod
    def _fetch(url):
        try:
            response = requests.get(url, timeout=60)
        except requests.RequestException as exc:
            fail(f"signing request failed: {exc}")
        text = response.text or ""
        if response.status_code == 404:
            return None, 404, text
        if response.status_code != 200:
            return None, response.status_code, text
        try:
            payload = response.json()
        except ValueError:
            return None, response.status_code, text
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            return None, response.status_code, text
        return token.strip().lstrip("?"), response.status_code, text


def _azure_account(href):
    parsed = urlparse(href)
    host = parsed.netloc
    if not host.endswith(".blob.core.windows.net"):
        fail(f"asset href is not an Azure blob URL: {href}")
    account = host.split(".")[0]
    container = parsed.path.lstrip("/").split("/", 1)[0]
    if not account or not container:
        fail(f"could not read account/container from {href}")
    return account, container


def band_scale(item, band):
    try:
        spec = item.assets[band].extra_fields["raster:bands"][0]
        return float(spec["scale"]), float(spec["offset"])
    except (KeyError, IndexError, TypeError, ValueError):
        return DEFAULT_SCALE, DEFAULT_OFFSET


def configure_rio():
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
    os.environ.setdefault("GDAL_HTTP_TIMEOUT", "120")
    odc.stac.configure_rio(cloud_defaults=True)


def assert_native(ds):
    box = ds.odc.geobox
    rx = abs(float(box.resolution.x))
    ry = abs(float(box.resolution.y))
    if abs(rx - 30) > 1 or abs(ry - 30) > 1:
        fail(f"native resolution is {rx:.3f} x {ry:.3f} m, expected 30 m")
    yname = "y" if "y" in ds.dims else "latitude"
    y = np.asarray(ds[yname].values)
    if not (y[0] > y[-1]):
        fail(f"native grid is not north-up: {yname}[0]={y[0]} {yname}[-1]={y[-1]}")
    log(STEP, f"native grid EPSG:{box.crs.to_epsg()} {box.width}x{box.height} at 30 m")
    return box


def clear_from_qa(qa):
    arr = np.asarray(qa)
    if np.issubdtype(arr.dtype, np.floating):
        finite = np.isfinite(arr)
        values = np.where(finite, arr, 1).astype(np.uint16)
        return finite & ((values & QA_MASK) == 0)
    return (arr.astype(np.uint16) & QA_MASK) == 0


def spectral_index(left, right):
    out = np.full(left.shape, np.nan, dtype=np.float32)
    den = left + right
    ok = np.isfinite(left) & np.isfinite(right) & (den != 0)
    out[ok] = (left[ok] - right[ok]) / den[ok]
    return out


def check_reflectance(red, clear, day):
    values = red[clear & np.isfinite(red)]
    if values.size < 100:
        log(STEP, f"{day} red reflectance sample n={values.size} (too cloudy to check the scale)")
        return
    p50 = float(np.percentile(values, 50))
    p95 = float(np.percentile(values, 95))
    log(STEP, f"{day} clear red reflectance p50={p50:.3f} p95={p95:.3f}")
    if p95 > 2 or p50 > 1.5:
        fail(
            f"{day} red looks unscaled (p50={p50:.3f} p95={p95:.3f}). "
            "Stopped before treating digital numbers as reflectance."
        )


def load_date(items, geobox, signer):
    started = time.perf_counter()
    ds = odc.stac.load(
        items,
        bands=list(BANDS),
        geobox=geobox,
        groupby="solar_day",
        chunks={},
        resampling="nearest",
        patch_url=signer,
        fail_on_error=True,
    ).compute()
    elapsed = time.perf_counter() - started
    loaded = dates_of(ds)
    if len(loaded) != 1:
        fail(f"expected one solar day from {scene_ids(items)}, got {loaded}")
    log(STEP, f"loaded {loaded[0]} ({len(items)} scenes) in {elapsed:.1f}s")
    return ds, loaded[0]


def planes_for_date(ds, day, src_box, frame_box, scales):
    red_dn = np.asarray(ds["red"].isel(time=0).values)
    if np.nanmax(red_dn) <= 2:
        fail(
            f"{day} red max={np.nanmax(red_dn)} looks pre-scaled. "
            "Stopped before applying scale and offset again."
        )
    red = to_reflectance(red_dn, *scales["red"])
    nir = to_reflectance(np.asarray(ds["nir08"].isel(time=0).values), *scales["nir08"])
    swir = to_reflectance(np.asarray(ds["swir22"].isel(time=0).values), *scales["swir22"])
    clear = clear_from_qa(np.asarray(ds["qa_pixel"].isel(time=0).values))
    check_reflectance(red, clear, day)
    ndvi = np.where(clear, spectral_index(nir, red), np.nan).astype(np.float32)
    nbr = np.where(clear, spectral_index(nir, swir), np.nan).astype(np.float32)
    ndvi_frame = resample_plane(ndvi, src_box, frame_box, "nearest", np.nan).astype(np.float32)
    nbr_frame = resample_plane(nbr, src_box, frame_box, "nearest", np.nan).astype(np.float32)
    clear_frame = resample_plane(clear.astype(np.uint8), src_box, frame_box, "nearest", 0)
    return ndvi_frame, nbr_frame, clear_frame.astype(bool)


def frame_path(day):
    return FRAMES / f"{day}.npz"


def read_frame(day, ids):
    path = frame_path(day)
    if not path.exists():
        return None
    saved = np.load(path)
    if sorted(str(item) for item in saved["ids"]) != ids:
        return None
    return np.asarray(saved["ndvi"]), np.asarray(saved["nbr"]), np.asarray(saved["clear"]).astype(bool)


def write_frame(day, ids, ndvi, nbr, clear):
    FRAMES.mkdir(parents=True, exist_ok=True)
    tmp = FRAMES / f".{day}.tmp.npz"
    np.savez_compressed(tmp, ndvi=ndvi, nbr=nbr, clear=clear.astype(np.uint8), ids=np.array(ids))
    os.replace(tmp, frame_path(day))


def erode_labels(labels, n_fields):
    out = np.zeros_like(labels)
    counts = np.zeros(n_fields + 1, dtype=np.int64)
    structure = np.ones((3, 3), dtype=bool)
    eroded_n = 0
    for index in range(1, n_fields + 1):
        mask = labels == index
        shrunk = binary_erosion(mask, structure=structure, iterations=ERODE_PX)
        if int(shrunk.sum()) >= ERODE_MIN_PX:
            use = shrunk
            eroded_n += 1
        else:
            use = mask
        out[use] = index
        counts[index] = int(use.sum())
    log(STEP, f"eroded {eroded_n}/{n_fields} field masks by {ERODE_PX} frame pixels")
    return out, counts


def load_frames(groups, geobox, frame_box, signer):
    planes = {}
    reused = 0
    src_box = None
    checked = False
    for day in sorted(groups):
        ids = scene_ids(groups[day])
        cached = read_frame(day, ids)
        if cached is not None:
            planes[day] = cached
            reused += 1
            log(STEP, f"{day} reused frame cache")
            continue
        ds, loaded = load_date(groups[day], geobox, signer)
        if loaded != day:
            fail(f"solar day {loaded} does not match scene date {day}")
        if src_box is None:
            src_box = assert_native(ds)
        scales = {band: band_scale(groups[day][0], band) for band in REFLECTANCE}
        if not checked:
            log(STEP, "scale/offset " + ", ".join(f"{band}={scales[band]}" for band in REFLECTANCE))
            checked = True
        ndvi, nbr, clear = planes_for_date(ds, day, src_box, frame_box, scales)
        write_frame(day, ids, ndvi, nbr, clear)
        planes[day] = (ndvi, nbr, clear)
    log(STEP, f"frame cache reused {reused}/{len(groups)} dates")
    return planes


def sample_fields(features, dates, planes, labels, counts, minimum):
    columns = {"ndvi": [], "nbr": [], "clear_frac": []}
    for day in dates:
        ndvi, nbr, clear = planes[day]
        ndvi_col, nbr_col, clear_col = sample_date(labels, ndvi, nbr, clear, counts, minimum)
        columns["ndvi"].append(ndvi_col)
        columns["nbr"].append(nbr_col)
        columns["clear_frac"].append(clear_col)
    by_id = {}
    for index, feature in enumerate(features):
        field_id = feature["properties"]["field_id"]
        by_id[field_id] = {
            "ndvi": [column[index] for column in columns["ndvi"]],
            "nbr": [column[index] for column in columns["nbr"]],
            "clear_frac": [column[index] for column in columns["clear_frac"]],
        }
    return {field_id: by_id[field_id] for field_id in sorted(by_id)}


def nearest_pairs(landsat_dates, landsat_ndvi, sentinel_dates, sentinel_ndvi):
    sentinel_days = [date.fromisoformat(text) for text in sentinel_dates]
    pairs = []
    for index, text in enumerate(landsat_dates):
        value = landsat_ndvi[index]
        if value is None:
            continue
        target = date.fromisoformat(text)
        best = None
        best_gap = PAIR_WINDOW_DAYS + 1
        for other, day in enumerate(sentinel_days):
            if sentinel_ndvi[other] is None:
                continue
            gap = abs((day - target).days)
            if gap <= PAIR_WINDOW_DAYS and gap < best_gap:
                best = other
                best_gap = gap
        if best is None:
            continue
        pairs.append(
            {
                "landsat_date": text,
                "landsat_ndvi": value,
                "sentinel_date": sentinel_dates[best],
                "sentinel_ndvi": sentinel_ndvi[best],
            }
        )
    return pairs


def agreement_stats(pairs):
    n = len(pairs)
    if n == 0:
        return {"n_pairs": 0, "r": None, "bias": None, "mae": None}
    diff = np.array([pair["landsat_ndvi"] - pair["sentinel_ndvi"] for pair in pairs], dtype=np.float64)
    stats = {
        "n_pairs": n,
        "r": None,
        "bias": round(float(diff.mean()), 3),
        "mae": round(float(np.abs(diff).mean()), 3),
    }
    if n >= 3:
        left = np.array([pair["landsat_ndvi"] for pair in pairs], dtype=np.float64)
        right = np.array([pair["sentinel_ndvi"] for pair in pairs], dtype=np.float64)
        left = left - left.mean()
        right = right - right.mean()
        denom = float(np.sqrt((left * left).sum() * (right * right).sum()))
        if denom > 0:
            stats["r"] = round(float((left * right).sum() / denom), 3)
    return stats


def median_or_none(values):
    if not values:
        return None
    return round(float(np.median(np.array(values, dtype=np.float64))), 3)


def build_agreement(fields, landsat_dates, sentinel):
    agreement = {}
    pairs = {}
    for field_id, series in fields.items():
        other = sentinel["fields"].get(field_id)
        if other is None:
            fail(f"{field_id} is missing from data/out/timeseries.json")
        matched = nearest_pairs(landsat_dates, series["ndvi"], sentinel["dates"], other["ndvi"])
        pairs[field_id] = matched
        agreement[field_id] = agreement_stats(matched)
    usable = [row for row in agreement.values() if row["n_pairs"] >= 3]
    summary = {
        "median_r": median_or_none([row["r"] for row in usable if row["r"] is not None]),
        "median_bias": median_or_none([row["bias"] for row in usable]),
        "median_mae": median_or_none([row["mae"] for row in usable]),
        "n_fields_with_3_pairs": len(usable),
        "pair_window_days": PAIR_WINDOW_DAYS,
    }
    return agreement, summary, pairs


def validate(doc, features):
    schema = json.loads((ROOT / "contracts" / "timeseries.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(doc))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR timeseries_landsat.json @ {loc}: {err.message}")
    wanted = sorted(feature["properties"]["field_id"] for feature in features)
    if list(doc["fields"]) != wanted:
        errors.append("fields")
        log(STEP, "ERROR fields are not the field ids from fields.geojson in order")
    if len(wanted) != 136:
        errors.append("count")
        log(STEP, f"ERROR expected 136 fields, found {len(wanted)}")
    n = len(doc["dates"])
    if doc["dates"] != sorted(set(doc["dates"])):
        errors.append("dates")
        log(STEP, "ERROR dates are missing, duplicated, or not ascending")
    for field_id, series in doc["fields"].items():
        if list(series) != ["ndvi", "nbr", "clear_frac"]:
            errors.append("keys")
            log(STEP, f"ERROR {field_id} key order {list(series)}")
            break
        for key in ("ndvi", "nbr", "clear_frac"):
            if len(series[key]) != n:
                errors.append("len")
                log(STEP, f"ERROR {field_id}.{key} has {len(series[key])} values, expected {n}")
                break
    if set(doc["agreement"]) != set(wanted):
        errors.append("agreement")
        log(STEP, "ERROR agreement keys are not the field ids")
    if errors:
        fail("validation failed; data/out/timeseries_landsat.json left untouched")


def clear_dates(doc):
    found = []
    for index, day in enumerate(doc["dates"]):
        if any(series["ndvi"][index] is not None for series in doc["fields"].values()):
            found.append(day)
    return found


def print_clear_share(doc, platforms):
    n_fields = len(doc["fields"])
    for platform in KEEP_PLATFORMS:
        days = [day for day in doc["dates"] if platforms[day] == platform]
        log(STEP, f"{platform}: {len(days)} dates")
        for day in days:
            index = doc["dates"].index(day)
            n_clear = sum(series["ndvi"][index] is not None for series in doc["fields"].values())
            log(STEP, f"  {day} clear fields {n_clear / n_fields:.2f} ({n_clear}/{n_fields})")


def print_examples(agreement, pairs):
    ranked = sorted(
        (
            (field_id, agreement[field_id], pairs[field_id])
            for field_id, row in agreement.items()
            if row["r"] is not None
        ),
        key=lambda row: (row[1]["r"], row[0]),
    )
    if not ranked:
        log(STEP, "no field has a defined r")
        return
    picks = [("lowest r", ranked[0]), ("median r", ranked[len(ranked) // 2]), ("highest r", ranked[-1])]
    for label, (field_id, stats, matched) in picks:
        log(
            STEP,
            f"{label} {field_id} n_pairs={stats['n_pairs']} r={stats['r']} bias={stats['bias']} mae={stats['mae']}",
        )
        for pair in matched:
            log(
                STEP,
                f"  {pair['landsat_date']} landsat={pair['landsat_ndvi']} "
                f"sentinel={pair['sentinel_date']} {pair['sentinel_ndvi']}",
            )


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    region = load_region()
    sentinel_path = OUT / "timeseries.json"
    if not sentinel_path.exists():
        fail(f"missing {sentinel_path}")
    sentinel_hash = file_sha256(sentinel_path)
    fields_path = OUT / "fields.geojson"
    if not fields_path.exists():
        fail(f"missing {fields_path}")
    features = json.loads(fields_path.read_text())["features"]
    features.sort(key=lambda feature: feature["properties"]["field_id"])
    frame_box = region_geobox(region)
    geobox = landsat_geobox(region)
    minimum = float(region["thresholds"]["clear_fraction_min"])
    items = search_items(region)
    print_scenes(items)
    groups = defaultdict(list)
    for item in items:
        groups[item_date(item)].append(item)
    dates = sorted(groups)
    platforms = {}
    for day, day_items in groups.items():
        names = sorted({platform_of(item) for item in day_items})
        platforms[day] = names[0] if len(names) == 1 else "+".join(names)
    configure_rio()
    signer = Signer()
    planes = load_frames(groups, geobox, frame_box, signer)
    labels, full_counts = raster_labels(features, frame_box)
    labels, counts = erode_labels(labels, full_counts.size - 1)
    fields = sample_fields(features, dates, planes, labels, counts, minimum)
    sentinel = json.loads(sentinel_path.read_text())
    if file_sha256(sentinel_path) != sentinel_hash:
        fail("data/out/timeseries.json changed while Landsat was running")
    agreement, summary, pairs = build_agreement(fields, dates, sentinel)
    doc = {
        "source": SOURCE,
        "dates": dates,
        "fields": fields,
        "agreement": agreement,
        "agreement_summary": summary,
    }
    ready = clear_dates(doc)
    print_clear_share(doc, platforms)
    log(
        STEP,
        f"agreement median_r={summary['median_r']} median_bias={summary['median_bias']} "
        f"median_mae={summary['median_mae']} n_fields_with_3_pairs={summary['n_fields_with_3_pairs']}",
    )
    print_examples(agreement, pairs)
    if len(ready) < MIN_CLEAR_DATES:
        fail(f"{len(ready)} dates have a clear field; need at least {MIN_CLEAR_DATES}. Output not written.")
    validate(doc, features)
    if file_sha256(sentinel_path) != sentinel_hash:
        fail("data/out/timeseries.json changed while Landsat was running; output not written")
    tmp = OUT_PATH.with_suffix(".json.tmp")
    write_json(tmp, doc)
    if file_sha256(sentinel_path) != sentinel_hash:
        tmp.unlink(missing_ok=True)
        fail("data/out/timeseries.json changed while writing; output not written")
    os.replace(tmp, OUT_PATH)
    log(STEP, f"wrote {OUT_PATH} ({len(dates)} dates, {len(fields)} fields, {len(ready)} with a clear field)")


if __name__ == "__main__":
    main()
