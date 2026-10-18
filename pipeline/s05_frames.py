"""True-colour JPEG for every pass that is at least 50% clear over the AOI."""
import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import odc.geo.xr  # registers Dataset.odc
from jsonschema import Draft202012Validator
from PIL import Image

from common import CACHE, OUT, ROOT, load_region, log, write_json

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

STEP = "s05"
RGB = ("red", "green", "blue")
JPEG_QUALITY = 92


def require_dates(got, expected, label):
    if got == expected:
        return
    log(STEP, f"ERROR {label} dates do not match")
    log(STEP, f"loaded: {got}")
    log(STEP, f"expected: {expected}")
    sys.exit(1)


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
    code = box.crs.to_epsg()
    log(STEP, f"native grid EPSG:{code} {box.width}x{box.height} at 10 m")
    return box


def clear_table(scl_by_date, dates):
    fractions = {}
    log(STEP, "date        clear_frac")
    for day in dates:
        frac = clear_fraction(scl_by_date[day])
        fractions[day] = frac
        log(STEP, f"{day}  {frac:.4f}")
    write_json(CACHE / "s05_clear.json", fractions)
    log(STEP, f"wrote {CACHE / 's05_clear.json'}")
    return fractions


def stretch_from_frames(rgb_frames, scl_frames):
    limits = {}
    for band_index, band in enumerate(RGB):
        pieces = []
        for rgb, scl in zip(rgb_frames, scl_frames):
            mask = np.isin(scl, VALID_SCL)[::4, ::4]
            vals = rgb[band_index][::4, ::4][mask]
            vals = vals[np.isfinite(vals)]
            if vals.size:
                pieces.append(vals)
        if not pieces:
            log(STEP, f"ERROR stretch pool is empty for {band}")
            sys.exit(1)
        pooled = np.concatenate(pieces)
        lo = float(np.percentile(pooled, 2))
        hi = float(np.percentile(pooled, 98))
        log(STEP, f"stretch {band} lo={lo:.6f} hi={hi:.6f} from {pooled.size} clear samples")
        limits[band] = (lo, hi)
    return limits


def render_uint8(refl, lo, hi):
    scaled = (refl - lo) / (hi - lo)
    scaled = np.clip(scaled, 0, 1)
    scaled = np.where(np.isfinite(refl), scaled, 0)
    return (scaled * 255).astype(np.uint8)


def prepare_staging():
    staging = CACHE / "frames_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    return staging


def validate_staging(staging, doc):
    schema = json.loads((ROOT / "contracts" / "frames.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda err: list(err.path))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR frames.json @ {loc}: {err.message}")
    missing = []
    for frame in doc["frames"]:
        path = staging / Path(frame["rgb"]).name
        if not path.exists():
            missing.append(frame["rgb"])
            log(STEP, f"ERROR missing staged image {path}")
    if errors or missing:
        log(STEP, "ERROR staging failed validation; data/out/frames left untouched")
        sys.exit(1)


def publish(staging):
    dest = OUT / "frames"
    OUT.mkdir(parents=True, exist_ok=True)
    backup = CACHE / "_frames_backup"
    if backup.exists():
        shutil.rmtree(backup)
    moved = False
    if dest.exists():
        dest.rename(backup)
        moved = True
    try:
        staging.rename(dest)
    except Exception:
        if moved and not dest.exists():
            backup.rename(dest)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    log(STEP, f"replaced {dest}")


def main():
    parser = argparse.ArgumentParser(description="Render clear true-colour frames for the demo AOI")
    parser.add_argument("--min-clear", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--new-stretch", action="store_true")
    args = parser.parse_args()

    region = load_region()
    items = load_items()
    items.sort(key=lambda item: (item_date(item), item.id))
    summary_dates = json.loads((CACHE / "s01_summary.json").read_text())["dates"]
    log(STEP, f"{len(items)} items, s01 has {len(summary_dates)} dates, min_clear={args.min_clear}")

    frame_box = region_geobox(region)
    scl_ds = load_bands(items, ["scl"])
    src_box = assert_native(scl_ds)
    loaded_dates = dates_of(scl_ds)
    require_dates(loaded_dates, summary_dates, "SCL")
    log(STEP, "nearest resample of SCL onto the frame grid")
    scl_by_date = {}
    for index, day in enumerate(loaded_dates):
        native = np.asarray(scl_ds.scl.isel(time=index).values)
        scl_by_date[day] = resample_plane(native, src_box, frame_box, "nearest", 0)
    fractions = clear_table(scl_by_date, loaded_dates)

    frame_dates = [day for day in loaded_dates if fractions[day] >= args.min_clear]
    if args.limit is not None:
        frame_dates = frame_dates[-args.limit:]
    log(STEP, f"frame dates ({len(frame_dates)}): {frame_dates}")
    if not frame_dates:
        log(STEP, "ERROR no dates meet the clear-fraction threshold")
        sys.exit(1)

    wanted = set(frame_dates)
    rgb_items = [item for item in items if item_date(item) in wanted]
    scales = {band: scale_offset(rgb_items, band) for band in RGB}
    for band, (scale, offset) in scales.items():
        log(STEP, f"{band} scale={scale} offset={offset}")

    rgb_ds = load_bands(rgb_items, list(RGB))
    assert_native(rgb_ds)
    require_dates(dates_of(rgb_ds), frame_dates, "RGB")
    sample = np.asarray(rgb_ds.red.isel(time=0).values)
    log(STEP, f"red DN dtype={sample.dtype} min={np.nanmin(sample)} max={np.nanmax(sample)}")
    if np.nanmax(sample) <= 2:
        log(STEP, "ERROR red values look pre-scaled, not digital numbers. Stopped before applying scale again.")
        sys.exit(1)

    log(STEP, "cubic resample onto the frame grid")
    scl_frames = [scl_by_date[day] for day in frame_dates]
    rgb_frames = []
    for index, _day in enumerate(frame_dates):
        planes = []
        for band in RGB:
            refl = to_reflectance(rgb_ds[band].isel(time=index).values, *scales[band])
            planes.append(resample_plane(refl, src_box, frame_box, "cubic", np.nan))
        rgb_frames.append(np.stack(planes))

    stretch_path = CACHE / "s05_stretch.json"
    full_run = args.limit is None
    limits = None
    if full_run and stretch_path.exists() and not args.new_stretch:
        saved = json.loads(stretch_path.read_text())
        if saved.get("dates") == frame_dates and all(band in saved for band in RGB):
            limits = {band: (float(saved[band]["lo"]), float(saved[band]["hi"])) for band in RGB}
            log(STEP, f"reused per-band stretch from {stretch_path.name}")
        else:
            log(STEP, "saved stretch does not match these frames; recomputing")
    if limits is None:
        limits = stretch_from_frames(rgb_frames, scl_frames)
        if full_run:
            payload = {"dates": frame_dates}
            for band, (lo, hi) in limits.items():
                payload[band] = {"lo": lo, "hi": hi}
            write_json(stretch_path, payload)
            log(STEP, f"wrote {stretch_path}")
        else:
            log(STEP, "limit run: stretch not saved")
    for band, (lo, hi) in limits.items():
        if hi <= lo:
            log(STEP, f"ERROR degenerate stretch {band} lo={lo} hi={hi}")
            sys.exit(1)

    staging = prepare_staging()
    frames = []
    for day, rgb, frac_scl in zip(frame_dates, rgb_frames, scl_frames):
        planes = [render_uint8(rgb[i], *limits[band]) for i, band in enumerate(RGB)]
        image = Image.fromarray(np.stack(planes, axis=-1), mode="RGB")
        if image.size != (frame_box.width, frame_box.height):
            log(STEP, f"ERROR {day} image is {image.size}, expected {frame_box.width}x{frame_box.height}")
            sys.exit(1)
        name = f"{day}_rgb.jpg"
        path = staging / name
        image.save(path, format="JPEG", quality=JPEG_QUALITY)
        size = path.stat().st_size
        if size > 1_000_000:
            log(STEP, f"WARN {name} is {size} bytes (>1 MB)")
        log(STEP, f"wrote {name} {size // 1024} KB clear_frac={fractions[day]:.3f}")
        frames.append({
            "date": day,
            "rgb": f"frames/{name}",
            "ndvi": None,
            "clear_frac": round(fractions[day], 3),
        })

    doc = {
        "bounds": region["bbox"],
        "width": 1000,
        "height": 900,
        "timelapse": None,
        "frames": frames,
    }
    write_json(staging / "frames.json", doc)
    validate_staging(staging, doc)
    publish(staging)

    as_of = date.fromisoformat(region["as_of"])
    oct1 = date(as_of.year, 10, 1)
    since = [day for day in frame_dates if date.fromisoformat(day) >= oct1]
    latest = frame_dates[-1]
    days_before = (as_of - date.fromisoformat(latest)).days
    log(STEP, f"{len(frame_dates)} frames, {len(since)} since {oct1.isoformat()}")
    log(STEP, f"latest frame {latest}, {days_before} days before as_of {region['as_of']}")
    place = region["name"].split(",")[0].upper()
    if len(frame_dates) >= 4:
        log(STEP, f"{place} GO")
    else:
        log(STEP, f"{place} NO-GO (<4 clear frames)")


if __name__ == "__main__":
    main()
