"""Per-field NDVI and NBR on every cube date."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from rasterio.features import rasterize
from shapely.geometry import shape

from common import CACHE, OUT, ROOT, load_region, log, write_json
from geo import region_geobox

STEP = "s04"
SOURCE = "sentinel-2-l2a (earth-search), pipeline/s04_timeseries.py"


def parse_day(text):
    return date.fromisoformat(str(text))


def fail(msg):
    log(STEP, f"ERROR {msg}")
    sys.exit(1)


def load_inputs(region, frame_box, fields_path):
    cube_path = CACHE / "cube.npz"
    if not cube_path.exists():
        fail(f"missing {cube_path}. Run pipeline/s02_cube.py first.")
    if not fields_path.exists():
        fail(f"missing {fields_path}")
    cube = np.load(cube_path)
    for key in ("dates", "ndvi", "nbr", "clear", "bbox"):
        if key not in cube.files:
            fail(f"cube.npz has no {key} array")
    dates = [str(day) for day in cube["dates"]]
    ndvi = np.asarray(cube["ndvi"])
    nbr = np.asarray(cube["nbr"])
    clear = np.asarray(cube["clear"])
    bbox = np.asarray(cube["bbox"], dtype=np.float64)
    if ndvi.shape != nbr.shape or ndvi.shape != clear.shape or ndvi.ndim != 3:
        fail("cube ndvi, nbr and clear do not line up")
    if len(dates) != ndvi.shape[0]:
        fail("cube dates do not match the stack")
    if dates != sorted(dates):
        fail("cube dates are not ascending")
    if not np.allclose(bbox, region["bbox"]) or ndvi.shape[1:] != (frame_box.height, frame_box.width):
        fail(f"cube bbox {list(bbox)} shape {ndvi.shape[1:]} does not match the region grid")
    fields = json.loads(fields_path.read_text())
    if fields.get("type") != "FeatureCollection":
        fail(f"{fields_path} is not a FeatureCollection")
    return dates, ndvi, nbr, clear, fields


def raster_labels(features, frame_box):
    shapes = [(shape(feature["geometry"]), index) for index, feature in enumerate(features, start=1)]
    labels = rasterize(
        shapes,
        out_shape=(frame_box.height, frame_box.width),
        transform=frame_box.affine,
        fill=0,
        dtype="int32",
    )
    counts = np.bincount(labels.ravel(), minlength=len(features) + 1)
    empty = [features[index - 1]["properties"]["field_id"] for index in range(1, len(features) + 1) if counts[index] == 0]
    if empty:
        fail(f"{len(empty)} fields rasterized to zero pixels: {', '.join(empty)}")
    return labels, counts


def sample_date(labels, ndvi, nbr, clear, counts, minimum):
    flat = labels.ravel()
    width = counts.size
    mask = np.asarray(clear).ravel().astype(bool)
    n_clear = np.bincount(flat, weights=mask.astype(np.float64), minlength=width)
    ndvi_plane = np.asarray(ndvi, dtype=np.float64).ravel()
    nbr_plane = np.asarray(nbr, dtype=np.float64).ravel()
    ndvi_ok = mask & np.isfinite(ndvi_plane)
    nbr_ok = mask & np.isfinite(nbr_plane)
    sum_ndvi = np.bincount(flat, weights=np.where(ndvi_ok, ndvi_plane, 0.0), minlength=width)
    sum_nbr = np.bincount(flat, weights=np.where(nbr_ok, nbr_plane, 0.0), minlength=width)
    n_ndvi = np.bincount(flat, weights=ndvi_ok.astype(np.float64), minlength=width)
    n_nbr = np.bincount(flat, weights=nbr_ok.astype(np.float64), minlength=width)
    ndvi_out = []
    nbr_out = []
    clear_out = []
    for index in range(1, width):
        frac = float(n_clear[index] / counts[index])
        clear_out.append(round(min(1.0, max(0.0, frac)), 3))
        if frac >= minimum and n_ndvi[index] > 0 and n_nbr[index] > 0:
            ndvi_out.append(round(float(sum_ndvi[index] / n_ndvi[index]), 3))
            nbr_out.append(round(float(sum_nbr[index] / n_nbr[index]), 3))
        else:
            ndvi_out.append(None)
            nbr_out.append(None)
    return ndvi_out, nbr_out, clear_out


def nearest(dates, month, day):
    year = date.fromisoformat(dates[0]).year
    target = date(year, month, day)
    return min(dates, key=lambda text: abs((date.fromisoformat(text) - target).days))


def validate(doc, features, dates):
    schema = json.loads((ROOT / "contracts" / "timeseries.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(doc))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR timeseries.json @ {loc}: {err.message}")
    wanted = [feature["properties"]["field_id"] for feature in features]
    if list(doc["fields"]) != sorted(wanted):
        errors.append("order")
        log(STEP, "ERROR timeseries fields are not the fields file in field_id order")
    n = len(dates)
    if doc["dates"] != dates:
        errors.append("dates")
        log(STEP, "ERROR timeseries dates are not the cube dates")
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
    if errors:
        fail("validation failed; data/out/timeseries.json left untouched")


def report(doc, features):
    crops = {feature["properties"]["field_id"]: feature["properties"]["crop"] for feature in features}
    counts = [sum(value is not None for value in series["ndvi"]) for series in doc["fields"].values()]
    thin = [field_id for field_id, series in doc["fields"].items() if sum(value is not None for value in series["ndvi"]) < 4]
    log(STEP, f"{len(doc['fields'])} fields, {len(doc['dates'])} dates")
    log(STEP, f"non-null passes min {min(counts)} median {float(np.median(counts)):.0f} max {max(counts)}")
    log(STEP, f"{len(thin)} fields with fewer than 4 non-null passes" + (f": {', '.join(thin)}" if thin else ""))
    targets = [("Jun 25", 6, 25), ("Jul 20", 7, 20), ("Aug 30", 8, 30), ("Sep 25", 9, 25), ("latest", None, None)]
    for label, month, day in targets:
        text = doc["dates"][-1] if month is None else nearest(doc["dates"], month, day)
        index = doc["dates"].index(text)
        parts = []
        for crop in ("corn", "soybean", "unknown"):
            values = [
                series["ndvi"][index]
                for field_id, series in doc["fields"].items()
                if crops.get(field_id) == crop and series["ndvi"][index] is not None
            ]
            shown = "none" if not values else f"{float(np.median(values)):.3f}"
            parts.append(f"{crop} {shown} (n={len(values)})")
        log(STEP, f"median NDVI {label} ({text}): {', '.join(parts)}")
    return thin


def main():
    parser = argparse.ArgumentParser(description="Build per-field NDVI/NBR timeseries from the cube")
    parser.add_argument("--fields", default=str(OUT / "fields.geojson"))
    args = parser.parse_args()
    region = load_region()
    frame_box = region_geobox(region)
    minimum = float(region["thresholds"]["clear_fraction_min"])
    fields_path = Path(args.fields)
    if not fields_path.is_absolute():
        fields_path = ROOT / fields_path
    dates, ndvi, nbr, clear, fields = load_inputs(region, frame_box, fields_path)
    as_of = parse_day(region["as_of"]).isoformat()
    keep = [index for index, day in enumerate(dates) if day <= as_of]
    if not keep:
        fail(f"no cube dates on or before {as_of}")
    if len(keep) != len(dates):
        log(STEP, f"dropped {len(dates) - len(keep)} cube dates after {as_of}")
        dates = [dates[index] for index in keep]
        ndvi = ndvi[keep]
        nbr = nbr[keep]
        clear = clear[keep]
    features = fields["features"]
    if not features:
        fail("fields file has no features")
    labels, counts = raster_labels(features, frame_box)
    columns = {"ndvi": [], "nbr": [], "clear_frac": []}
    for index, day in enumerate(dates):
        ndvi_col, nbr_col, clear_col = sample_date(labels, ndvi[index], nbr[index], clear[index], counts, minimum)
        columns["ndvi"].append(ndvi_col)
        columns["nbr"].append(nbr_col)
        columns["clear_frac"].append(clear_col)
        log(STEP, f"{day} done")
    by_id = {}
    for index, feature in enumerate(features):
        field_id = feature["properties"]["field_id"]
        by_id[field_id] = {
            "ndvi": [column[index] for column in columns["ndvi"]],
            "nbr": [column[index] for column in columns["nbr"]],
            "clear_frac": [column[index] for column in columns["clear_frac"]],
        }
    doc = {
        "source": SOURCE,
        "dates": dates,
        "fields": {field_id: by_id[field_id] for field_id in sorted(by_id)},
    }
    validate(doc, features, dates)
    staging = CACHE / "timeseries_staging.json"
    write_json(staging, doc)
    staging.replace(OUT / "timeseries.json")
    log(STEP, f"wrote {OUT / 'timeseries.json'}")
    report(doc, features)


if __name__ == "__main__":
    main()
