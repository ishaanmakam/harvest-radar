"""Segment crop fields from the NDVI cube and write frozen field outlines."""
import argparse
import json
import math
import os
import sys
import warnings
from datetime import date

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw
from pyproj import Geod, Transformer
from rasterio.features import rasterize, shapes
from shapely import transform
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from skimage.graph import RAG, cut_threshold
from skimage.segmentation import felzenszwalb

from common import CACHE, OUT, ROOT, load_region, log, write_json
from geo import region_geobox

STEP = "s03"
SCALE = 400
MIN_SIZE = 400
THRESHOLDS = (0.06, 0.09, 0.12)
STACK_WINDOWS = ("W1", "W2", "W3", "W4")
GEOD = Geod(ellps="WGS84")
TO_METRES = Transformer.from_crs("EPSG:4326", "EPSG:32615", always_xy=True)
TO_DEGREES = Transformer.from_crs("EPSG:32615", "EPSG:4326", always_xy=True)
RECTANGULARITY_MIN = 0.75
CONVEXITY_MIN = 0.85


def fail(msg):
    log(STEP, f"ERROR {msg}")
    sys.exit(1)


def note(counts, key):
    if counts is not None:
        counts[key] = counts.get(key, 0) + 1


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
    # Pre-harvest only. W5 and W6 split fields along the harvest line.
    order = [name for name in STACK_WINDOWS if name in chosen]
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


def vectorize(labels, kept, medians, names, affine, counts=None):
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
        parts = polygons_of(geom)
        if not parts:
            continue
        if len(parts) > 1:
            largest = max(parts, key=lambda part: part.area)
            total = sum(part.area for part in parts)
            if total <= 0 or largest.area < 0.8 * total:
                note(counts, "multipolygon")
                continue
            geom = largest
        else:
            geom = parts[0]
        if geom.is_empty:
            continue
        old = kept[label - 1]
        series = {name: medians[old][index] for index, name in enumerate(names)}
        fields.append({"geom": geom, "series": series})
    return fields


def project(geom, transformer):
    def apply(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])
    return transform(geom, apply)


def rag_distance(image, labels):
    """Mean-color distance graph. rag_mean_color hardcodes 3 channels; the stack has 4."""
    graph = RAG(labels, connectivity=2)
    channels = image.shape[-1]
    flat = labels.ravel()
    pixels = np.asarray(image, dtype=np.float64).reshape(-1, channels)
    width = int(labels.max()) + 1
    counts = np.bincount(flat, minlength=width)
    sums = np.column_stack([
        np.bincount(flat, weights=pixels[:, channel], minlength=width) for channel in range(channels)
    ])
    for node in graph.nodes:
        count = int(counts[node])
        mean = sums[node] / count if count else np.zeros(channels)
        graph.nodes[node]["labels"] = [int(node)]
        graph.nodes[node]["pixel count"] = count
        graph.nodes[node]["mean color"] = mean
    for left, right, data in graph.edges(data=True):
        diff = graph.nodes[left]["mean color"] - graph.nodes[right]["mean color"]
        data["weight"] = float(np.linalg.norm(diff))
    return graph


def drop_small_holes(poly):
    kept = []
    removed = 0
    for hole in poly.interiors:
        ring = Polygon(hole)
        if ring.area >= 10000:
            kept.append(hole)
        else:
            removed += 1
    if removed == 0:
        return poly, 0
    cleaned = Polygon(poly.exterior, kept)
    if not cleaned.is_valid:
        cleaned = cleaned.buffer(0)
    return cleaned, removed


def short_side(rect):
    coords = list(rect.exterior.coords)
    if len(coords) < 2:
        return 0.0
    lengths = []
    for start, end in zip(coords, coords[1:]):
        lengths.append(math.hypot(end[0] - start[0], end[1] - start[1]))
    return min(lengths) if lengths else 0.0


def polsby_popper(geom):
    perim = geom.length
    if perim <= 0 or geom.area <= 0:
        return 0.0
    return 4 * math.pi * geom.area / (perim * perim)


def largest_piece(geom):
    parts = polygons_of(geom)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    largest = max(parts, key=lambda part: part.area)
    total = sum(part.area for part in parts)
    if total <= 0 or largest.area < 0.8 * total:
        return None
    return largest


def clean_metres(fields, counts=None):
    """Square up outlines in UTM metres, drop slivers, then give overlaps to the larger field."""
    prepared = []
    holes_filled = 0
    slivers = 0
    rectangular = 0
    for field in fields:
        metric = largest_piece(project(field["geom"], TO_METRES))
        if metric is None or metric.area <= 0:
            note(counts, "multipolygon")
            continue
        metric, removed = drop_small_holes(metric)
        holes_filled += removed
        metric = metric.simplify(10, preserve_topology=True)
        if not metric.is_valid:
            metric = metric.buffer(0)
        metric = largest_piece(metric)
        if metric is None or metric.area <= 0:
            note(counts, "multipolygon")
            continue
        try:
            rectangle = metric.minimum_rotated_rectangle
        except Exception:
            slivers += 1
            note(counts, "sliver")
            continue
        if rectangle.is_empty or rectangle.area <= 0:
            slivers += 1
            note(counts, "sliver")
            continue
        is_rect = metric.area / rectangle.area >= 0.85
        if is_rect:
            metric = rectangle
        if polsby_popper(metric) < 0.2 or short_side(metric.minimum_rotated_rectangle) < 80:
            slivers += 1
            note(counts, "sliver")
            continue
        prepared.append({**field, "geom": metric, "rectangular": is_rect})
        if is_rect:
            rectangular += 1
    prepared.sort(key=lambda field: field["geom"].area, reverse=True)
    accepted = []
    taken = None
    for field in prepared:
        geom = field["geom"]
        subtracted = taken is not None and not taken.is_empty and geom.intersects(taken)
        if taken is not None and not taken.is_empty:
            geom = geom.difference(taken).buffer(0)
        geom = largest_piece(geom)
        if geom is None or geom.area < 20000:
            note(counts, "overlap" if subtracted else "area")
            continue
        lonlat = project(geom, TO_DEGREES)
        if not lonlat.is_valid:
            lonlat = lonlat.buffer(0)
        lonlat = largest_piece(lonlat)
        if lonlat is None:
            note(counts, "overlap" if subtracted else "multipolygon")
            continue
        area_m2, _perim = GEOD.geometry_area_perimeter(lonlat)
        area_ha = abs(area_m2) / 10000.0
        if area_ha < 2 or area_ha > 300:
            note(counts, "overlap" if subtracted else "area")
            continue
        accepted.append({**field, "geom": lonlat, "area_ha": area_ha})
        taken = geom if taken is None else unary_union([taken, geom])
    share = (rectangular / len(prepared)) if prepared else 0.0
    final_share = (sum(field["rectangular"] for field in accepted) / len(accepted)) if accepted else 0.0
    areas = [field["area_ha"] for field in accepted]
    return accepted, {
        "n_fields": len(accepted),
        "median_ha": None if not areas else float(np.median(areas)),
        "share_rectangular": final_share,
        "share_rectangular_before_overlap": share,
        "slivers_dropped": slivers,
        "holes_filled": holes_filled,
    }


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


def class_means(fields):
    means = {}
    for window in ("W2", "W4", "W5"):
        means[window] = {}
        for crop in ("corn", "soybean"):
            vals = [
                field["series"].get(window)
                for field in fields
                if field["crop"] == crop and field["series"].get(window) is not None
            ]
            means[window][crop] = None if not vals else float(np.mean(vals))
        corn = means[window]["corn"]
        soy = means[window]["soybean"]
        corn_txt = "none" if corn is None else f"{corn:.3f}"
        soy_txt = "none" if soy is None else f"{soy:.3f}"
        log(STEP, f"mean {window} corn {corn_txt} soybean {soy_txt}")
    return means


def expectations_hold(means):
    corn_w2 = means["W2"]["corn"]
    soy_w2 = means["W2"]["soybean"]
    corn_w4 = means["W4"]["corn"]
    soy_w4 = means["W4"]["soybean"]
    if None in (corn_w2, soy_w2, corn_w4, soy_w4):
        return False
    return corn_w2 > soy_w2 and corn_w4 < soy_w4


def label_crops(fields):
    samples = []
    for field in fields:
        w2 = field["series"].get("W2")
        w4 = field["series"].get("W4")
        if w2 is None or w4 is None:
            field["d"] = None
        else:
            field["d"] = w2 - w4
            samples.append(field["d"])
    if len(samples) < 2:
        for field in fields:
            field["crop"] = "unknown"
        log(STEP, "fewer than 2 fields with both W2 and W4; every field labelled unknown")
        return None, None, class_means(fields)
    centres = kmeans_1d(samples)
    if centres is None or abs(centres[1] - centres[0]) < 0.15:
        for field in fields:
            field["crop"] = "unknown"
        gap = None if centres is None else centres[1] - centres[0]
        log(STEP, f"crop centres {centres} gap {gap}; every field labelled unknown")
        return centres, None, class_means(fields)
    threshold = (centres[0] + centres[1]) / 2
    counts = {"corn": 0, "soybean": 0, "unknown": 0}
    for field in fields:
        value = field["d"]
        if value is None or abs(value - threshold) <= 0.05:
            field["crop"] = "unknown"
        elif value > threshold:
            field["crop"] = "corn"
        else:
            field["crop"] = "soybean"
        counts[field["crop"]] += 1
    log(STEP, f"crop centres {[round(c, 3) for c in centres]} threshold {threshold:.3f} counts {counts}")
    return centres, threshold, class_means(fields)


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


def outline_rings(geometry):
    coords = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        return coords
    if geometry["type"] == "MultiPolygon":
        rings = []
        for polygon in coords:
            rings.extend(polygon)
        return rings
    return []


def draw_overlay(features, region, new_from=None):
    del region
    frames = json.loads((OUT / "frames" / "frames.json").read_text())
    latest = max(frames["frames"], key=lambda frame: frame["date"])
    image = Image.open(OUT / latest["rgb"]).convert("RGB")
    west, south, east, north = frames["bounds"]
    pixel_width = frames["width"]
    pixel_height = frames["height"]
    if image.size != (pixel_width, pixel_height):
        fail(f"latest frame is {image.size}, expected {pixel_width}x{pixel_height}")

    def pixel(lon, lat):
        x = (lon - west) / (east - west) * pixel_width
        y = (north - lat) / (north - south) * pixel_height
        return (x, y)

    draw = ImageDraw.Draw(image)
    for index, feature in enumerate(features):
        appended = new_from is not None and index >= new_from
        color = (255, 255, 255) if appended else (255, 255, 0)
        stroke = 3 if appended else 2
        for ring in outline_rings(feature["geometry"]):
            if len(ring) < 2:
                continue
            draw.line([pixel(lon, lat) for lon, lat in ring], fill=color, width=stroke)
    path = CACHE / "quicklook" / "fields_overlay.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    desktop = os.path.expanduser("~/Desktop/fields_overlay.png")
    image.save(desktop)
    log(STEP, f"overlay {path}")
    log(STEP, f"overlay {desktop}")


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
    log(STEP, f"thresh={params['thresh']} scale={params['scale']} min_size={params['min_size']}")
    log(STEP, f"rectangular {params['share_rectangular']:.0%} | slivers dropped {params['slivers_dropped']} | holes filled {params['holes_filled']}")
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


def run_thresh(stack, planes, masks, names, affine, thresh):
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Got image with third dimension")
        labels = felzenszwalb(stack, scale=SCALE, sigma=0.5, min_size=MIN_SIZE, channel_axis=-1)
    labels = np.asarray(labels, dtype=np.int32)
    merged = cut_threshold(labels, rag_distance(stack, labels), thresh, in_place=False)
    medians = segment_medians(merged, planes, masks)
    kept = [label for label, values in medians.items() if keep_label(values, names)]
    raw = vectorize(merged, kept, medians, names, affine) if kept else []
    fields, stats = clean_metres(raw)
    centres, threshold, means = label_crops(fields)
    return fields, stats, centres, threshold, means


def search(stack, planes, masks, names, affine):
    rows = []
    for thresh in THRESHOLDS:
        fields, stats, centres, threshold, means = run_thresh(stack, planes, masks, names, affine, thresh)
        median = stats["median_ha"]
        ok = expectations_hold(means) and median is not None and 15 <= median <= 65
        shown = "none" if median is None else f"{median:.1f}"
        log(
            STEP,
            f"thresh={thresh:.2f} fields={stats['n_fields']:<4} median_ha={shown:<6} "
            f"rect={stats['share_rectangular']:.0%} slivers={stats['slivers_dropped']} "
            f"holes={stats['holes_filled']} means={'ok' if expectations_hold(means) else 'fail'}",
        )
        rows.append({
            "thresh": thresh,
            "fields": fields,
            "stats": stats,
            "centres": centres,
            "threshold": threshold,
            "means": means,
            "ok": ok,
        })
    good = [row for row in rows if row["ok"]]
    if not good:
        fail("no thresh keeps valid corn/soybean class means and a median area of 15-65 ha; data/out left untouched")
    chosen = max(good, key=lambda row: row["thresh"])
    log(STEP, f"chosen thresh={chosen['thresh']}")
    table = [{
        "thresh": row["thresh"],
        "n_fields": row["stats"]["n_fields"],
        "median_ha": None if row["stats"]["median_ha"] is None else round(row["stats"]["median_ha"], 2),
        "share_rectangular": round(row["stats"]["share_rectangular"], 3),
        "slivers_dropped": row["stats"]["slivers_dropped"],
        "holes_filled": row["stats"]["holes_filled"],
        "means_ok": expectations_hold(row["means"]),
        "median_ok": row["stats"]["median_ha"] is not None and 15 <= row["stats"]["median_ha"] <= 65,
    } for row in rows]
    params = {
        "scale": SCALE,
        "min_size": MIN_SIZE,
        "thresh": chosen["thresh"],
        **chosen["stats"],
        "centres": chosen["centres"],
        "crop_threshold": chosen["threshold"],
        "means": chosen["means"],
    }
    return table, params, chosen["fields"]


def segment_once(stack, planes, masks, names, affine, thresh, counts=None):
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Got image with third dimension")
        labels = felzenszwalb(stack, scale=SCALE, sigma=0.5, min_size=MIN_SIZE, channel_axis=-1)
    labels = np.asarray(labels, dtype=np.int32)
    merged = cut_threshold(labels, rag_distance(stack, labels), thresh, in_place=False)
    medians = segment_medians(merged, planes, masks)
    if counts is not None:
        counts["segments"] = len(medians)
        counts["crop_filter"] = sum(1 for values in medians.values() if not keep_label(values, names))
    kept = [label for label, values in medians.items() if keep_label(values, names)]
    raw = vectorize(merged, kept, medians, names, affine, counts) if kept else []
    fields, stats = clean_metres(raw, counts)
    return fields, stats


def crop_pixels(planes, names):
    early = planes[names.index("W1")]
    peak = planes[names.index("W3")]
    return np.isfinite(early) & np.isfinite(peak) & (early <= 0.5) & (peak >= 0.65) & ((peak - early) >= 0.25)


def mask_coverage(mask, features, frame_box):
    total = int(mask.sum())
    if total == 0 or not features:
        return 0.0
    painted = rasterize(
        [(shape(feature["geometry"]), 1) for feature in features],
        out_shape=(frame_box.height, frame_box.width),
        transform=frame_box.affine,
        fill=0,
        dtype="uint8",
    )
    return float((mask & (painted > 0)).sum() / total)


def is_sliver(metric):
    try:
        rectangle = metric.minimum_rotated_rectangle
    except Exception:
        return True
    if rectangle.is_empty or rectangle.area <= 0:
        return True
    return polsby_popper(metric) < 0.2 or short_side(rectangle) < 80


def trim_against_existing(fields, barrier):
    kept = []
    for field in fields:
        metric = project(field["geom"], TO_METRES)
        before = metric.area
        if before <= 0:
            continue
        remain = metric.difference(barrier).buffer(0)
        parts = polygons_of(remain)
        if not parts:
            continue
        piece = max(parts, key=lambda part: part.area)
        if piece.area < 50000 or piece.area < 0.6 * before or is_sliver(piece):
            continue
        kept.append({**field, "geom": piece})
    return kept


def resolve_new(fields):
    ordered = sorted(fields, key=lambda field: field["geom"].area, reverse=True)
    accepted = []
    taken = None
    for field in ordered:
        geom = field["geom"]
        if taken is not None and not taken.is_empty:
            geom = geom.difference(taken).buffer(0)
        geom = largest_piece(geom)
        if geom is None or geom.area < 20000:
            continue
        lonlat = project(geom, TO_DEGREES)
        if not lonlat.is_valid:
            lonlat = lonlat.buffer(0)
        lonlat = largest_piece(lonlat)
        if lonlat is None:
            continue
        area_m2, _perim = GEOD.geometry_area_perimeter(lonlat)
        area_ha = abs(area_m2) / 10000.0
        if area_ha < 2 or area_ha > 300:
            continue
        accepted.append({**field, "geom": lonlat, "area_ha": area_ha})
        taken = geom if taken is None else unary_union([taken, geom])
    return accepted


def shape_scores(lonlat):
    """Rectangularity and convexity in EPSG:32615."""
    metric = project(lonlat, TO_METRES)
    if metric.is_empty:
        return 0.0, 0.0
    if not metric.is_valid:
        metric = metric.buffer(0)
    if metric.is_empty or metric.area <= 0:
        return 0.0, 0.0
    try:
        rectangle = metric.minimum_rotated_rectangle
    except Exception:
        return 0.0, 0.0
    hull = metric.convex_hull
    if rectangle.is_empty or rectangle.area <= 0 or hull.is_empty or hull.area <= 0:
        return 0.0, 0.0
    return metric.area / rectangle.area, metric.area / hull.area


def quality_gate(fields):
    """Append-only. Keep fields that are close to a rectangle and to their convex hull."""
    kept = []
    for index, field in enumerate(fields, start=1):
        rectangularity, convexity = shape_scores(field["geom"])
        accept = rectangularity >= RECTANGULARITY_MIN and convexity >= CONVEXITY_MIN
        log(
            STEP,
            f"candidate {index} rect={rectangularity:.3f} convex={convexity:.3f} "
            f"{'kept' if accept else 'dropped'}",
        )
        if accept:
            kept.append(field)
    log(STEP, f"quality gate kept {len(kept)} dropped {len(fields) - len(kept)}")
    return kept


def crop_from_threshold(field, threshold):
    early = field["series"].get("W2")
    late = field["series"].get("W4")
    if threshold is None or early is None or late is None:
        return "unknown"
    value = early - late
    if abs(value - threshold) <= 0.05:
        return "unknown"
    return "corn" if value > threshold else "soybean"


def stored_threshold(debug):
    stored = (debug.get("crop") or {}).get("threshold")
    if stored is not None:
        return float(stored), False
    samples = []
    for series in (debug.get("fields") or {}).values():
        if series.get("W2") is not None and series.get("W4") is not None:
            samples.append(series["W2"] - series["W4"])
    centres = kmeans_1d(samples) if len(samples) >= 2 else None
    if centres is None or abs(centres[1] - centres[0]) < 0.15:
        fail("crop threshold is missing from the debug JSON and could not be recomputed")
    return (centres[0] + centres[1]) / 2, True


def new_features(fields, region, first_number):
    def sort_key(field):
        centre = field["geom"].centroid
        return (-round(centre.y, 3), centre.x)

    features = []
    for offset, field in enumerate(sorted(fields, key=sort_key)):
        centre = field["geom"].centroid
        features.append({
            "type": "Feature",
            "geometry": round_polygon(field["geom"]),
            "properties": {
                "field_id": f"F{first_number + offset:04d}",
                "zone_id": zone_of(centre.x, centre.y, region),
                "area_ha": round(field["area_ha"], 2),
                "crop": field["crop"],
                "source": "segmentation",
            },
        })
    return features


def append_fields():
    dest = OUT / "fields.geojson"
    if not dest.exists():
        fail("data/out/fields.geojson is missing; nothing to append to")
    old = json.loads(dest.read_text())
    existing = old["features"]
    region = load_region()
    frame_box = region_geobox(region)
    dates, ndvi, clear = load_cube(region, frame_box)
    chosen = choose_windows(dates, clear, parse_day(region["as_of"]))
    if "W4" not in chosen:
        fail("W4 has no date")
    stack, _stack_names = feature_stack(dates, ndvi, chosen)
    names = [name for name in ("W1", "W2", "W3", "W4", "W5", "W6") if name in chosen]
    date_index = {day: index for index, day in enumerate(dates)}
    planes = [np.asarray(ndvi[date_index[chosen[name]["date"]]], dtype=np.float32) for name in names]
    masks = [np.asarray(clear[date_index[chosen[name]["date"]]]) for name in names]
    debug_path = CACHE / "s03_fields_debug.json"
    debug = json.loads(debug_path.read_text()) if debug_path.exists() else {}
    current = float((debug.get("chosen") or {}).get("thresh") or 0.12)
    counts = {"crop_filter": 0, "area": 0, "multipolygon": 0, "sliver": 0, "overlap": 0}
    segment_once(stack, planes, masks, names, frame_box.affine, current, counts)
    log(STEP, f"drops at thresh={current:g}: segments {counts.get('segments', 0)}, "
        f"crop filter {counts['crop_filter']}, area {counts['area']}, "
        f"multipolygon {counts['multipolygon']}, sliver {counts['sliver']}, overlap {counts['overlap']}")
    debug["drops"] = counts
    mask = crop_pixels(planes, names)
    before = mask_coverage(mask, existing, frame_box)
    log(STEP, f"coverage of existing fields {before:.1%}")
    candidates, _stats = segment_once(stack, planes, masks, names, frame_box.affine, 0.06)
    barrier = unary_union([project(shape(feature["geometry"]), TO_METRES) for feature in existing]).buffer(5)
    fresh = quality_gate(resolve_new(trim_against_existing(candidates, barrier)))
    threshold, recomputed = stored_threshold(debug)
    if recomputed:
        debug.setdefault("crop", {})
        debug["crop"]["signal"] = "W2-W4"
        debug["crop"]["threshold"] = threshold
        log(STEP, f"recomputed crop threshold {threshold:.3f}")
    for field in fresh:
        field["crop"] = crop_from_threshold(field, threshold)
    added = new_features(fresh, region, len(existing) + 1)
    combined = existing + added
    after = mask_coverage(mask, combined, frame_box)
    areas = [feature["properties"]["area_ha"] for feature in added]
    crops = {}
    for feature in added:
        crop = feature["properties"]["crop"]
        crops[crop] = crops.get(crop, 0) + 1
    median = float(np.median(areas)) if areas else None
    shown = "none" if median is None else f"{median:.1f}"
    log(STEP, f"new fields {len(added)} | coverage {before:.1%} -> {after:.1%} | median ha {shown} | crops {crops}")
    if after <= before:
        write_json(debug_path, debug, pretty=True)
        fail(f"coverage did not improve ({before:.1%} -> {after:.1%}); data/out left untouched")
    doc = {"type": "FeatureCollection", "features": combined}
    validate(doc, region)
    staging = CACHE / "fields_staging.geojson"
    write_json(staging, doc)
    checked = json.loads(staging.read_text())
    if checked["features"][:len(existing)] != existing:
        staging.unlink(missing_ok=True)
        fail("existing features would change; data/out left untouched")
    os.replace(staging, dest)
    debug["append"] = {
        "thresh": 0.06,
        "new_fields": len(added),
        "coverage_before": round(before, 4),
        "coverage_after": round(after, 4),
        "median_ha": None if median is None else round(median, 2),
        "crops": crops,
        "threshold": threshold,
        "rectangularity_min": RECTANGULARITY_MIN,
        "convexity_min": CONVEXITY_MIN,
    }
    write_json(debug_path, debug, pretty=True)
    draw_overlay(checked["features"], region, new_from=len(existing))
    log(STEP, f"wrote {dest}")


def main():
    parser = argparse.ArgumentParser(description="Segment fields from the NDVI cube")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()
    dest = OUT / "fields.geojson"
    if args.append:
        if args.force:
            fail("--append keeps the existing fields; do not pass --force")
        append_fields()
        return
    if dest.exists() and not args.force:
        print("fields frozen: data/out/fields.geojson exists, use --force to rebuild", flush=True)
        return

    region = load_region()
    frame_box = region_geobox(region)
    dates, ndvi, clear = load_cube(region, frame_box)
    chosen = choose_windows(dates, clear, parse_day(region["as_of"]))
    if "W4" not in chosen:
        fail("W4 has no date")
    stack, _stack_names = feature_stack(dates, ndvi, chosen)
    names = [name for name in ("W1", "W2", "W3", "W4", "W5", "W6") if name in chosen]
    date_index = {day: index for index, day in enumerate(dates)}
    planes = [np.asarray(ndvi[date_index[chosen[name]["date"]]], dtype=np.float32) for name in names]
    masks = [np.asarray(clear[date_index[chosen[name]["date"]]]) for name in names]
    rows, params, fields = search(stack, planes, masks, names, frame_box.affine)
    if len(fields) < 1:
        fail("chosen run produced no fields")
    features = assign_ids(fields, region)
    doc = {"type": "FeatureCollection", "features": features}
    validate(doc, region)
    debug = {
        "windows": chosen,
        "runs": rows,
        "chosen": params,
        "stack_windows": list(STACK_WINDOWS),
        "crop": {
            "signal": "W2-W4",
            "centres": params["centres"],
            "threshold": params["crop_threshold"],
            "means": params["means"],
        },
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
