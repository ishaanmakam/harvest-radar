"""FIRST-HOUR TEST: can Perceptron read our satellite imagery?

    python vision/validate_perceptron.py                     # uses latest clear frame in data/out (else data/mock)
    python vision/validate_perceptron.py --image path.jpg

Runs probes on one image and saves raw + parsed output to data/out/perceptron/validation/:
  1. field boundaries as polygons on the full frame
  1b. the same on one zone tile, upscaled
  2. boxes labelled by landscape state + counts
  3. (unless --skip-change, and if >=4 frames exist) multi-image change question across dates

Outputs always go to data/out/perceptron/validation/, even when the frames are read from data/mock.
"""
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perceptron_client  # noqa: E402
from analyze_timelapse import crop_zone, zone_bbox  # noqa: E402
from landscape import landscape  # noqa: E402
from perceptron_client import ask, clean_text, norm_to_lonlat, parse_annotations  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "out" / "perceptron" / "validation"
COLORS = ["#ff2d2d", "#ffe14a", "#3dff6a", "#3dc2ff", "#d13dff", "#ff8a1e", "#ffffff", "#00e5c3"]
LABEL_COLORS = {"standing": "#3dff6a", "harvested": "#ffe14a", "burned": "#ff2d2d"}


def pick_frames():
    for base in (ROOT / "data" / "out", ROOT / "data" / "mock"):
        fj = base / "frames" / "frames.json"
        if fj.exists():
            doc = json.loads(fj.read_text())
            if doc["frames"]:
                return base, doc
    sys.exit("No frames found. Run pipeline/s05_frames.py (Person A) (or scripts/make_mock_data.py) first.")


def polygon_prompt(scene, limit):
    return (
        f"{scene} Outline each individual agricultural field as a polygon. Follow the field edges "
        "(roads, field boundaries). Do not outline the town, roads, rivers or farmsteads. "
        f"Return at most {limit} polygons, the clearest ones."
    )


def polygons_of(text):
    return [a for a in parse_annotations(text) if a["kind"] == "polygon" and len(a["coords"]) >= 3]


def to_geojson(polys, bounds):
    feats = []
    for poly in polys:
        ring = [list(norm_to_lonlat(x, y, bounds)) for x, y in poly["coords"] + [poly["coords"][0]]]
        feats.append({"type": "Feature", "properties": {"mention": poly["mention"]},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


def _round5(value):
    return int(round(value / 5.0) * 5)


def sanity(polys, finish_reason):
    n = len(polys)
    keys = []
    centroids = []
    areas = []
    edge = 0
    for poly in polys:
        coords = poly["coords"]
        keys.append(tuple((_round5(x), _round5(y)) for x, y in coords))
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        centroids.append((sum(xs) / len(xs), sum(ys) / len(ys)))
        areas.append((max(xs) - min(xs)) * (max(ys) - min(ys)) / 10000.0)
        if min(xs) <= 5 or min(ys) <= 5:
            edge += 1
    if centroids:
        x_coverage = max(c[0] for c in centroids) - min(c[0] for c in centroids)
        y_coverage = max(c[1] for c in centroids) - min(c[1] for c in centroids)
    else:
        x_coverage = y_coverage = 0
    stats = {
        "n_polygons": n,
        "finish_reason": finish_reason,
        "n_duplicates": n - len(set(keys)),
        "y_coverage": round(y_coverage, 1),
        "x_coverage": round(x_coverage, 1),
        "median_area_pct": round(statistics.median(areas), 2) if areas else 0,
        "edge_frac": round(edge / n, 3) if n else 0,
    }
    print("  sanity", json.dumps(stats))
    return stats


def scale(coord, size):
    return coord / 1000.0 * size


def draw_polygons(path, polys, dest):
    im = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(im)
    width, height = im.size
    for i, poly in enumerate(polys):
        pts = [(scale(x, width), scale(y, height)) for x, y in poly["coords"]]
        if len(pts) >= 2:
            draw.line(pts + [pts[0]], fill=COLORS[i % len(COLORS)], width=2)
    im.save(dest)


def draw_boxes(path, boxes, labels, dest):
    im = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(im)
    width, height = im.size
    for i, box in enumerate(boxes):
        if len(box["coords"]) < 2:
            continue
        (x0, y0), (x1, y1) = box["coords"][0], box["coords"][1]
        mention = (box["mention"] or "").lower()
        color = LABEL_COLORS.get(mention, COLORS[i % len(COLORS)])
        draw.rectangle([scale(x0, width), scale(y0, height), scale(x1, width), scale(y1, height)], outline=color, width=2)
    y = 8
    for label in labels:
        color = LABEL_COLORS.get(label, "#ffffff")
        draw.rectangle([8, y, 22, y + 12], fill=color)
        draw.text((28, y), label, fill="#ffffff")
        y += 16
    im.save(dest)


def parse_state_counts(text, labels):
    counts = {}
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*=\s*(\d+)", text or "", re.I)
        if match:
            counts[label] = int(match.group(1))
    return counts


def finish_reason():
    return perceptron_client.LAST_FINISH_REASON


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--tile-zone", default="Z07")
    ap.add_argument("--skip-change", action="store_true")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    base, frames = pick_frames()
    bounds = frames["bounds"]
    latest = base / frames["frames"][-1]["rgb"]
    img = Path(args.image) if args.image else latest
    land = landscape()
    print(f"image: {img}  (data from {base.name}, {land['key']})")
    results = {"landscape": land["key"], "crop_system": land["crop_system"]}

    text, _ = ask([img], polygon_prompt(land["scene"], 60), annotation_format="polygon",
                  label="fields_polygon", max_tokens=args.max_tokens)
    polys = polygons_of(text)
    reason = finish_reason()
    (OUTDIR / "fields_perceptron.geojson").write_text(json.dumps(to_geojson(polys, bounds)))
    full_stats = sanity(polys, reason)
    results["fields_polygon"] = {"n_polygons": len(polys), "finish_reason": reason, "sanity": full_stats, "raw": text}
    draw_polygons(img, polys, OUTDIR / "overlay_full.png")
    print(f"  -> {len(polys)} polygons")

    zones = json.loads((base / "zones.geojson").read_text())["features"]
    zone = next((z for z in zones if z["properties"]["zone_id"] == args.tile_zone), None)
    if zone is None:
        sys.exit(f"zone {args.tile_zone} not in {base / 'zones.geojson'}")
    tile_path, tile_bounds = crop_zone(latest, bounds, zone_bbox(zone), OUTDIR / "tile.jpg", 768)
    text, _ = ask([tile_path], polygon_prompt(land["scene"], 30), annotation_format="polygon",
                  label="fields_polygon_tile", max_tokens=args.max_tokens)
    tile_polys = polygons_of(text)
    reason = finish_reason()
    (OUTDIR / "fields_perceptron_tile.geojson").write_text(json.dumps(to_geojson(tile_polys, tile_bounds)))
    tile_stats = sanity(tile_polys, reason)
    results["fields_polygon_tile"] = {"zone": args.tile_zone, "n_polygons": len(tile_polys), "finish_reason": reason,
                                      "sanity": tile_stats, "bounds": tile_bounds, "raw": text}
    draw_polygons(tile_path, tile_polys, OUTDIR / "overlay_tile.png")
    print(f"  -> {len(tile_polys)} tile polygons ({args.tile_zone})")

    labels = land["state_labels"]
    count_fmt = " ".join(f"{label}=N" for label in labels)
    text, _ = ask([img], (
        f"{land['scene']} Mark each field with a box labelled exactly one of: {', '.join(labels)}. "
        f"Then state the approximate count of each, formatted as: {count_fmt}"),
        annotation_format="box", label="state_boxes", max_tokens=args.max_tokens)
    boxes = [a for a in parse_annotations(text) if a["kind"] == "point_box"]
    by = {}
    for box in boxes:
        by[box["mention"]] = by.get(box["mention"], 0) + 1
    counts = parse_state_counts(text, labels)
    reason = finish_reason()
    results["state_boxes"] = {"by_mention": by, "counts": counts, "finish_reason": reason,
                              "summary": clean_text(text), "raw": text}
    draw_boxes(img, boxes, labels, OUTDIR / "overlay_boxes.png")
    print(f"  -> boxes by label: {by}")
    print(f"  -> counts: {counts}")
    print(f"  -> summary: {clean_text(text)[:300]}")

    if args.skip_change:
        print("  -> change skipped")
    elif len(frames["frames"]) >= 4:
        picks = frames["frames"][-4:]
        dates = ", ".join(f"image {i} = {f['date']}" for i, f in enumerate(picks))
        text, _ = ask([base / f["rgb"] for f in picks], (
            f"{land['scene']} These are satellite images of the same farmland on different dates ({dates}). "
            "Describe how the fields change across the dates: where did green rice turn bare (harvested), "
            "and where did dark burn scars appear? Mention which part of the image (north/south/east/west) and which dates."),
            label="multi_date_change")
        reason = finish_reason()
        results["multi_date_change"] = {"answer": clean_text(text), "finish_reason": reason, "raw": text}
        print(f"  -> change: {clean_text(text)[:400]}")
    else:
        print("  -> change skipped (fewer than 4 frames)")

    (OUTDIR / "results.json").write_text(json.dumps(results, indent=1))
    print(f"\nSaved to {OUTDIR}. Post the verdict in the team chat.")


if __name__ == "__main__":
    main()
