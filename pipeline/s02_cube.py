"""NDVI, NBR and a clear mask for every pass, on the same grid as the frames."""
import json
import os
import sys
import time
from datetime import date, datetime

import numpy as np
import odc.geo.xr  # registers Dataset.odc
from PIL import Image

from common import CACHE, load_region, log
from geo import (
    VALID_SCL,
    clear_fraction,
    dates_of,
    item_date,
    load_bands,
    load_items,
    region_geobox,
    resample_plane,
    scale_offset,
    to_reflectance,
)

STEP = "s02"
BANDS = ("red", "nir", "swir22")
CUBE = CACHE / "cube.npz"
TMP = CACHE / "cube_tmp.npz"
CLEAR_EPS = 0.002


def assert_native(ds):
    box = ds.odc.geobox
    rx = abs(float(box.resolution.x))
    ry = abs(float(box.resolution.y))
    if abs(rx - 10) > 0.01 or abs(ry - 10) > 0.01:
        log(STEP, f"ERROR native resolution is {rx:.3f} x {ry:.3f} m, expected 10 m")
        sys.exit(1)
    yname = "y" if "y" in ds.dims else "latitude"
    y = np.asarray(ds[yname].values)
    if not (y[0] > y[-1]):
        log(STEP, f"ERROR native grid is not north-up: {yname}[0]={y[0]} {yname}[-1]={y[-1]}")
        sys.exit(1)
    log(STEP, f"native grid EPSG:{box.crs.to_epsg()} {box.width}x{box.height} at 10 m")
    return box


def summary_dates():
    path = CACHE / "s01_summary.json"
    if not path.exists():
        log(STEP, f"ERROR missing {path}. Run pipeline/s01_search.py first.")
        sys.exit(1)
    dates = json.loads(path.read_text())["dates"]
    if list(dates) != sorted(dates):
        log(STEP, "ERROR s01 dates are not ascending")
        sys.exit(1)
    return dates


def index_plane(nir, other, valid):
    out = np.full(nir.shape, np.nan, dtype=np.float32)
    den = nir + other
    ok = valid & np.isfinite(den) & (den != 0)
    out[ok] = (nir[ok] - other[ok]) / den[ok]
    return np.clip(out, -1, 1)


def frame_planes(red, nir, swir, scl, scales, src_box, frame_box):
    red_r = to_reflectance(red, *scales["red"])
    nir_r = to_reflectance(nir, *scales["nir"])
    swir_r = to_reflectance(swir, *scales["swir22"])
    if np.nanmax(red) <= 2:
        log(STEP, "ERROR red values look pre-scaled, not digital numbers. Stopped before applying scale again.")
        sys.exit(1)
    valid = np.isin(scl, VALID_SCL) & np.isfinite(red_r) & np.isfinite(nir_r) & np.isfinite(swir_r)
    ndvi = resample_plane(index_plane(nir_r, red_r, valid), src_box, frame_box, "bilinear", np.nan)
    nbr = resample_plane(index_plane(nir_r, swir_r, valid), src_box, frame_box, "bilinear", np.nan)
    scl_frame = resample_plane(np.asarray(scl), src_box, frame_box, "nearest", 0)
    clear = np.isin(scl_frame, VALID_SCL)
    ndvi = np.where(clear, ndvi, np.nan).astype(np.float16)
    nbr = np.where(clear, nbr, np.nan).astype(np.float16)
    return ndvi, nbr, clear


def load_chunk(items, scales, frame_box):
    ds = load_bands(items, ["red", "nir", "swir22", "scl"], resampling={"scl": "nearest", "*": "bilinear"})
    src_box = assert_native(ds)
    loaded = dates_of(ds)
    planes = {}
    for index, day in enumerate(loaded):
        planes[day] = frame_planes(
            ds.red.isel(time=index).values,
            ds.nir.isel(time=index).values,
            ds.swir22.isel(time=index).values,
            ds.scl.isel(time=index).values,
            scales,
            src_box,
            frame_box,
        )
    return planes


def cache_ok(region, frame_box, wanted):
    if not CUBE.exists():
        return None
    with np.load(CUBE) as saved:
        bbox = np.asarray(saved["bbox"], dtype=np.float64)
        shape = tuple(int(v) for v in saved["shape"])
        dates = [str(day) for day in saved["dates"]]
        if not np.allclose(bbox, region["bbox"]) or shape != (frame_box.height, frame_box.width):
            log(STEP, f"cached bbox {list(bbox)} shape {shape} does not match the region grid; rebuilding")
            return None
        if saved["ndvi"].shape[1:] != (frame_box.height, frame_box.width):
            log(STEP, "cached array shape does not match the region grid; rebuilding")
            return None
        keep = [day for day in dates if day in wanted]
        dropped = len(dates) - len(keep)
        if dropped:
            log(STEP, f"dropping {dropped} cached dates that are no longer in s01")
        index = {day: i for i, day in enumerate(dates)}
        kept = {
            day: (
                np.array(saved["ndvi"][index[day]]),
                np.array(saved["nbr"][index[day]]),
                np.array(saved["clear"][index[day]]),
            )
            for day in keep
        }
    return kept


def median_clear(values, clear):
    sample = np.asarray(values, dtype=np.float32)[clear]
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return float("nan")
    return float(np.median(sample))


def check_clear_fractions(dates, fractions):
    path = CACHE / "s05_clear.json"
    if not path.exists():
        log(STEP, f"ERROR missing {path}; cannot check clear fractions against s05")
        sys.exit(1)
    reference = json.loads(path.read_text())
    worst = 0.0
    shared = 0
    for day in dates:
        if day not in reference:
            continue
        shared += 1
        diff = abs(fractions[day] - float(reference[day]))
        worst = max(worst, diff)
        if diff > CLEAR_EPS:
            log(STEP, f"ERROR clear fraction {day} cube {fractions[day]:.4f} s05 {float(reference[day]):.4f} diff {diff:.4f}")
            sys.exit(1)
    log(STEP, f"clear fractions match s05 on {shared} dates (max abs diff {worst:.4f})")


def check_scale(dates, medians, fractions):
    clear_days = [day for day in dates if fractions[day] >= 0.5 and np.isfinite(medians[day])]
    if not clear_days:
        log(STEP, "ERROR no clear dates to judge the NDVI scale")
        sys.exit(1)
    season = float(np.median([medians[day] for day in clear_days]))
    low = [day for day in clear_days if medians[day] < 0.1]
    if season >= 0.9 or season <= 0.1 or low:
        log(STEP, f"ERROR NDVI scale looks wrong: season median {season:.3f}, dates below 0.1: {low}")
        sys.exit(1)
    log(STEP, f"season median NDVI on clear dates {season:.3f}")


def quicklooks(dates, ndvi, fractions):
    year = date.fromisoformat(dates[0]).year
    targets = (date(year, 7, 20), date(year, 9, 10))
    clear_days = [day for day in dates if fractions[day] >= 0.5]
    if not clear_days:
        log(STEP, "ERROR no clear date for a quick-look")
        sys.exit(1)
    chosen = []
    for target in targets:
        chosen.append(min(clear_days, key=lambda day: abs((date.fromisoformat(day) - target).days)))
    chosen.append(dates[-1])
    out = CACHE / "quicklook"
    out.mkdir(parents=True, exist_ok=True)
    brown = np.array([139, 90, 43], dtype=np.float32)
    green = np.array([34, 139, 34], dtype=np.float32)
    for day in chosen:
        values = np.asarray(ndvi[dates.index(day)], dtype=np.float32)
        span = np.clip((values - (-0.2)) / (0.9 - (-0.2)), 0, 1)
        rgb = brown * (1 - span[..., None]) + green * span[..., None]
        rgb = np.where(np.isfinite(values)[..., None], rgb, 255).astype(np.uint8)
        image = Image.fromarray(rgb, mode="RGB")
        if image.size != (1000, 900):
            log(STEP, f"ERROR quick-look {day} is {image.size}, expected 1000x900")
            sys.exit(1)
        path = out / f"ndvi_{day}.png"
        image.save(path)
        log(STEP, f"quick-look {path.name}")


def publish(dates, ndvi, nbr, clear, bbox, shape):
    if TMP.exists():
        TMP.unlink()
    np.savez(
        TMP,
        dates=np.array(dates),
        ndvi=ndvi,
        nbr=nbr,
        clear=clear,
        bbox=np.asarray(bbox, dtype=np.float64),
        shape=np.asarray(shape, dtype=np.int32),
    )
    os.replace(TMP, CUBE)


def report(dates, ndvi, nbr, clear, fractions, medians_ndvi, loaded, reused, started):
    log(STEP, "date        clear    ndvi    nbr")
    for index, day in enumerate(dates):
        med_nbr = median_clear(nbr[index], clear[index])
        log(STEP, f"{day}  {fractions[day]:.4f}  {medians_ndvi[day]:.3f}  {med_nbr:.3f}")
    quicklooks(dates, ndvi, fractions)
    size_mb = CUBE.stat().st_size / 1_000_000
    elapsed = time.perf_counter() - started
    log(STEP, f"cube {ndvi.shape} {size_mb:.1f} MB")
    log(STEP, f"loaded {loaded} dates, reused {reused}, {elapsed:.1f}s")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Cache NDVI, NBR and the clear mask on the frame grid")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--batch", type=int, default=8)
    args = parser.parse_args()
    if args.batch < 1:
        log(STEP, "ERROR --batch must be at least 1")
        sys.exit(1)

    started = time.perf_counter()
    region = load_region()
    frame_box = region_geobox(region)
    wanted = summary_dates()
    items = load_items()
    items.sort(key=lambda item: (item_date(item), item.id))
    by_date = {}
    for item in items:
        by_date.setdefault(item_date(item), []).append(item)
    missing_items = [day for day in wanted if day not in by_date]
    if missing_items:
        log(STEP, f"ERROR s01 dates with no items: {missing_items}")
        sys.exit(1)

    kept = None if args.full else cache_ok(region, frame_box, set(wanted))
    if args.full:
        log(STEP, "full rebuild")
    if kept is None:
        kept = {}
    needed = [day for day in wanted if day not in kept]
    log(STEP, f"{len(wanted)} s01 dates, {len(needed)} to load, batch {args.batch}")

    scales = {band: scale_offset(items, band) for band in BANDS}
    for band, (scale, offset) in scales.items():
        log(STEP, f"{band} scale={scale} offset={offset}")

    loaded = {}
    for start in range(0, len(needed), args.batch):
        chunk = needed[start:start + args.batch]
        batch_items = [item for day in chunk for item in by_date[day]]
        log(STEP, f"batch {chunk[0]} .. {chunk[-1]} ({len(batch_items)} items)")
        planes = load_chunk(batch_items, scales, frame_box)
        if list(planes) != chunk:
            log(STEP, "ERROR loaded dates do not match this batch")
            log(STEP, f"loaded: {list(planes)}")
            log(STEP, f"expected: {chunk}")
            sys.exit(1)
        loaded.update(planes)

    ordered = []
    ndvi_rows, nbr_rows, clear_rows = [], [], []
    for day in wanted:
        ndvi, nbr, clear = loaded[day] if day in loaded else kept[day]
        ordered.append(day)
        ndvi_rows.append(ndvi)
        nbr_rows.append(nbr)
        clear_rows.append(clear)
    ndvi = np.stack(ndvi_rows).astype(np.float16)
    nbr = np.stack(nbr_rows).astype(np.float16)
    clear = np.stack(clear_rows).astype(bool)
    if ndvi.shape != (len(wanted), frame_box.height, frame_box.width):
        log(STEP, f"ERROR cube shape {ndvi.shape}")
        sys.exit(1)
    fractions = {day: float(clear[index].mean()) for index, day in enumerate(ordered)}
    medians = {day: median_clear(ndvi[index], clear[index]) for index, day in enumerate(ordered)}
    check_clear_fractions(ordered, fractions)
    check_scale(ordered, medians, fractions)
    publish(ordered, ndvi, nbr, clear, region["bbox"], (frame_box.height, frame_box.width))
    report(ordered, ndvi, nbr, clear, fractions, medians, len(needed), len(wanted) - len(needed), started)


if __name__ == "__main__":
    main()
