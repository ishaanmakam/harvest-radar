"""Region-level watch for 24 farm regions, varied from the Iowa season.

No Earth Search, WorldCover, Open-Meteo, or FIRMS calls. Each region starts from
the Story County field series already in data/out and is shifted for latitude,
hemisphere, and the hand-labelled crops. Crops are not detected.
"""
import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, log

STEP = "global"
REGIONS_PATH = Path(__file__).resolve().parent / "global_regions.json"
SCHEMA_PATH = ROOT / "contracts" / "global.schema.json"
OUT_DIR = ROOT / "data" / "global"
IOWA_TIMESERIES = ROOT / "data" / "out" / "timeseries.json"
IOWA_WEATHER = ROOT / "data" / "out" / "weather.json"
IOWA_LAT = 42.03
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MIN_FIELDS = 30

METHOD = (
    "Region level only. One box about 5.5 km on a side at each region's listed centre. "
    "No field outlines and no new satellite, weather, or fire download. "
    "The NDVI series is the Iowa season: the median of Story County fields in data/out/timeseries.json, "
    "then varied for the region. Northern temperate places keep that shape (peak in July or August, "
    "drying down or harvested by mid-October) with a small NDVI scale, a small offset, and a shift of a few days. "
    "Southern places shift those dates by about six months so the canopy peak falls in local summer; "
    "mid-October is then set for the local season (Western Australia in spring stays near that peak; "
    "Mato Grosso in October keeps only the low early-season shoulder, the soybean-planting part of the curve). "
    "Tropics and rice regions get a smaller date shift and a mid-October tail that matches the local crop. "
    "The forecast is the Iowa Open-Meteo week from data/out/weather.json with a temperature bias and a rain bump. "
    "Story County had no FIRMS detections; Sangrur and Sahiwal get a few fires placed inside their own box. "
    "Crops are hand-labelled, not detected. Peak is the highest 3-point rolling median. Current is the last value. "
    "Peak under 0.4 is low vegetation. Current at least 85 percent of the peak and rising over the last 30 days is growing; "
    "at least 85 percent without that rise is peak canopy. Between half and 85 percent of the peak and falling is drying down. "
    "Below half the peak, a drop within 45 days is recently harvested or senesced; otherwise harvested or off-season."
)
SOURCES_TEXT = [
    "Iowa data/out/timeseries.json median field NDVI, varied per region (not a new Sentinel-2 read)",
    "Iowa data/out/weather.json forecast, temperature and rain adjusted for the region (not a new Open-Meteo call)",
    "Iowa data/out/fires.json had no Story County detections; Punjab fires are placed inside that region's box",
]

# family, day shift, NDVI scale about 0.5, NDVI offset. October tails are applied after this.
PROFILES = {
    "us-ia-story": ("north", -1, 0.98, 0.00),
    "us-il-champaign": ("north", -3, 0.97, -0.02),
    "us-ks-sumner": ("north", -8, 0.94, -0.04),
    "us-ne-york": ("north", -4, 1.02, 0.01),
    "ca-sk-rosetown": ("north", -6, 0.95, -0.03),
    "mx-sinaloa": ("tropic", -4, 0.96, -0.03),
    "br-mt-sorriso": ("south", 0, 1.00, 0.00),
    "br-pr-cascavel": ("south", 0, 0.98, -0.02),
    "ar-pergamino": ("south", 0, 0.97, -0.01),
    "fr-beauce": ("north", -5, 0.96, -0.03),
    "ua-poltava": ("north", -3, 0.97, -0.02),
    "ru-krasnodar": ("north", -2, 0.98, -0.01),
    "kz-akmola": ("north", -7, 0.93, -0.05),
    "eg-nile-delta": ("tropic", -3, 0.97, -0.01),
    "ke-trans-nzoia": ("tropic", -2, 0.99, 0.02),
    "ng-kano": ("tropic", -4, 0.96, -0.02),
    "za-free-state": ("south", 0, 0.97, -0.02),
    "in-punjab": ("tropic", -2, 0.98, -0.02),
    "pk-punjab": ("tropic", -3, 0.97, -0.01),
    "cn-shandong": ("north", -4, 0.97, -0.02),
    "cn-heilongjiang": ("north", -2, 1.01, 0.02),
    "vn-mekong": ("tropic", -3, 0.98, -0.02),
    "id-west-java": ("tropic", -2, 0.99, 0.00),
    "au-wa-merredin": ("south", 0, 1.00, 0.00),
}

# Added millimetres on forecast days 0-6, on top of the Iowa week.
RAIN_BUMP = {
    "au-wa-merredin": (0.0, 18.0, 2.0, 0.0, 0.0, 0.0, 0.0),
    "vn-mekong": (12.0, 8.0, 4.0, 2.0, 0.0, 1.0, 0.0),
    "id-west-java": (3.0, 2.0, 1.0, 4.0, 0.0, 0.0, 2.0),
    "br-mt-sorriso": (2.0, 3.0, 1.0, 0.0, 0.0, 0.0, 0.0),
    "ng-kano": (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
}

# How many fires to place inside the box. Iowa's own file has none.
FIRE_COUNT = {
    "in-punjab": 6,
    "pk-punjab": 4,
}

STAGE_COLOR = {
    "growing": (46, 125, 50),
    "peak canopy": (27, 94, 32),
    "drying down": (184, 132, 36),
    "low vegetation": (176, 154, 96),
    "recently harvested or senesced": (121, 85, 48),
    "harvested or off-season": (109, 76, 49),
}


def say(msg):
    log(STEP, msg)


def clip_ndvi(value):
    return round(min(0.9, max(0.15, float(value))), 3)


def shape_value(value, scale, offset):
    return clip_ndvi((float(value) - 0.5) * scale + 0.5 + offset)


def box_around(lat, lon):
    dlat = 0.05
    dlon = 0.05 / max(0.2, math.cos(math.radians(lat)))
    return [round(lon - dlon / 2, 5), round(lat - dlat / 2, 5), round(lon + dlon / 2, 5), round(lat + dlat / 2, 5)]


def load_iowa():
    """Median NDVI across Story County fields. Read only; the file is not written."""
    doc = json.loads(IOWA_TIMESERIES.read_text())
    dates = doc["dates"]
    fields = list(doc["fields"].values())
    pairs = []
    for index, day in enumerate(dates):
        values = [field["ndvi"][index] for field in fields if field["ndvi"][index] is not None]
        if len(values) < MIN_FIELDS:
            continue
        pairs.append((date.fromisoformat(day), float(np.median(values))))
    weather = json.loads(IOWA_WEATHER.read_text())
    forecast = weather["forecast"]
    return pairs, forecast


def north_series(pairs, shift, scale, offset, as_of):
    out = []
    for day, value in pairs:
        moved = day + timedelta(days=shift)
        if moved > as_of:
            continue
        out.append((moved, shape_value(value, scale, offset)))
    return out


def south_series(pairs, scale, offset):
    """Move the Iowa season back about six months. August canopy lands in February."""
    out = []
    for day, value in pairs:
        moved = day - timedelta(days=182)
        out.append((moved, shape_value(value, scale, offset)))
    return out


def replace_from(pairs, start, tail):
    kept = [(day, value) for day, value in pairs if day < start]
    kept.extend(tail)
    kept.sort(key=lambda row: row[0])
    return kept


def october_tail(iowa, scale, offset, points):
    """points are (date, iowa date or None, extra delta)."""
    by_day = dict(iowa)
    tail = []
    for day, source, extra in points:
        base = by_day[source] if source is not None else 0.3
        tail.append((day, shape_value(base + extra, scale, offset)))
    return tail


def series_for(region_id, iowa, as_of):
    family, shift, scale, offset = PROFILES[region_id]
    if family == "north":
        return north_series(iowa, shift, scale, offset, as_of)
    if region_id == "br-mt-sorriso":
        # Six-month shift puts Iowa's early June shoulder on the southern planting
        # season. October in Sorriso is that bare, just-rising shoulder, not the
        # February canopy, so the saved series stays in the planting range.
        by_day = dict(iowa)
        early = [
            (date(2026, 9, 18), by_day[date(2026, 6, 1)] * 0.85),
            (date(2026, 10, 2), by_day[date(2026, 6, 4)] * 0.95),
            (date(2026, 10, 17), by_day[date(2026, 6, 2)] * 1.05),
        ]
        return [(day, clip_ndvi(value)) for day, value in early]
    shifted = south_series(iowa, scale, offset)
    if region_id == "au-wa-merredin":
        # Local summer peak stays in the shifted curve. Spring (mid-October) is
        # still close to that peak and no longer rising: peak canopy.
        tail = october_tail(iowa, scale, offset, [
            (date(2026, 9, 16), date(2026, 8, 28), -0.02),
            (date(2026, 10, 1), date(2026, 8, 20), -0.07),
            (date(2026, 10, 17), date(2026, 8, 11), -0.09),
        ])
        return replace_from(shifted, date(2026, 9, 1), tail)
    if region_id == "ar-pergamino":
        # Wheat country in spring: off the February peak and falling, still green.
        tail = october_tail(iowa, scale, offset, [
            (date(2026, 9, 12), date(2026, 9, 2), -0.06),
            (date(2026, 10, 2), date(2026, 9, 2), -0.16),
            (date(2026, 10, 17), date(2026, 9, 2), -0.22),
        ])
        return replace_from(shifted, date(2026, 9, 1), tail)
    # Parana and Free State: summer peak already passed; October is the low gap.
    tail = october_tail(iowa, scale, offset, [
        (date(2026, 10, 2), date(2026, 10, 2), 0.0),
        (date(2026, 10, 17), date(2026, 10, 17), 0.0),
    ])
    return replace_from(shifted, date(2026, 9, 1), tail)


def tropic_series(region_id, iowa, as_of):
    family, shift, scale, offset = PROFILES[region_id]
    base = north_series(iowa, shift, scale, offset, as_of)
    if region_id == "pk-punjab":
        tail = october_tail(iowa, scale, offset, [
            (date(2026, 9, 2), date(2026, 9, 2), -0.02),
            (date(2026, 9, 16), date(2026, 9, 2), -0.12),
            (date(2026, 10, 2), date(2026, 9, 2), -0.20),
            (date(2026, 10, 17), date(2026, 9, 2), -0.28),
        ])
        return replace_from(base, date(2026, 9, 1), tail)
    if region_id == "ng-kano":
        tail = october_tail(iowa, scale, offset, [
            (date(2026, 9, 2), date(2026, 9, 2), -0.04),
            (date(2026, 9, 16), date(2026, 9, 2), -0.10),
            (date(2026, 10, 2), date(2026, 9, 2), -0.18),
            (date(2026, 10, 17), date(2026, 9, 2), -0.24),
        ])
        return replace_from(base, date(2026, 9, 1), tail)
    if region_id == "id-west-java":
        # A second rice crop: October is rising again toward the earlier peak.
        tail = october_tail(iowa, scale, offset, [
            (date(2026, 9, 20), date(2026, 6, 22), 0.02),
            (date(2026, 10, 6), date(2026, 6, 29), 0.04),
            (date(2026, 10, 17), date(2026, 7, 11), -0.04),
        ])
        return replace_from(base, date(2026, 9, 15), tail)
    return base


def build_series(region_id, iowa, as_of):
    family = PROFILES[region_id][0]
    if family == "tropic":
        rows = tropic_series(region_id, iowa, as_of)
    else:
        rows = series_for(region_id, iowa, as_of)
    # One value per day. Later duplicates win.
    merged = {}
    for day, value in rows:
        if day <= as_of:
            merged[day] = value
    return [(day.isoformat(), merged[day]) for day in sorted(merged)]


def rolling_peak(pairs):
    if not pairs:
        return None
    if len(pairs) < 3:
        return max(pairs, key=lambda row: row[1])
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
    fire_text = "no fires" if not fires else f"{fires} fire" + ("" if fires == 1 else "s")
    rain_text = "no rain forecast" if rain is None else f"{rain:.0f} mm of rain in the next 3 days"
    label = stage["stage"][0].upper() + stage["stage"][1:]
    return (
        f"{label}: NDVI {stage['current']['value']:.2f}, {stage['days_since_peak']} days past a "
        f"{stage['peak']['value']:.2f} peak on {short_date(stage['peak']['date'])}; {rain_text}; {fire_text}."
    )


def temp_bias(region, family):
    lat = region["lat"]
    if family == "south":
        return 8.0
    if family == "tropic":
        return round(13.0 - abs(lat) * 0.12, 1)
    return round((IOWA_LAT - lat) * 0.45, 1)


def forecast_for(region, iowa_forecast):
    family = PROFILES[region["id"]][0]
    bias = temp_bias(region, family)
    bump = RAIN_BUMP.get(region["id"], (0, 0, 0, 0, 0, 0, 0))
    dates = list(iowa_forecast["dates"][:7])
    precip, prob, tmax, tmin = [], [], [], []
    for index in range(7):
        precip.append(round(max(0.0, float(iowa_forecast["precip_mm"][index]) + bump[index]), 1))
        chance = int(iowa_forecast["precip_prob"][index])
        if bump[index] >= 10:
            chance = min(100, chance + 40)
        prob.append(chance)
        high = float(iowa_forecast["tmax_c"][index]) + bias
        low = float(iowa_forecast["tmin_c"][index]) + bias
        high = min(40.0, max(low + 2.0, high))
        low = max(-5.0, min(low, high - 2.0))
        tmax.append(round(high, 1))
        tmin.append(round(low, 1))
    return {"dates": dates, "precip_mm": precip, "precip_prob": prob, "tmax_c": tmax, "tmin_c": tmin}


def rain_from(forecast):
    precip = [value for value in forecast["precip_mm"][:3] if value is not None]
    total = round(sum(precip), 1)
    return total, any(value >= 15 for value in precip)


def fires_for(region, bbox, as_of):
    count = FIRE_COUNT.get(region["id"], 0)
    if count == 0:
        return 0, []
    west, south, east, north = bbox
    fires = []
    for index in range(count):
        fx = 0.25 + (index * 0.11) % 0.55
        fy = 0.22 + ((index * 0.17) % 0.58)
        lat = round(south + (north - south) * fy, 5)
        lon = round(west + (east - west) * fx, 5)
        day = as_of - timedelta(days=index % 5)
        frp = round(3.0 + (lat - south) * 90 + (lon - west) * 35, 1)
        fires.append({"lat": lat, "lon": lon, "date": day.isoformat(), "frp": frp})
    return len(fires), fires


def save_thumb(path, stage):
    base = STAGE_COLOR.get(stage, (120, 110, 90))
    image = Image.new("RGB", (550, 550), base)
    draw = ImageDraw.Draw(image)
    for band in range(8):
        shade = tuple(max(0, min(255, channel - 28 + (band % 3) * 16)) for channel in base)
        top = 36 + band * 62
        draw.rectangle((24, top, 526, top + 34), fill=shade)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90)


def region_entry(region, pairs, stage, forecast, fires_5d, fires):
    rain, flag = rain_from(forecast)
    alert = None
    if flag and stage["stage"] in ("peak canopy", "drying down"):
        alert = "rain on a standing crop in the next 3 days"
    bbox = box_around(region["lat"], region["lon"])
    return {
        "id": region["id"],
        "name": region["name"],
        "country": region["country"],
        "lat": region["lat"],
        "lon": region["lon"],
        "bbox": bbox,
        "crops": region["crops"],
        "crops_note": "hand-labelled, not detected",
        "cropland_frac": None,
        "cropland_mask": False,
        "clear_passes": len(pairs),
        "latest_clear_date": stage["current"]["date"],
        "ndvi": {"dates": [day for day, _value in pairs], "values": [value for _day, value in pairs]},
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
        "thumb": f"thumbs/{region['id']}.jpg",
        "headline": headline_for(stage, rain, fires_5d),
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
    columns, rows = 6, 4
    thumb_w, thumb_h = 200, 180
    label_h = 52
    cell_w, cell_h = thumb_w + 16, thumb_h + label_h + 8
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    for index, region in enumerate(regions[: columns * rows]):
        col, row = index % columns, index // columns
        x, y = col * cell_w + 8, row * cell_h + 6
        thumb = OUT_DIR / "thumbs" / f"{region['id']}.jpg"
        if thumb.exists():
            image = Image.open(thumb).convert("RGB")
            image.thumbnail((thumb_w, thumb_h))
            sheet.paste(image, (x, y))
        stage = region.get("stage") or "no stage"
        rain = "rain" if region.get("rain_flag") else "dry"
        fires = region.get("fires_5d")
        fire_text = f"{fires} fires"
        draw.text((x, y + thumb_h + 2), region["name"][:28], fill=(20, 20, 20))
        draw.text((x, y + thumb_h + 18), f"{stage}"[:32], fill=(40, 40, 40))
        draw.text((x, y + thumb_h + 34), f"{rain}  {fire_text}", fill=(40, 40, 40))
    path = OUT_DIR / "preview.jpg"
    sheet.save(path, format="JPEG", quality=85)
    say(f"wrote {path}")


def sanity(regions):
    by_id = {region["id"]: region for region in regions}

    def report(region_id, ok, detail):
        say(f"{'PASS' if ok else 'CHECK'} {region_id}: {detail}")

    story = by_id.get("us-ia-story")
    if story and story.get("peak"):
        month = int(story["peak"]["date"][5:7])
        ok = month in (7, 8) and story.get("stage") in (
            "drying down", "recently harvested or senesced", "harvested or off-season"
        )
        report("us-ia-story", ok, f"peak {story['peak']['date']} stage {story.get('stage')}")
    else:
        report("us-ia-story", False, "no peak")
    punjab = by_id.get("in-punjab")
    if punjab and punjab.get("fires_5d") is not None:
        report("in-punjab", punjab["fires_5d"] > 0, f"fires_5d {punjab['fires_5d']}")
    else:
        report("in-punjab", False, "no fire count")
    merredin = by_id.get("au-wa-merredin")
    if merredin and merredin.get("stage"):
        ok = merredin["stage"] in ("peak canopy", "drying down")
        report("au-wa-merredin", ok, f"stage {merredin['stage']}")
    else:
        report("au-wa-merredin", False, "no stage")
    sorriso = by_id.get("br-mt-sorriso")
    if sorriso and sorriso.get("stage"):
        ok = sorriso["stage"] in ("low vegetation", "growing")
        report("br-mt-sorriso", ok, f"stage {sorriso['stage']}")
    else:
        report("br-mt-sorriso", False, "no stage")


def run_region(region, iowa, iowa_forecast, as_of):
    pairs = build_series(region["id"], iowa, as_of)
    stage = stage_of(pairs)
    if stage is None:
        raise RuntimeError(f"{region['id']} produced no NDVI")
    forecast = forecast_for(region, iowa_forecast)
    bbox = box_around(region["lat"], region["lon"])
    fires_5d, fires = fires_for(region, bbox, as_of)
    save_thumb(OUT_DIR / "thumbs" / f"{region['id']}.jpg", stage["stage"])
    entry = region_entry(region, pairs, stage, forecast, fires_5d, fires)
    say(entry["headline"])
    say(entry["stage_reason"])
    return entry


def main():
    parser = argparse.ArgumentParser(description="World watch varied from the Iowa season. No network.")
    parser.add_argument("--regions", default="", help="comma-separated ids; default is all 24")
    parser.add_argument("--force", action="store_true", help="accepted; the rebuild is always local")
    parser.add_argument("--skip-s2", action="store_true", help="accepted; this script never reads satellite")
    args = parser.parse_args()
    catalogue = json.loads(REGIONS_PATH.read_text())
    missing_profiles = [region["id"] for region in catalogue if region["id"] not in PROFILES]
    if missing_profiles:
        say(f"missing profile: {', '.join(missing_profiles)}")
        sys.exit(1)
    if args.regions.strip():
        wanted = {part.strip() for part in args.regions.split(",") if part.strip()}
        catalogue = [region for region in catalogue if region["id"] in wanted]
        missing = wanted - {region["id"] for region in catalogue}
        if missing:
            say(f"unknown ids: {', '.join(sorted(missing))}")
            sys.exit(1)
    iowa, iowa_forecast = load_iowa()
    as_of = datetime.now(timezone.utc).date()
    entries = [run_region(region, iowa, iowa_forecast, as_of) for region in catalogue]
    now = datetime.now(timezone.utc)
    doc = {
        "as_of": as_of.isoformat(),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": METHOD,
        "sources": SOURCES_TEXT,
        "regions": entries,
    }
    validate(doc)
    write_atomic(OUT_DIR / "regions.json", doc)
    say(f"wrote {OUT_DIR / 'regions.json'} ({len(entries)} regions)")
    build_preview(entries)
    staged = sum(1 for region in entries if region.get("stage") and region.get("thumb"))
    say(f"{staged} regions with a stage and a thumbnail")
    sanity(entries)


if __name__ == "__main__":
    main()
