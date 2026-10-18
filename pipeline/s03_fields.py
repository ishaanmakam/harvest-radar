"""Segment crop fields from the NDVI cube and write frozen field outlines."""
import argparse
import json
import os
import sys
from datetime import date

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw
from pyproj import Geod
from rasterio.features import shapes
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from skimage.segmentation import felzenszwalb

from common import CACHE, OUT, ROOT, load_region, log, write_json
from geo import region_geobox

STEP = "s03"
SCALES = (50, 100, 200, 400)
MIN_SIZES = (100, 200)
GEOD = Geod(ellps="WGS84")
COLORS = {"corn": (255, 255, 0), "soybean": (0, 255, 255), "unknown": (255, 0, 255)}


def fail(msg):
    log(STEP, f"ERROR {msg}")
    sys.exit(1)


def parse_day(text):
    return date.fromisoformat(str(text))


def zone_of(lon, lat, region):
    """Same formula as scripts/make_mock_data.py zone_of, clamped onto the grid."""
    west, south, east, north = region["bbox"]
    gx, gy = region["zones_grid"]
    i = min(gx - 1, max(0, int((lon - west) / (east - west) * gx)))
    j = min(gy - 1, max(0, int((north - lat) / (north - south) * gy)))
    return f"Z{j * gx + i + 1:02d}"


def load_cube(region, frame_box):
    path = CACHE / "cube.npz"
    if not path.exists():
        fail(f"missing {path}. Run pipeline/s02_cube.py first.")
    cube = np.load(path)
    for key in ("dates", "ndvi", "clear"):
        if key not in cube.files:
            fail(f"cube.npz has no {key} array")
    dates = [str(day) for day in cube["dates"]]
    ndvi = np.asarray(cube["ndvi"])
    clear = np.asarray(cube["clear"])
    if ndvi.ndim != 3 or ndvi.shape[1:] != (frame_box.height, frame_box.width):
        fail(f"cube ndvi shape {ndvi.shape} does not match the frame grid")
    if clear.shape != ndvi.shape or len(dates) != ndvi.shape[0]:
        fail("cube dates, ndvi and clear do not line up")
    return dates, ndvi, clear


def choose_windows(dates, clear, as_of):
    fracs = {day: float(clear[index].mean()) for index, day in enumerate(dates)}
    year = as_of.year
    specs = [
        ("W1", date(year, 6, 1), date(year, 6, 15)),
        ("W2", date(year, 6, 20), date(year, 7, 10)),
        ("W3", date(year, 7, 15), date(year, 8, 15)),
        ("W4", date(year, 8, 25), date(year, 9, 15)),
        ("W5", date(year, 9, 20), date(year, 10, 5)),
        ("W6", date(year, 10, 6), as_of),
    ]
    chosen = {}
    skipped = []
    for name, start, end in specs:
        pool = [
            (day, fracs[day])
            for day in dates
            if start <= parse_day(day) <= end and fracs[day] >= 0.7
        ]
        if not pool:
            skipped.append(name)
            log(STEP, f"{name} skipped ({start.isoformat()} .. {end.isoformat()})")
            continue
        # highest clear fraction; earliest date wins a tie
        day, frac = max(pool, key=lambda item: (item[1], -parse_day(item[0]).toordinal()))
        chosen[name] = {"date": day, "clear_frac": round(frac, 4), "fallback": False}
        log(STEP, f"{name} {day} clear_frac={frac:.4f}")
    if "W1" not in chosen:
        early = [
            (day, fracs[day])
            for day in dates
            if parse_day(day) < date(year, 7, 1) and fracs[day] >= 0.7
        ]
        if early:
            day, frac = min(early, key=lambda item: parse_day(item[0]))
            chosen["W1"] = {"date": day, "clear_frac": round(frac, 4), "fallback": True}
            if "W1" in skipped:
                skipped.remove("W1")
            log(STEP, f"W1 missing in Jun 1-15; using earliest date before Jul 1: {day} clear_frac={frac:.4f}")
    if skipped:
        log(STEP, f"skipped windows: {', '.join(skipped)}")
    if "W3" not in chosen or len(chosen) < 4:
        fail(f"need W3 and at least 4 windows, have {sorted(chosen)}")
    return chosen


def feature_stack(dates, ndvi, chosen):
    order = [name for name in ("W1", "W2", "W3", "W4", "W5", "W6") if name in chosen]
    index = {day: i for i, day in enumerate(dates)}
    planes = [np.asarray(ndvi[index[chosen[name]["date"]]], dtype=np.float32) for name in order]
    stack = np.clip(np.stack(planes, axis=-1), 0, 1)
    finite = np.isfinite(stack)
    count = finite.sum(axis=2)
    total = np.nansum(stack, axis=2)
    mean = np.divide(total, count, out=np.zeros(count.shape, dtype=np.float32), where=count > 0)
    filled = np.where(finite, stack, mean[..., None])
    return np.where(np.isfinite(filled), filled, 0).astype(np.float32), order


def segment_medians(labels, planes, masks):
    """Median NDVI per label per date. Null when under half the segment is clear."""
    flat = labels.ravel()
    order = np.argsort(flat, kind="mergesort")
    sorted_labels = flat[order]
    cuts = np.flatnonzero(np.diff(sorted_labels)) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, flat.size]
    ids = [int(value) for value in sorted_labels[starts]]
    values = {label: [None] * len(planes) for label in ids}
    for channel, (plane, mask) in enumerate(zip(planes, masks)):
        samples = plane.ravel()[order]
        clear = mask.ravel()[order]
        for label, start, end in zip(ids, starts, ends):
            size = end - start
            keep = clear[start:end]
            n_clear = int(keep.sum())
            if size == 0 or n_clear / size < 0.5:
                continue
            values[label][channel] = float(np.median(samples[start:end][keep]))
    return values


def keep_label(values, names):
    if "W1" not in names or "W3" not in names:
        return False
    w1 = values[names.index("W1")]
    w3 = values[names.index("W3")]
    if w1 is None or w3 is None:
        return False
    return w1 <= 0.5 and w3 >= 0.65 and (w3 - w1) >= 0.25


def polygons_of(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return [part for part in geom.geoms if not part.is_empty]
    if geom.geom_type == "GeometryCollection":
        out = []
        for part in geom.geoms:
            out.extend(polygons_of(part))
        return out
    return []


def vectorize(labels, kept, medians, names, affine):
    remap = np.zeros(int(labels.max()) + 1, dtype=np.int32)
    for new_id, old in enumerate(kept, start=1):
        remap[old] = new_id
    src = remap[labels]
    pieces = {}
    for geom, value in shapes(src, mask=src > 0, transform=affine):
        label = int(value)
        if label == 0:
            continue
        pieces.setdefault(label, []).append(shape(geom))
    fields = []
    for label, parts in pieces.items():
        geom = unary_union(parts).buffer(0)
        geom = geom.simplify(0.00003, preserve_topology=True)
        if not geom.is_valid:
            geom = geom.buffer(0)
        parts = polygons_of(geom)
        if not parts:
            continue
        if len(parts) > 1:
            largest = max(parts, key=lambda part: part.area)
            total = sum(part.area for part in parts)
            if total <= 0 or largest.area < 0.8 * total:
                continue
            geom = largest
        else:
            geom = parts[0]
        area_m2, _perim = GEOD.geometry_area_perimeter(geom)
        area_ha = abs(area_m2) / 10000.0
        if area_ha < 2 or area_ha > 300:
            continue
        old = kept[label - 1]
        series = {name: medians[old][index] for index, name in enumerate(names)}
        fields.append({"geom": geom, "area_ha": area_ha, "series": series})
    return fields


def kmeans_1d(values):
    centres = [float(np.min(values)), float(np.max(values))]
    data = np.asarray(values, dtype=np.float64)
    for _ in range(50):
        distance = np.abs(data[:, None] - np.asarray(centres))
        group = distance.argmin(axis=1)
        updated = []
        for cluster in (0, 1):
            members = data[group == cluster]
            if members.size == 0:
                return None
            updated.append(float(members.mean()))
        if updated == centres:
            break
        centres = updated
    centres.sort()
    return centres


def label_crops(fields, names):
    if "W2" not in names:
        for field in fields:
            field["crop"] = "unknown"
        log(STEP, "W2 missing; every field labelled unknown")
        return None, None
    w2_index = names.index("W2")
    samples = [field["series"]["W2"] for field in fields if field["series"]["W2"] is not None]
    if len(samples) < 2:
        for field in fields:
            field["crop"] = "unknown"
        log(STEP, "fewer than 2 W2 values; every field labelled unknown")
        return None, None
    centres = kmeans_1d(samples)
    if centres is None or abs(centres[1] - centres[0]) < 0.15:
        for field in fields:
            field["crop"] = "unknown"
        gap = None if centres is None else centres[1] - centres[0]
        log(STEP, f"crop centres {centres} gap {gap}; every field labelled unknown")
        return centres, None
    threshold = (centres[0] + centres[1]) / 2
    counts = {"corn": 0, "soybean": 0, "unknown": 0}
    for field in fields:
        value = field["series"]["W2"]
        if value is None or abs(value - threshold) <= 0.05:
            field["crop"] = "unknown"
        elif value > threshold:
            field["crop"] = "corn"
        else:
            field["crop"] = "soybean"
        counts[field["crop"]] += 1
    log(STEP, f"crop centres {[round(c, 3) for c in centres]} threshold {threshold:.3f} counts {counts}")
    if "W5" in names:
        for crop in ("corn", "soybean"):
            vals = [field["series"]["W5"] for field in fields if field["crop"] == crop and field["series"]["W5"] is not None]
            if vals:
                log(STEP, f"mean W5 {crop} {float(np.mean(vals)):.3f} (n={len(vals)})")
    return centres, threshold


def round_polygon(geom):
    def ring(line):
        return [[round(x, 6), round(y, 6)] for x, y in line.coords]
    return {"type": "Polygon", "coordinates": [ring(geom.exterior), *[ring(hole) for hole in geom.interiors]]}


def assign_ids(fields, region):
    def sort_key(field):
        centre = field["geom"].centroid
        return (-round(centre.y, 3), centre.x)

    ordered = sorted(fields, key=sort_key)
    features = []
    for number, field in enumerate(ordered, start=1):
        centre = field["geom"].centroid
        field["field_id"] = f"F{number:04d}"
        field["zone_id"] = zone_of(centre.x, centre.y, region)
        features.append({
            "type": "Feature",
            "geometry": round_polygon(field["geom"]),
            "properties": {
                "field_id": field["field_id"],
                "zone_id": field["zone_id"],
                "area_ha": round(field["area_ha"], 2),
                "crop": field["crop"],
                "source": "segmentation",
            },
        })
    return features


def overlaps(features):
    geoms = [shape(feature["geometry"]) for feature in features]
    if len(geoms) < 2:
        return None
    tree = STRtree(geoms)
    for index, geom in enumerate(geoms):
        for other in tree.query(geom, predicate="intersects"):
            if other <= index:
                continue
            shared = geom.intersection(geoms[other]).area
            smaller = min(geom.area, geoms[other].area)
            if smaller > 0 and shared / smaller > 0.01:
                left = features[index]["properties"]["field_id"]
                right = features[other]["properties"]["field_id"]
                return f"{left} overlaps {right} by {shared / smaller:.1%} of the smaller"
    return None


def validate(doc, region):
    schema = json.loads((ROOT / "contracts" / "fields.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(doc))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR fields.geojson @ {loc}: {err.message}")
    zones = json.loads((OUT / "zones.geojson").read_text())
    known = {feature["properties"]["zone_id"] for feature in zones["features"]}
    ids = []
    for feature in doc["features"]:
        props = feature["properties"]
        ids.append(props["field_id"])
        if list(props) != ["field_id", "zone_id", "area_ha", "crop", "source"]:
            errors.append("keys")
            log(STEP, f"ERROR {props.get('field_id')} property order {list(props)}")
        if props["zone_id"] not in known:
            errors.append("zone")
            log(STEP, f"ERROR {props['field_id']} zone {props['zone_id']} is not in zones.geojson")
    if ids != [f"F{i:04d}" for i in range(1, len(ids) + 1)]:
        errors.append("ids")
        log(STEP, "ERROR field ids are not F0001..Fnnnn without gaps")
    if len(ids) != len(set(ids)):
        errors.append("unique")
        log(STEP, "ERROR field ids are not unique")
    clash = overlaps(doc["features"])
    if clash:
        errors.append("overlap")
        log(STEP, f"ERROR {clash}")
    if errors:
        fail("validation failed; data/out/fields.geojson left untouched")


def publish(doc):
    staging = CACHE / "fields_staging.geojson"
    write_json(staging, doc)
    os.replace(staging, OUT / "fields.geojson")
    log(STEP, f"wrote {OUT / 'fields.geojson'}")


def draw_overlay(features, region):
    frames = json.loads((OUT / "frames" / "frames.json").read_text())
    latest = max(frames["frames"], key=lambda frame: frame["date"])
    image = Image.open(OUT / latest["rgb"]).convert("RGB")
    if image.size != (1000, 900):
        fail(f"latest frame is {image.size}, expected 1000x900")
    west, south, east, north = region["bbox"]
    width, height = image.size

    def pixel(lon, lat):
        return ((lon - west) / (east - west) * width, (north - lat) / (north - south) * height)

    draw = ImageDraw.Draw(image)
    for feature in features:
        color = COLORS[feature["properties"]["crop"]]
        rings = feature["geometry"]["coordinates"]
        for ring in rings:
            draw.line([pixel(lon, lat) for lon, lat in ring], fill=color, width=2)
    path = CACHE / "quicklook" / "fields_overlay.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    log(STEP, f"overlay {path}")


def box_area_ha(region):
    west, south, east, north = region["bbox"]
    box = Polygon([(west, south), (east, south), (east, north), (west, north)])
    area_m2, _perim = GEOD.geometry_area_perimeter(box)
    return abs(area_m2) / 10000.0


def summarize(fields, features, params, region):
    areas = [field["area_ha"] for field in fields]
    crops = {}
    zones = {}
    for feature in features:
        props = feature["properties"]
        crops[props["crop"]] = crops.get(props["crop"], 0) + 1
        zones[props["zone_id"]] = zones.get(props["zone_id"], 0) + 1
    total = sum(areas)
    share = total / box_area_ha(region)
    log(STEP, f"{len(fields)} fields | area min {min(areas):.1f} median {float(np.median(areas)):.1f} max {max(areas):.1f} ha")
    log(STEP, f"total {total:.0f} ha, {share:.0%} of the box")
    log(STEP, f"crops {crops}")
    log(STEP, f"zones {dict(sorted(zones.items()))}")
    log(STEP, f"params scale={params['scale']} min_size={params['min_size']}")
    labelled = crops.get("corn", 0) + crops.get("soybean", 0)
    if not (100 <= len(fields) <= 350):
        log(STEP, f"note: field count {len(fields)} is outside the 100-350 expectation")
    median = float(np.median(areas))
    if not (20 <= median <= 60):
        log(STEP, f"note: median area {median:.1f} ha is outside the 20-60 expectation")
    if not (0.5 <= share <= 0.8):
        log(STEP, f"note: field cover {share:.0%} is outside the 50-80% expectation")
    if labelled:
        for crop in ("corn", "soybean"):
            portion = crops.get(crop, 0) / labelled
            if portion < 0.25:
                log(STEP, f"note: {crop} is {portion:.0%} of labelled fields, under a quarter")


def search(stack, planes, masks, names, affine):
    rows = []
    best = None
    for scale in SCALES:
        for min_size in MIN_SIZES:
            labels = felzenszwalb(stack, scale=scale, sigma=0.5, min_size=min_size, channel_axis=-1)
            medians = segment_medians(labels, planes, masks)
            kept = [label for label, values in medians.items() if keep_label(values, names)]
            fields = vectorize(labels, kept, medians, names, affine) if kept else []
            areas = [field["area_ha"] for field in fields]
            median = float(np.median(areas)) if areas else None
            over = sum(area > 150 for area in areas)
            row = {
                "scale": scale,
                "min_size": min_size,
                "n_fields": len(fields),
                "median_ha": None if median is None else round(median, 2),
                "n_over_150": over,
            }
            rows.append(row)
            shown = "none" if median is None else f"{median:.1f}"
            log(STEP, f"scale={scale:<4} min_size={min_size:<4} fields={len(fields):<5} median_ha={shown:<6} over_150={over}")
    qualified = [row for row in rows if row["median_ha"] is not None and 15 <= row["median_ha"] <= 65]
    fallback = False
    if qualified:
        chosen = sorted(qualified, key=lambda row: (row["n_over_150"], -row["n_fields"], row["scale"], row["min_size"]))[0]
        log(STEP, f"chosen scale={chosen['scale']} min_size={chosen['min_size']}")
    else:
        fallback = True
        usable = [row for row in rows if row["median_ha"] is not None]
        if not usable:
            fail("no grid-search run kept a field")
        chosen = min(usable, key=lambda row: (abs(row["median_ha"] - 40), row["scale"], row["min_size"]))
        log(STEP, f"no run had median area 15-65 ha; using median closest to 40 ha (scale={chosen['scale']} min_size={chosen['min_size']})")
    if max(row["n_fields"] for row in rows) < 50:
        fail("no grid-search run keeps at least 50 fields")
    # Recompute the chosen run so we don't keep every run's polygons.
    labels = felzenszwalb(stack, scale=chosen["scale"], sigma=0.5, min_size=chosen["min_size"], channel_axis=-1)
    medians = segment_medians(labels, planes, masks)
    kept = [label for label, values in medians.items() if keep_label(values, names)]
    fields = vectorize(labels, kept, medians, names, affine)
    chosen = dict(chosen)
    chosen["fallback"] = fallback
    return rows, chosen, fields


def main():
    parser = argparse.ArgumentParser(description="Segment fields from the NDVI cube")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dest = OUT / "fields.geojson"
    if dest.exists() and not args.force:
        print("fields frozen: data/out/fields.geojson exists, use --force to rebuild", flush=True)
        return

    region = load_region()
    frame_box = region_geobox(region)
    dates, ndvi, clear = load_cube(region, frame_box)
    chosen = choose_windows(dates, clear, parse_day(region["as_of"]))
    stack, names = feature_stack(dates, ndvi, chosen)
    date_index = {day: index for index, day in enumerate(dates)}
    planes = [np.asarray(ndvi[date_index[chosen[name]["date"]]], dtype=np.float32) for name in names]
    masks = [np.asarray(clear[date_index[chosen[name]["date"]]]) for name in names]
    rows, params, fields = search(stack, planes, masks, names, frame_box.affine)
    if len(fields) < 1:
        fail("chosen run produced no fields")
    centres, threshold = label_crops(fields, names)
    features = assign_ids(fields, region)
    doc = {"type": "FeatureCollection", "features": features}
    validate(doc, region)
    debug = {
        "windows": chosen,
        "runs": rows,
        "chosen": params,
        "crop": {"centres": centres, "threshold": threshold},
        "fields": {
            field["field_id"]: {name: field["series"].get(name) for name in ("W1", "W2", "W3", "W4", "W5", "W6")}
            for field in fields
        },
    }
    write_json(CACHE / "s03_fields_debug.json", debug, pretty=True)
    publish(doc)
    draw_overlay(features, region)
    summarize(fields, features, params, region)


if __name__ == "__main__":
    main()
