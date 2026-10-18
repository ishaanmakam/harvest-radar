"""NASA FIRMS VIIRS detections inside the demo box, tagged to fields."""
import argparse
import csv
import io
import json
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import requests
from jsonschema import Draft202012Validator
from pyproj import Transformer
from shapely import transform
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from common import CACHE, OUT, ROOT, load_env, load_region, log, write_json

STEP = "s07"
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
SOURCE_TEXT = "NASA FIRMS VIIRS NRT (SNPP, NOAA-20, NOAA-21), area API"
AVAILABILITY_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{west},{south},{east},{north}/{days}/{start}"
NEEDED = ("latitude", "longitude", "acq_date", "acq_time", "confidence", "frp")
CHUNK_DAYS = 5  # the area API answers "Invalid day range. Expects [1..5]."; 5 is within the 10-day cap
TO_METRES = Transformer.from_crs("EPSG:4326", "EPSG:32615", always_xy=True)
FIRMS_DIR = CACHE / "firms"


def fail(msg):
    log(STEP, f"ERROR {msg}")
    sys.exit(1)


def redact(text, key):
    shown = str(text)
    if key:
        shown = shown.replace(key, "***")
    return shown


def parse_day(text):
    return date.fromisoformat(str(text)[:10])


def days_between(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def ranges(days):
    if not days:
        return "none"
    groups = []
    begin = prev = days[0]
    for day in days[1:]:
        if day == prev + timedelta(days=1):
            prev = day
            continue
        groups.append(begin.isoformat() if begin == prev else f"{begin.isoformat()}..{prev.isoformat()}")
        begin = prev = day
    groups.append(begin.isoformat() if begin == prev else f"{begin.isoformat()}..{prev.isoformat()}")
    return ", ".join(groups)


def expand(bbox, pad):
    west, south, east, north = bbox
    return (round(west - pad, 5), round(south - pad, 5), round(east + pad, 5), round(north + pad, 5))


def inside(lon, lat, box):
    west, south, east, north = box
    return west <= lon <= east and south <= lat <= north


def project(geom):
    def apply(coords):
        x, y = TO_METRES.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])
    return transform(geom, apply)


def fetch_text(url, key, timeout=90):
    last = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code != 200:
                snippet = redact(response.text[:200], key).replace("\n", " ")
                raise requests.HTTPError(f"HTTP {response.status_code}: {snippet}")
            text = response.content.decode("utf-8-sig")
            if not text.strip() or text.lstrip().startswith("<"):
                raise ValueError("response is not a CSV")
            return text
        except (requests.RequestException, ValueError) as exc:
            last = redact(exc, key)
            log(STEP, f"request failed ({attempt}/3): {last}")
            if attempt < 3:
                time.sleep(2 * attempt)
    fail(f"giving up after 3 tries: {last}")


def load_key():
    load_env()
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        fail("FIRMS_MAP_KEY is missing. Add it to .env and run again.")
    return key


def availability_windows(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        fail("availability CSV has no rows")
    header = list(rows[0].keys())
    wanted = {name.lower(): name for name in header}
    if not {"data_id", "min_date", "max_date"} <= set(wanted):
        fail(f"availability CSV header is {header}")
    windows = {}
    for row in rows:
        name = row[wanted["data_id"]].strip().strip('"')
        if name not in SOURCES:
            continue
        windows[name] = (parse_day(row[wanted["min_date"]]), parse_day(row[wanted["max_date"]]))
    return windows


def chunk_plan(start, end, window):
    if window is None:
        return [], list(days_between(start, end))
    avail_start, avail_end = window
    covered_start = max(start, avail_start)
    covered_end = min(end, avail_end)
    missing = [day for day in days_between(start, end) if day < avail_start or day > avail_end]
    chunks = []
    if covered_start <= covered_end:
        day = covered_start
        while day <= covered_end:
            last = min(covered_end, day + timedelta(days=CHUNK_DAYS - 1))
            chunks.append((day, (last - day).days + 1))
            day = last + timedelta(days=1)
    return chunks, missing


def require_detection_header(fieldnames):
    missing = [name for name in NEEDED if name not in fieldnames]
    if missing:
        fail(f"detection CSV header {list(fieldnames)} is missing {missing}")


def parse_csv(text, source, keep_box, seen, header_printed):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        fail(f"{source} CSV has no header")
    if not header_printed[0]:
        log(STEP, f"header: {','.join(reader.fieldnames)}")
        header_printed[0] = True
    require_detection_header(reader.fieldnames)
    kept = []
    for row in reader:
        try:
            lat = round(float(row["latitude"]), 5)
            lon = round(float(row["longitude"]), 5)
            day = parse_day(row["acq_date"]).isoformat()
        except (TypeError, ValueError):
            fail(f"{source} has a row that is not latitude/longitude/acq_date")
        if not inside(lon, lat, keep_box):
            continue
        clock = str(row["acq_time"]).strip()
        if not clock.isdigit():
            fail(f"{source} acq_time {clock!r} is not HHMM")
        clock = clock.zfill(4)
        if len(clock) != 4:
            fail(f"{source} acq_time {clock!r} is not HHMM")
        frp = str(row["frp"]).strip()
        try:
            frp_mw = None if frp == "" else float(frp)
        except ValueError:
            fail(f"{source} frp {frp!r} is not a number")
        key = (source, lat, lon, day, clock)
        if key in seen:
            continue
        seen.add(key)
        kept.append({
            "lon": lon,
            "lat": lat,
            "date": day,
            "time_utc": clock,
            "satellite": source,
            "confidence": str(row["confidence"]).strip(),
            "frp_mw": frp_mw,
            "field_id": None,
        })
    return kept


def load_fields():
    path = OUT / "fields.geojson"
    if not path.exists():
        fail(f"missing {path}")
    doc = json.loads(path.read_text())
    features = doc["features"]
    geoms = [shape(feature["geometry"]) for feature in features]
    metric = [project(geom) for geom in geoms]
    ids = [feature["properties"]["field_id"] for feature in features]
    tree = STRtree(geoms)
    tree_m = STRtree(metric)

    def assign(lon, lat):
        point = Point(lon, lat)
        hits = [int(index) for index in tree.query(point, predicate="intersects")]
        if hits:
            return ids[min(hits, key=lambda index: ids[index])], "inside"
        point_m = Point(*TO_METRES.transform(lon, lat))
        best = None
        for index in tree_m.query(point_m.buffer(400)):
            index = int(index)
            distance = metric[index].distance(point_m)
            if distance > 400:
                continue
            if best is None or distance < best[0] or (distance == best[0] and ids[index] < best[1]):
                best = (distance, ids[index])
        if best:
            return best[1], "near"
        return None, None

    return ids, assign


def tag(detections, assign):
    inside_n = near_n = 0
    for detection in detections:
        field_id, kind = assign(detection["lon"], detection["lat"])
        detection["field_id"] = field_id
        if kind == "inside":
            inside_n += 1
        elif kind == "near":
            near_n += 1
    return inside_n, near_n


def validate(doc, known_ids):
    schema = json.loads((ROOT / "contracts" / "fires.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(doc))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR fires.json @ {loc}: {err.message}")
    order = ["lon", "lat", "date", "time_utc", "satellite", "confidence", "frp_mw", "field_id"]
    for detection in doc["detections"]:
        if list(detection) != order:
            errors.append("order")
            log(STEP, f"ERROR detection key order {list(detection)}")
            break
        if detection["field_id"] is not None and detection["field_id"] not in known_ids:
            errors.append("field")
            log(STEP, f"ERROR {detection['field_id']} is not in fields.geojson")
    if errors:
        fail("validation failed; data/out/fires.json left untouched")


def publish(doc):
    staging = CACHE / "fires_staging.json"
    write_json(staging, doc)
    os.replace(staging, OUT / "fires.json")
    log(STEP, f"wrote {OUT / 'fires.json'}")


def report(doc, per_source, inside_n, near_n, missing, as_of):
    detections = doc["detections"]
    last5 = as_of - timedelta(days=4)
    recent = [item for item in detections if parse_day(item["date"]) >= last5]
    since = date(as_of.year, 9, 15)
    fields = sorted({item["field_id"] for item in detections if item["field_id"] and parse_day(item["date"]) >= since})
    for source in SOURCES:
        gap = missing.get(source) or []
        log(STEP, f"{source}: {per_source.get(source, 0)} in the box; days not covered: {ranges(gap)}")
    log(STEP, f"{len(detections)} detections in the box | tagged inside {inside_n} | within 400 m {near_n}")
    log(STEP, f"last 5 days ({last5.isoformat()}..{as_of.isoformat()}): {len(recent)} detections")
    uncovered_recent = sorted({day for days in missing.values() for day in days if day >= last5})
    if uncovered_recent:
        log(STEP, f"FIRMS does not cover these of the last 5 days: {', '.join(day.isoformat() for day in uncovered_recent)}")
    log(STEP, f"fields with a detection since {since.isoformat()}: {', '.join(fields) if fields else 'none'}")


def read_saved(path, key):
    if not path.exists():
        fail(f"missing {path.name}. Run once without --from-cache.")
    return path.read_text()


def main():
    parser = argparse.ArgumentParser(description="Download FIRMS VIIRS detections and tag them to fields")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--start", default="2026-09-01")
    args = parser.parse_args()
    region = load_region()
    start = parse_day(args.start)
    as_of = parse_day(region["as_of"])
    if start > as_of:
        fail(f"start {start.isoformat()} is after as_of {as_of.isoformat()}")
    request_box = expand(region["bbox"], 0.01)
    keep_box = expand(region["bbox"], 0.005)
    FIRMS_DIR.mkdir(parents=True, exist_ok=True)
    key = None if args.from_cache else load_key()
    if args.from_cache:
        log(STEP, "rebuilding from cache, no network")
        availability = availability_windows(read_saved(FIRMS_DIR / "availability.csv", key))
    else:
        text = fetch_text(AVAILABILITY_URL.format(key=key), key)
        (FIRMS_DIR / "availability.csv").write_text(text)
        availability = availability_windows(text)
        time.sleep(1)
    for source in SOURCES:
        window = availability.get(source)
        if window is None:
            log(STEP, f"{source}: not in the availability file")
        else:
            log(STEP, f"{source}: available {window[0].isoformat()} .. {window[1].isoformat()}")

    seen = set()
    detections = []
    per_source = {source: 0 for source in SOURCES}
    missing = {}
    header_printed = [False]
    for source in SOURCES:
        chunks, gap = chunk_plan(start, as_of, availability.get(source))
        missing[source] = gap
        for chunk_start, day_count in chunks:
            path = FIRMS_DIR / f"{source}_{chunk_start.isoformat()}.csv"
            if args.from_cache:
                text = read_saved(path, key)
            else:
                url = AREA_URL.format(
                    key=key, source=source,
                    west=request_box[0], south=request_box[1], east=request_box[2], north=request_box[3],
                    days=day_count, start=chunk_start.isoformat(),
                )
                text = fetch_text(url, key)
                path.write_text(text)
                time.sleep(1)
            rows = parse_csv(text, source, keep_box, seen, header_printed)
            chunk_end = chunk_start + timedelta(days=day_count - 1)
            outside = sorted({row["date"] for row in rows if not (chunk_start.isoformat() <= row["date"] <= chunk_end.isoformat())})
            if outside:
                fail(f"{source} {chunk_start.isoformat()} returned dates outside the chunk: {outside[:5]}")
            per_source[source] += len(rows)
            detections.extend(rows)
            log(STEP, f"{source} {chunk_start.isoformat()} +{day_count - 1}d: {len(rows)} in the box")

    known, assign = load_fields()
    inside_n, near_n = tag(detections, assign)
    detections.sort(key=lambda item: (item["date"], item["time_utc"], item["satellite"], item["lat"], item["lon"]))
    doc = {"source": SOURCE_TEXT, "detections": detections}
    validate(doc, set(known))
    publish(doc)
    log(STEP, f"requested {start.isoformat()} .. {as_of.isoformat()}")
    report(doc, per_source, inside_n, near_n, missing, as_of)


if __name__ == "__main__":
    main()
