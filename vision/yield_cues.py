"""Perceptron visual yield cues for each Story County zone.

Reads the season frames (one clear pass per window, all before harvest), crops each
zone, optionally adds the peak-date NDVI panel, and asks Perceptron for lodging,
stand gaps, weeds and drowned spots. No annotation mode: two plain sentences, then
one yes/no line. Merges one note per zone into insights.json and leaves every other
item in place. Does not write per-field cues.

Run Srikar's analyze_timelapse.py first, then this, so a timelapse rewrite of
insights.json does not drop these notes:

    python vision/yield_cues.py --zones Z05,Z01 --cube data/cache/cube.npz
    python vision/yield_cues.py --cube data/cache/cube.npz
"""
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perceptron_client import MODEL, ask, clean_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "yield_cues"
CUES = ("drowned_spots", "stand_gaps", "weed_pressure", "lodging")
_CANON = {
    "drowned": "drowned_spots",
    "drowned_spots": "drowned_spots",
    "gaps": "stand_gaps",
    "stand_gaps": "stand_gaps",
    "weeds": "weed_pressure",
    "weed_pressure": "weed_pressure",
    "lodging": "lodging",
}
_LABEL = {
    "drowned_spots": "drowned spots",
    "stand_gaps": "stand gaps",
    "weed_pressure": "weeds",
    "lodging": "lodging",
}
# Inclusive windows, all before harvest. Never use a frame after Sep 10.
WINDOWS = (
    ("early", "2026-06-10", "2026-06-30", "early season"),
    ("mid", "2026-07-01", "2026-07-20", "mid season"),
    ("peak", "2026-08-01", "2026-08-22", "peak season"),
    ("late", "2026-08-23", "2026-09-10", "late season"),
)
HARVEST_CUTOFF = "2026-09-10"
MIN_CLEAR = 0.85
LONG_SIDE = 768
FIRST_CALL_EST_USD = 0.05
S1_CAP_USD = 2.0

PROMPT = (
    "Sentinel-2 true-colour images, 10 m pixels, north up, of one patch of corn and soybean farmland "
    "in Story County, Iowa, in 2026. Fields are large rectangles on a 1-mile road grid. "
    "The images are in date order: {images}. "
    "Ignore clouds, cloud shadows, roads, farmsteads and tree lines.\n"
    "Look for four problems inside planted fields: drowned spots (bare or dark patches in low spots early "
    "in the season), stand gaps (thin or bare patches inside green fields after the canopy closed), "
    "weeds (patchy, uneven green texture inside fields), lodging (irregular lighter streaks or patches in "
    "corn late in the season). Patches smaller than about three pixels can't be seen.\n"
    "In two short sentences, say what you see across this patch and which part (north, south, east, west "
    "or centre); mention a problem only if it is clearly visible.\n"
    "Finally, answer on one line exactly: drowned=yes|no|unclear gaps=yes|no|unclear "
    "weeds=yes|no|unclear lodging=yes|no|unclear"
)

# Skip the instruction template "yes|no|unclear", where "yes" is followed by a pipe.
_KEY = re.compile(
    r"\b(drowned_spots|drowned|stand_gaps|gaps|weed_pressure|weeds|lodging)"
    r"\s*[=:]\s*(yes|no|unclear)\b(?!\s*\|)",
    re.I,
)


def zone_bbox(feat):
    geom = feat["geometry"]
    if geom["type"] == "Polygon":
        rings = geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        rings = [ring for poly in geom["coordinates"] for ring in poly]
    else:
        raise ValueError(f"unsupported geometry {geom['type']}")
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def choose_frames(frames_doc):
    """Highest clear_frac in each window; tie goes to the later date. Skip below 0.85."""
    chosen = []
    for name, start, end, label in WINDOWS:
        cands = [
            f for f in frames_doc["frames"]
            if start <= f["date"] <= end and f["date"] <= HARVEST_CUTOFF
        ]
        if not cands:
            print(f"  skip {name}: no frame in {start}..{end}")
            continue
        best = max(cands, key=lambda f: (float(f["clear_frac"]), f["date"]))
        if float(best["clear_frac"]) < MIN_CLEAR:
            print(f"  skip {name}: best {best['date']} clear_frac={best['clear_frac']} < {MIN_CLEAR}")
            continue
        chosen.append({**best, "window": name, "label": label})
    for frame in chosen:
        if frame["date"] > HARVEST_CUTOFF:
            raise SystemExit(f"refusing frame after harvest cutoff: {frame['date']}")
    return chosen


def pixel_window(bounds, width, height, zbbox):
    """Zone bbox -> outward-rounded pixel window, clamped to the frame."""
    W, S, E, N = bounds
    x0f = (zbbox[0] - W) / (E - W) * width
    x1f = (zbbox[2] - W) / (E - W) * width
    y0f = (N - zbbox[3]) / (N - S) * height
    y1f = (N - zbbox[1]) / (N - S) * height
    x0 = max(0, min(width, math.floor(x0f)))
    x1 = max(0, min(width, math.ceil(x1f)))
    y0 = max(0, min(height, math.floor(y0f)))
    y1 = max(0, min(height, math.ceil(y1f)))
    if x1 <= x0:
        x1 = min(width, x0 + 1)
    if y1 <= y0:
        y1 = min(height, y0 + 1)
    return x0, y0, x1, y1


def upscale(im):
    """Lanczos so the long side is 768. Never downscale."""
    w, h = im.size
    long_side = max(w, h)
    if long_side == 0 or long_side >= LONG_SIDE:
        return im
    if w >= h:
        nw, nh = LONG_SIDE, max(1, round(h * LONG_SIDE / w))
    else:
        nh, nw = LONG_SIDE, max(1, round(w * LONG_SIDE / h))
    return im.resize((nw, nh), Image.Resampling.LANCZOS)


def save_jpeg(im, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.save(path, quality=90)


def ndvi_to_image(arr):
    """Brown <= 0.2, yellow at 0.5, dark green >= 0.85, gray for NaN."""
    v = np.asarray(arr, dtype=np.float32)
    brown = np.array([139.0, 90.0, 43.0], dtype=np.float32)
    yellow = np.array([230.0, 190.0, 40.0], dtype=np.float32)
    green = np.array([0.0, 90.0, 30.0], dtype=np.float32)
    gray = np.array([128.0, 128.0, 128.0], dtype=np.float32)
    nan = ~np.isfinite(v)
    t1 = np.clip((v - 0.2) / 0.3, 0.0, 1.0)[..., None]
    rgb = brown * (1.0 - t1) + yellow * t1
    t2 = np.clip((v - 0.5) / 0.35, 0.0, 1.0)[..., None]
    hi = (v >= 0.5)[..., None]
    rgb = np.where(hi, yellow * (1.0 - t2) + green * t2, rgb)
    rgb = np.where(nan[..., None], gray, rgb)
    return Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))


def load_cube(path, frames_doc):
    cube = np.load(path, mmap_mode="r")
    if "ndvi" not in cube.files or "dates" not in cube.files:
        raise SystemExit(f"cube {path} is missing dates or ndvi")
    ndvi = cube["ndvi"]
    height, width = int(frames_doc["height"]), int(frames_doc["width"])
    if ndvi.ndim != 3 or ndvi.shape[1] != 900 or ndvi.shape[2] != 1000:
        raise SystemExit(f"cube ndvi shape {ndvi.shape} is not (dates, 900, 1000)")
    if ndvi.shape[1] != height or ndvi.shape[2] != width:
        raise SystemExit(f"cube grid {ndvi.shape[1:]} does not match frames {height}x{width}")
    dates = [str(d) for d in cube["dates"]]
    return ndvi, dates


def _combine(values):
    """Any yes wins; otherwise any unclear; otherwise no. Empty stays unclear."""
    if not values:
        return "unclear"
    if "yes" in values:
        return "yes"
    if "unclear" in values:
        return "unclear"
    return "no"


def parse_cues(text):
    votes = {name: [] for name in CUES}
    for match in _KEY.finditer(text or ""):
        votes[_CANON[match.group(1).lower()]].append(match.group(2).lower())
    cues = [{"cue": name, "seen": _combine(votes[name])} for name in CUES]
    mentioned = sum(1 for name in CUES if votes[name])
    return cues, mentioned


def _and(items):
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + " and " + items[-1]


def sentence_from_cues(cues):
    by = {c["cue"]: c["seen"] for c in cues}
    if all(by[name] == "no" for name in CUES):
        return "Perceptron saw no drowned spots, stand gaps, weeds or lodging in this patch at 10 m."
    yes = [_LABEL[name] for name in CUES if by[name] == "yes"]
    unclear = [_LABEL[name] for name in CUES if by[name] == "unclear"]
    no = [_LABEL[name] for name in CUES if by[name] == "no"]
    bits = []
    if yes:
        bits.append("saw " + _and(yes))
    if unclear:
        bits.append("could not tell about " + _and(unclear))
    if no:
        bits.append("saw no " + _and(no))
    return "Perceptron " + ", and ".join(bits) + " in this patch at 10 m."


def prose_summary(text):
    """Drop key lines, then clean markup. Empty when the model wrote no sentences."""
    kept = []
    for line in (text or "").splitlines():
        stripped = _KEY.sub(" ", line)
        if _KEY.search(line) and len(re.sub(r"[^A-Za-z]+", "", stripped)) < 8:
            continue
        kept.append(stripped)
    return clean_text("\n".join(kept))


def parse_response(text):
    cues, mentioned = parse_cues(text)
    prose = prose_summary(text)
    if len(prose) < 20:
        return cues, sentence_from_cues(cues), mentioned, False
    return cues, prose, mentioned, True


def legend_for(images):
    parts = []
    for i, image in enumerate(images):
        if image["kind"] == "ndvi":
            parts.append(
                f"image {i} = NDVI map for {image['date']}: dark green = dense canopy, "
                "yellow and brown = thin or bare"
            )
        elif image["window"] == "early":
            parts.append(f"image {i} = {image['date']} (early season)")
        elif image["window"] == "late":
            parts.append(f"image {i} = {image['date']} (late season)")
        else:
            parts.append(f"image {i} = {image['date']}")
    return ", ".join(parts)


def build_images(zone_id, zbbox, frames_doc, chosen, data, ndvi, cube_dates):
    bounds = frames_doc["bounds"]
    width, height = int(frames_doc["width"]), int(frames_doc["height"])
    x0, y0, x1, y1 = pixel_window(bounds, width, height, zbbox)
    images = []
    for frame in chosen:
        src = data / frame["rgb"]
        if not src.exists():
            raise SystemExit(f"missing frame {src}")
        im = upscale(Image.open(src).convert("RGB").crop((x0, y0, x1, y1)))
        path = CACHE / "crops" / f"{zone_id}_{frame['date']}.jpg"
        save_jpeg(im, path)
        images.append({
            "path": path, "date": frame["date"], "window": frame["window"], "kind": "rgb",
        })
    peak = next((f for f in chosen if f["window"] == "peak"), None)
    if ndvi is not None and peak is not None:
        if peak["date"] not in cube_dates:
            print(f"  {zone_id}: cube has no {peak['date']}, skipping NDVI panel")
        else:
            band = np.array(ndvi[cube_dates.index(peak["date"]), y0:y1, x0:x1])
            path = CACHE / "crops" / f"{zone_id}_{peak['date']}_ndvi.jpg"
            save_jpeg(upscale(ndvi_to_image(band)), path)
            images.append({"path": path, "date": peak["date"], "window": "peak", "kind": "ndvi"})
    return images


def load_or_blank(path):
    if path.exists():
        doc = json.loads(path.read_text())
        doc["items"] = [it for it in doc.get("items", []) if it.get("topic") != "yield_cues"]
        return doc
    return {"model": MODEL, "spent_usd": 0, "items": [], "field_cues": []}


def write_insights(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)


def refuse_mock(path):
    resolved = path.resolve()
    mock = (ROOT / "data" / "mock").resolve()
    if resolved == mock or mock in resolved.parents:
        raise SystemExit(f"refusing --out {path}: data/mock is off limits")


def _flush(path, items, run_cost):
    doc = load_or_blank(path)
    doc["items"].extend(items)
    doc["yield_cues_model"] = MODEL
    doc["yield_cues_spent_usd"] = round(run_cost, 5)
    doc["spent_usd"] = round(float(doc.get("spent_usd") or 0) + run_cost, 5)
    write_insights(path, doc)


def main():
    ap = argparse.ArgumentParser(description="Perceptron yield cues per zone")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--zones", default="", help="comma-separated zone ids, default all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cube", default="", help="cube.npz with dates and ndvi on the frame grid")
    ap.add_argument("--max-usd", type=float, default=2.0)
    args = ap.parse_args()

    data = Path(args.data)
    out_dir = Path(args.out)
    refuse_mock(out_dir)
    frames_doc = json.loads((data / "frames" / "frames.json").read_text())
    zones = json.loads((data / "zones.geojson").read_text())["features"]
    if args.zones.strip():
        keep = {z.strip() for z in args.zones.split(",") if z.strip()}
        zones = [z for z in zones if z["properties"]["zone_id"] in keep]
        missing = keep - {z["properties"]["zone_id"] for z in zones}
        if missing:
            raise SystemExit(f"unknown zones: {sorted(missing)}")
    if not zones:
        raise SystemExit("no zones selected")

    chosen = choose_frames(frames_doc)
    print("chosen frames:")
    for frame in chosen:
        print(f"  {frame['window']}: {frame['date']} clear_frac={frame['clear_frac']}")
    if not chosen:
        raise SystemExit("no frames cleared the 0.85 bar before Sep 10")

    ndvi = cube_dates = None
    if args.cube:
        ndvi, cube_dates = load_cube(args.cube, frames_doc)

    insights_path = out_dir / "insights.json"
    items = []
    run_cost = 0.0
    observed = []
    (CACHE / "raw").mkdir(parents=True, exist_ok=True)
    spend_path = ROOT / "data" / "out" / "perceptron" / "spend.json"
    already = 0.0
    if spend_path.exists():
        already = float(json.loads(spend_path.read_text()).get("usd") or 0)

    for zone in zones:
        zone_id = zone["properties"]["zone_id"]
        images = build_images(zone_id, zone_bbox(zone), frames_doc, chosen, data, ndvi, cube_dates)
        prompt = PROMPT.format(images=legend_for(images))
        print(f"\n=== {zone_id} ({len(images)} images) ===")
        if args.dry_run:
            print(prompt)
            print(f"  crops: {', '.join(str(im['path'].relative_to(ROOT)) for im in images)}")
            continue

        est = max(observed) if observed else FIRST_CALL_EST_USD
        if est <= 0:
            est = FIRST_CALL_EST_USD
        if run_cost + est > args.max_usd or already + run_cost + est > S1_CAP_USD:
            print(f"stop before {zone_id}: this run ${run_cost:.4f} + est ${est:.4f} "
                  f"would pass the ${min(args.max_usd, S1_CAP_USD - already):.2f} cap")
            break
        try:
            text, cost = ask(
                [im["path"] for im in images], prompt,
                label=f"yield_cues {zone_id}", max_tokens=1024,
            )
        except Exception as exc:
            print(f"stop: Perceptron call failed for {zone_id} ({type(exc).__name__})")
            break
        run_cost += cost
        observed.append(cost)
        (CACHE / "raw" / f"{zone_id}.txt").write_text(text)
        cues, summary, mentioned, prose_ok = parse_response(text)
        item = {
            "zone_id": zone_id,
            "kind": "tile",
            "topic": "yield_cues",
            "summary": summary,
            "dates": [f["date"] for f in chosen],
            "cues": cues,
            "raw": text,
        }
        items.append(item)
        answers = " ".join(f"{c['cue']}={c['seen']}" for c in cues)
        print(f"  parsed ({mentioned} keys, prose={'yes' if prose_ok else 'no'}): {answers}")
        print(f"  summary: {summary}")
        print("--- raw ---")
        print(text)
        print("--- end raw ---")

    if args.dry_run:
        print(f"\ndry-run: {len(zones)} zone(s), no API call, insights.json not written")
        return

    if len(items) != len(zones):
        if items:
            _flush(insights_path, items, run_cost)
            print(f"wrote partial {insights_path} ({len(items)}/{len(zones)} zones)")
        raise SystemExit(f"stopped after {len(items)}/{len(zones)} zones; this run ${run_cost:.4f}")

    _flush(insights_path, items, run_cost)
    print(f"\nwrote {insights_path} ({len(items)} yield_cues items, this run ${run_cost:.4f})")


if __name__ == "__main__":
    main()
