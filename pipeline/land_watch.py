"""Satellite watch for one box at an arbitrary land coordinate.

Uses the same season-pipeline pieces as the rest of pipeline/: Sentinel-2 search,
WorldCover cropland, Open-Meteo, and FIRMS. Does not read or write
pipeline/global_watch.py, and does not shift the Iowa Story County curve.
Output goes under data/cache/global/random/.
"""
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from jsonschema import Draft202012Validator, FormatChecker
from odc.geo.geobox import GeoBox
from PIL import Image, ImageDraw
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.windows import Window, from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s07_fires
from common import ROOT, log, write_json
from geo import VALID_SCL, dates_of, load_bands, scale_offset, to_reflectance
from s01_search import STAC_URL, search_collection
from s06_weather import METEO_URL
from s07_fires import AREA_URL, SOURCES, expand, fetch_text, load_key, parse_csv

STEP = "land"
SCHEMA_PATH = ROOT / "contracts" / "global.schema.json"
CACHE_DIR = ROOT / "data" / "cache" / "global"
OUT_DIR = CACHE_DIR / "random"
WORLDCOVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
CROPLAND = 40
SEARCH_START = "2025-09-15"
CLOUD_MAX = 70
CLEAR_MIN = 0.6
THUMB_CLEAR = 0.8
RAIN_MM = 15.0
OFFSETS = (-0.2, -0.1, 0.0, 0.1, 0.2)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
METHOD = (
    "One box about 5.5 km on a side, moved onto the ESA WorldCover cropland class when that map can be read. "
    "Sentinel-2 NDVI runs from 2025-09-15. Peak is the highest 3-point rolling median of clear NDVI. "
    "Current is the last clear value. Peak under 0.4 is low vegetation. "
    "Current at least 85 percent of the peak and rising over the last 30 days is growing; "
    "at least 85 percent without that rise is peak canopy. Between half and 85 percent of the peak and falling is drying down. "
    "Below half the peak, a drop within 45 days is recently harvested or senesced; otherwise the box is harvested or off-season. "
    "Crops are not labelled. This is not the Iowa Story County curve."
)
SOURCES_TEXT = [
    "Sentinel-2 L2A (Earth Search, sentinel-2-l2a)",
    "ESA WorldCover 2021 v200 (class 40 cropland)",
    "Open-Meteo forecast",
    "NASA FIRMS VIIRS NRT (SNPP, NOAA-20, NOAA-21), area API",
]


def say(msg):
    log(STEP, msg)


def box_around(lat, lon):
    dlat = 0.05
    dlon = 0.05 / max(0.2, math.cos(math.radians(lat)))
    return [round(lon - dlon / 2, 5), round(lat - dlat / 2, 5), round(lon + dlon / 2, 5), round(lat + dlat / 2, 5)]


def tile_name(lat, lon):
    sw_lat = math.floor(lat / 3.0) * 3
    sw_lon = math.floor(lon / 3.0) * 3
    ns = "N" if sw_lat >= 0 else "S"
    ew = "E" if sw_lon >= 0 else "W"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(int(sw_lat)):02d}{ew}{abs(int(sw_lon)):03d}_Map.tif"


def read_cropland(lat, lon):
    """One geographic overview covering the 25 candidate boxes. None when the tiles cannot be read."""
    import rasterio
    from affine import Affine
    from rasterio.warp import reproject

    half = 0.4
    west, south, east, north = lon - half, lat - half, lon + half, lat + half
    width = height = 400
    transform = Affine((east - west) / width, 0, west, 0, -(north - south) / height, north)
    dst = np.zeros((height, width), dtype=np.uint8)
    names = []
    for slat in (south, lat, north):
        for slon in (west, lon, east):
            name = tile_name(slat, slon)
            if name not in names:
                names.append(name)
    filled = False
    try:
        for name in names:
            url = "/vsicurl/" + WORLDCOVER + name
            with rasterio.open(url) as src:
                window = from_bounds(west, south, east, north, transform=src.transform)
                window = window.intersection(Window(0, 0, src.width, src.height))
                if window.width < 2 or window.height < 2:
                    continue
                src_arr = src.read(1, window=window)
                temp = np.zeros_like(dst)
                reproject(
                    src_arr,
                    temp,
                    src_transform=src.window_transform(window),
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs="EPSG:4326",
                    resampling=Resampling.nearest,
                    src_nodata=0,
                    dst_nodata=0,
                )
                put = (dst == 0) & (temp != 0)
                dst[put] = temp[put]
                filled = True
    except Exception as exc:
        say(f"WorldCover unreadable near {lat:.2f},{lon:.2f}: {type(exc).__name__}")
        return None
    if not filled or not np.any(dst):
        return None
    return dst, (west, north, (east - west) / width, -(north - south) / height)


def fraction_in(sample, west, south, east, north):
    arr, (left, top, px, py) = sample
    height, width = arr.shape
    c0 = int(math.floor((west - left) / px))
    c1 = int(math.ceil((east - left) / px))
    r_north = (north - top) / py
    r_south = (south - top) / py
    r0 = int(math.floor(min(r_north, r_south)))
    r1 = int(math.ceil(max(r_north, r_south)))
    if c1 <= 0 or c0 >= width or r1 <= 0 or r0 >= height:
        return 0.0
    c0 = max(0, c0)
    c1 = min(width, c1)
    r0 = max(0, r0)
    r1 = min(height, r1)
    chip = arr[r0:r1, c0:c1]
    if chip.size == 0:
        return 0.0
    valid = chip != 0
    if int(valid.sum()) < 0.5 * chip.size:
        return 0.0
    return float((chip == CROPLAND).sum()) / float(valid.sum())


def snap_box(lat, lon):
    sample = read_cropland(lat, lon)
    centre_box = box_around(lat, lon)
    if sample is None:
        return centre_box, None, False
    scored = []
    for dlat in OFFSETS:
        for dlon in OFFSETS:
            box = box_around(lat + dlat, lon + dlon)
            frac = fraction_in(sample, box[0], box[1], box[2], box[3])
            scored.append((frac, dlat * dlat + dlon * dlon, box))
    best_frac = max(row[0] for row in scored)
    near = [row for row in scored if row[0] >= best_frac - 0.05]
    near.sort(key=lambda row: (row[1], -row[0]))
    chosen = near[0]
    return chosen[2], round(chosen[0], 3), True


def search_scenes(bbox):
    today = datetime.now(timezone.utc).date().isoformat()
    interval = f"{SEARCH_START}T00:00:00Z/{today}T23:59:59Z"
    client = Client.open(STAC_URL)
    items = search_collection(client, "sentinel-2-l2a", bbox, interval, CLOUD_MAX)
    items.sort(key=lambda item: item.datetime or datetime.min.replace(tzinfo=timezone.utc))
    return items


def cropland_on_grid(bbox, geobox):
    import rasterio
    from rasterio.warp import reproject

    west, south, east, north = bbox
    names = []
    for lat in (south, (south + north) / 2, north):
        for lon in (west, (west + east) / 2, east):
            name = tile_name(lat, lon)
            if name not in names:
                names.append(name)
    dst = np.zeros((geobox.height, geobox.width), dtype=np.uint8)
    filled = False
    pad = 0.02
    try:
        for name in names:
            url = "/vsicurl/" + WORLDCOVER + name
            with rasterio.open(url) as src:
                window = from_bounds(west - pad, south - pad, east + pad, north + pad, transform=src.transform)
                window = window.intersection(Window(0, 0, src.width, src.height))
                if window.width < 2 or window.height < 2:
                    continue
                src_arr = src.read(1, window=window)
                temp = np.zeros_like(dst)
                reproject(
                    src_arr,
                    temp,
                    src_transform=src.window_transform(window),
                    src_crs=src.crs,
                    dst_transform=geobox.transform,
                    dst_crs=geobox.crs,
                    resampling=Resampling.nearest,
                    src_nodata=0,
                    dst_nodata=0,
                )
                put = (dst == 0) & (temp != 0)
                dst[put] = temp[put]
                filled = True
    except Exception as exc:
        say(f"WorldCover grid failed: {type(exc).__name__}")
        return None
    if not filled:
        return None
    return dst == CROPLAND


def scene_batches(items):
    batches = []
    current = []
    current_day = None
    for item in items:
        day = item.datetime.astimezone(timezone.utc).date().isoformat() if item.datetime else None
        if current and day != current_day and len(current) >= 8:
            batches.append(current)
            current = []
        current.append(item)
        current_day = day
    if current:
        batches.append(current)
    return batches


def series_from_items(items, bbox, use_mask):
    geobox = GeoBox.from_bbox((bbox[0], bbox[1], bbox[2], bbox[3]), "utm", resolution=20)
    mask = cropland_on_grid(bbox, geobox) if use_mask else None
    fine_frac = round(float(mask.mean()), 3) if mask is not None else None
    dates, ndvi, clear = [], [], []
    for chunk in scene_batches(items):
        try:
            ds = load_bands(chunk, ["red", "nir", "scl"], geobox=geobox)
            red_scale = scale_offset(chunk, "red")
            nir_scale = scale_offset(chunk, "nir")
        except Exception as exc:
            say(f"scene batch failed: {type(exc).__name__}: {exc}")
            continue
        for index, day in enumerate(dates_of(ds)):
            red = to_reflectance(ds.red.isel(time=index).values, *red_scale) - np.float32(red_scale[1])
            nir = to_reflectance(ds.nir.isel(time=index).values, *nir_scale) - np.float32(nir_scale[1])
            scl = np.asarray(ds.scl.isel(time=index).values)
            good = np.isin(scl, VALID_SCL) & np.isfinite(red) & np.isfinite(nir)
            if mask is not None and mask.shape == good.shape and mask.any():
                base = mask
            else:
                base = np.ones(good.shape, dtype=bool)
            base_n = int(base.sum())
            if base_n == 0:
                continue
            frac = float((good & base).sum()) / float(base_n)
            value = None
            if frac >= CLEAR_MIN:
                use = good & base & (red > 0) & (nir > 0)
                if use.any():
                    denom = nir[use] + red[use]
                    ratio = (nir[use] - red[use]) / denom
                    value = round(float(np.mean(np.clip(ratio, -1, 1))), 3)
            if day in dates:
                slot = dates.index(day)
                if clear[slot] >= frac:
                    continue
                clear[slot] = round(frac, 3)
                ndvi[slot] = value
                continue
            dates.append(day)
            clear.append(round(frac, 3))
            ndvi.append(value)
    order = sorted(range(len(dates)), key=lambda index: dates[index])
    return (
        [dates[index] for index in order],
        [ndvi[index] for index in order],
        [clear[index] for index in order],
        fine_frac,
    )


def latest_thumb_day(dates, clear):
    found = None
    for day, frac in zip(dates, clear):
        if frac is not None and frac >= THUMB_CLEAR:
            found = day
    return found


def save_thumb(item, bbox, path):
    geobox = GeoBox.from_bbox((bbox[0], bbox[1], bbox[2], bbox[3]), "utm", resolution=10)
    ds = load_bands([item], ["red", "green", "blue"], geobox=geobox)
    planes = []
    for name in ("red", "green", "blue"):
        scale = scale_offset([item], name)
        plane = to_reflectance(getattr(ds, name).isel(time=0).values, *scale) - np.float32(scale[1])
        finite = plane[np.isfinite(plane)]
        if finite.size == 0:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = np.percentile(finite, [2, 98])
        if hi <= lo:
            hi = lo + 1e-6
        stretched = np.clip((plane - lo) / (hi - lo), 0, 1)
        stretched[~np.isfinite(plane)] = 0
        planes.append((stretched * 255).astype(np.uint8))
    rgb = np.stack(planes, axis=-1)
    image = Image.fromarray(rgb, mode="RGB")
    longest = max(image.size)
    if longest > 550:
        scale = 550 / longest
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.BILINEAR)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90)


def rolling_peak(pairs):
    if not pairs:
        return None
    if len(pairs) < 3:
        day, value = max(pairs, key=lambda row: row[1])
        return day, value
    best = None
    for index in range(1, len(pairs) - 1):
        window = sorted(row[1] for row in pairs[index - 1:index + 2])
        median = window[1]
        if best is None or median > best[1]:
            best = (pairs[index][0], median)
    return best


def stage_of(pairs):
    peak = rolling_peak(pairs)
    if peak is None:
        return None
    peak_day = date.fromisoformat(peak[0])
    current_day = date.fromisoformat(pairs[-1][0])
    current = pairs[-1][1]
    peak_value = peak[1]
    prior = [row for row in pairs if date.fromisoformat(row[0]) <= current_day - timedelta(days=30)]
    ref = prior[-1][1] if prior else (pairs[-2][1] if len(pairs) > 1 else current)
    rising = current > ref
    falling = current < ref
    if peak_value < 0.4:
        name = "low vegetation"
    elif current >= 0.85 * peak_value and rising:
        name = "growing"
    elif current >= 0.85 * peak_value:
        name = "peak canopy"
    elif 0.5 * peak_value <= current < 0.85 * peak_value and falling:
        name = "drying down"
    elif current < 0.5 * peak_value:
        crossed = None
        after = False
        for day, value in pairs:
            if day == peak[0]:
                after = True
                continue
            if after and value < 0.5 * peak_value:
                crossed = date.fromisoformat(day)
                break
        if crossed is not None and (current_day - crossed).days <= 45:
            name = "recently harvested or senesced"
        else:
            name = "harvested or off-season"
    else:
        name = "harvested or off-season"
    days = (current_day - peak_day).days
    reason = (
        f"peak {peak_value:.2f} on {peak[0]}, current {current:.2f} on {pairs[-1][0]}, "
        f"{days} days since peak, 30-day reference {ref:.2f}"
    )
    return {
        "stage": name,
        "stage_reason": reason,
        "peak": {"date": peak[0], "value": round(peak_value, 3)},
        "current": {"date": pairs[-1][0], "value": round(current, 3)},
        "days_since_peak": days,
    }


def short_date(text):
    day = date.fromisoformat(text)
    return f"{MONTHS[day.month - 1]} {day.day}"


def headline_for(stage, rain, fires):
    if stage is None:
        return "No clear Sentinel-2 pass in the window."
    fire_text = "no fires" if not fires else f"{fires} fire" + ("" if fires == 1 else "s")
    rain_text = "no rain forecast" if rain is None else f"{rain:.0f} mm of rain in the next 3 days"
    label = stage["stage"][0].upper() + stage["stage"][1:]
    return (
        f"{label}: NDVI {stage['current']['value']:.2f}, {stage['days_since_peak']} days past a "
        f"{stage['peak']['value']:.2f} peak on {short_date(stage['peak']['date'])}; {rain_text}; {fire_text}."
    )


def fetch_forecast(lat, lon):
    last = None
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "forecast_days": 7,
        "daily": "precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min",
    }
    for attempt in range(1, 4):
        try:
            response = requests.get(METEO_URL, params=params, timeout=30)
            response.raise_for_status()
            daily = response.json().get("daily") or {}
            dates = daily.get("time") or []

            def column(name):
                values = daily.get(name) or []
                out = []
                for value in values:
                    if value is None:
                        out.append(None)
                    else:
                        number = float(value)
                        out.append(int(round(number)) if name.endswith("probability_max") else round(number, 1))
                return out

            return {
                "dates": dates,
                "precip_mm": column("precipitation_sum"),
                "precip_prob": column("precipitation_probability_max"),
                "tmax_c": column("temperature_2m_max"),
                "tmin_c": column("temperature_2m_min"),
            }
        except (requests.RequestException, ValueError, TypeError) as exc:
            last = exc
            say(f"Open-Meteo failed ({attempt}/3): {exc}")
            time.sleep(attempt)
    say(f"Open-Meteo giving up: {last}")
    return None


def rain_from(forecast):
    if not forecast:
        return None, False
    precip = [value for value in forecast["precip_mm"][:3] if value is not None]
    if not precip:
        return None, False
    total = round(sum(precip), 1)
    return total, any(value >= RAIN_MM for value in precip)


def firms_for(bbox):
    widened = expand(tuple(bbox), 0.1)
    start = datetime.now(timezone.utc).date() - timedelta(days=4)
    seen = set()
    header_printed = [False]
    rows = []
    real_exit = sys.exit

    def stop(code=1):
        raise RuntimeError("firms request failed")

    sys.exit = stop
    try:
        key = load_key()
        for source in SOURCES:
            url = AREA_URL.format(
                key=key, source=source,
                west=widened[0], south=widened[1], east=widened[2], north=widened[3],
                days=5, start=start.isoformat(),
            )
            try:
                text = fetch_text(url, key)
                rows.extend(parse_csv(text, source, widened, seen, header_printed))
            except Exception as exc:
                message = s07_fires.redact(str(exc), key)
                say(f"{source} skipped: {message}")
            time.sleep(0.3)
    finally:
        sys.exit = real_exit
    kept = []
    for row in rows[:20]:
        kept.append({
            "lat": row["lat"],
            "lon": row["lon"],
            "date": row["date"],
            "frp": row["frp_mw"],
        })
    return len(rows), kept


def load_cache(region_id):
    path = CACHE_DIR / f"{region_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_cache(region_id, payload):
    write_json(CACHE_DIR / f"{region_id}.json", payload)


def observe(region, force, skip_s2):
    started = time.perf_counter()
    cached = None if force else load_cache(region["id"])
    thumb_path = OUT_DIR / "thumbs" / f"{region['id']}.jpg"
    if cached and cached.get("dates") is not None and (skip_s2 or thumb_path.exists() or cached.get("no_thumb")):
        bbox = cached["bbox"]
        dates, ndvi, clear = cached["dates"], cached["ndvi"], cached["clear"]
        cropland_frac = cached.get("cropland_frac")
        cropland_mask = bool(cached.get("cropland_mask"))
        say(f"{region['id']}: reused cache ({len(dates)} dates)")
    elif skip_s2:
        bbox = box_around(region["lat"], region["lon"])
        dates, ndvi, clear = [], [], []
        cropland_frac, cropland_mask = None, False
    else:
        say(f"{region['id']}: snapping cropland box")
        bbox, cropland_frac, cropland_mask = snap_box(region["lat"], region["lon"])
        if cropland_mask and cropland_frac is not None and cropland_frac < 0.5:
            say(f"{region['id']}: cropland fraction {cropland_frac} is under 0.5")
        say(f"{region['id']}: searching Sentinel-2")
        items = search_scenes(bbox)
        say(f"{region['id']}: {len(items)} scenes under {CLOUD_MAX}% cloud")
        if time.perf_counter() - started > 600:
            raise TimeoutError("search exceeded 10 minutes")
        dates, ndvi, clear, fine_frac = series_from_items(items, bbox, cropland_mask)
        if fine_frac is not None:
            cropland_frac = fine_frac
        if not cropland_mask:
            cropland_frac = None
        thumb_day = latest_thumb_day(dates, clear)
        no_thumb = thumb_day is None
        if thumb_day is not None:
            match = [item for item in items if str(item.datetime.date()) == thumb_day]
            if match:
                try:
                    save_thumb(match[-1], bbox, thumb_path)
                    no_thumb = False
                except Exception as exc:
                    say(f"{region['id']}: thumbnail failed: {type(exc).__name__}")
                    no_thumb = True
        save_cache(region["id"], {
            "bbox": bbox,
            "cropland_frac": cropland_frac,
            "cropland_mask": cropland_mask,
            "dates": dates,
            "ndvi": ndvi,
            "clear": clear,
            "no_thumb": no_thumb,
        })
        if time.perf_counter() - started > 600:
            say(f"{region['id']}: over 10 minutes; kept what was cached and moved on")
    pairs = [(day, value) for day, value in zip(dates, ndvi) if value is not None]
    stage = stage_of(pairs)
    lat = round((bbox[1] + bbox[3]) / 2, 5)
    lon = round((bbox[0] + bbox[2]) / 2, 5)
    return {
        "bbox": bbox,
        "lat": lat,
        "lon": lon,
        "cropland_frac": cropland_frac,
        "cropland_mask": cropland_mask,
        "dates": dates,
        "ndvi": ndvi,
        "clear": clear,
        "stage": stage,
        "clear_passes": len(pairs),
        "thumb": thumb_path.exists(),
    }


def region_entry(region, observed, forecast, fires_5d, fires):
    rain, flag = rain_from(forecast)
    stage = observed["stage"]
    alert = None
    if stage and flag and stage["stage"] in ("peak canopy", "drying down"):
        alert = "rain on a standing crop in the next 3 days"
    if stage is None:
        return {
            "id": region["id"],
            "name": region["name"],
            "country": region["country"],
            "lat": observed["lat"],
            "lon": observed["lon"],
            "bbox": observed["bbox"],
            "crops": region["crops"],
            "crops_note": "hand-labelled, not detected",
            "status": "no_clear_data",
            "cropland_frac": observed["cropland_frac"],
            "cropland_mask": observed["cropland_mask"],
            "forecast": forecast,
            "rain_next3_mm": rain,
            "rain_flag": flag,
            "fires_5d": fires_5d,
            "fires": fires,
            "headline": headline_for(None, rain, fires_5d),
        }
    return {
        "id": region["id"],
        "name": region["name"],
        "country": region["country"],
        "lat": observed["lat"],
        "lon": observed["lon"],
        "bbox": observed["bbox"],
        "crops": region["crops"],
        "crops_note": "hand-labelled, not detected",
        "cropland_frac": observed["cropland_frac"],
        "cropland_mask": observed["cropland_mask"],
        "clear_passes": observed["clear_passes"],
        "latest_clear_date": stage["current"]["date"],
        "ndvi": {"dates": observed["dates"], "values": observed["ndvi"]},
        "peak": stage["peak"],
        "current": stage["current"],
        "stage": stage["stage"],
        "stage_reason": stage["stage_reason"],
        "forecast": forecast,
        "rain_next3_mm": rain,
        "rain_flag": flag,
        "alert": alert,
        "fires_5d": fires_5d,
        "fires": fires,
        "thumb": f"thumbs/{region['id']}.jpg" if observed["thumb"] else None,
        "headline": headline_for(stage, rain, fires_5d),
    }


def error_entry(region, reason):
    return {
        "id": region["id"],
        "name": region["name"],
        "country": region["country"],
        "lat": region["lat"],
        "lon": region["lon"],
        "crops": region["crops"],
        "crops_note": "hand-labelled, not detected",
        "status": f"error: {reason[:180]}",
        "headline": f"No reading: {reason[:120]}",
    }


def validate(doc):
    schema = json.loads(SCHEMA_PATH.read_text())
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(doc),
        key=lambda err: list(err.path),
    )
    if errors:
        for err in errors[:8]:
            say(f"schema: {list(err.path)} {err.message}")
        raise ValueError(f"{len(errors)} schema errors")


def write_atomic(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, path)


def build_preview(regions):
    columns = 6
    rows = max(1, math.ceil(len(regions) / columns))
    thumb_w, thumb_h = 200, 180
    label_h = 52
    cell_w, cell_h = thumb_w + 16, thumb_h + label_h + 8
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    for index, region in enumerate(regions):
        col, row = index % columns, index // columns
        x, y = col * cell_w + 8, row * cell_h + 6
        thumb = OUT_DIR / "thumbs" / f"{region['id']}.jpg"
        if thumb.exists():
            image = Image.open(thumb).convert("RGB")
            image.thumbnail((thumb_w, thumb_h))
            sheet.paste(image, (x, y))
        else:
            draw.rectangle((x, y, x + thumb_w, y + thumb_h), fill=(210, 206, 198))
        stage = region.get("stage") or region.get("status") or "no stage"
        rain = "rain" if region.get("rain_flag") else "dry"
        fires = region.get("fires_5d")
        fire_text = "fires ?" if fires is None else f"{fires} fires"
        draw.text((x, y + thumb_h + 2), region["name"][:28], fill=(20, 20, 20))
        draw.text((x, y + thumb_h + 18), f"{stage}"[:32], fill=(40, 40, 40))
        draw.text((x, y + thumb_h + 34), f"{rain}  {fire_text}", fill=(40, 40, 40))
    path = OUT_DIR / "preview.jpg"
    sheet.save(path, format="JPEG", quality=85)
    say(f"wrote {path}")


def run_region(region, force, skip_s2):
    say(f"--- {region['id']} {region['name']}")
    try:
        observed = observe(region, force, skip_s2)
    except Exception as exc:
        say(f"{region['id']}: {type(exc).__name__}: {exc}")
        return error_entry(region, f"{type(exc).__name__}: {exc}")
    try:
        forecast = fetch_forecast(observed["lat"], observed["lon"])
    except Exception as exc:
        say(f"{region['id']}: forecast {type(exc).__name__}")
        forecast = None
    try:
        fires_5d, fires = firms_for(observed["bbox"])
    except Exception as exc:
        message = s07_fires.redact(str(exc), os.environ.get("FIRMS_MAP_KEY", ""))
        say(f"{region['id']}: fires failed: {message}")
        fires_5d, fires = None, []
    entry = region_entry(region, observed, forecast, fires_5d, fires)
    say(entry.get("headline", ""))
    if entry.get("stage_reason"):
        say(entry["stage_reason"])
    return entry
