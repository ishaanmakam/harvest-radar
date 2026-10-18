"""Seed points on a 6x6 grid, then trace the one field under each seed.

    python vision/outline_fields.py --stage seeds --dry-run
    python vision/outline_fields.py --stage seeds
    python vision/outline_fields.py --stage trace --variant A --zones Z01,Z07
    python vision/outline_fields.py --stage trace --variant A

Trace variants, tuned only on seeds inside Z01 and Z07:
  A  200 px window, the trace prompt below
  B  160 px window, same prompt
  C  200 px window, plus "use the colour under the dot"
A trace that touches 2 or more window sides is retried once at 320 px.
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perceptron_client  # noqa: E402
from eval.common import ROOT, SPEND_CAP, area_ha, frame_path, load_json, zone_of  # noqa: E402
from landscape import landscape  # noqa: E402
from marks import draw_seed, px_to_lonlat, upscale  # noqa: E402
from perceptron_client import MODEL, ask, parse_annotations, spent_usd  # noqa: E402

EVAL = ROOT / "data" / "out" / "perceptron" / "eval"
START_FILE = EVAL / "spend_start.json"
LATEST = "2026-10-17"
SEED_PROMPT = (
    "Put one point at the centre of each separate crop field in this image. "
    "Do not mark roads, farmsteads, the town, trees or water. At most 10 points."
)
TRACE_PROMPT = (
    "Outline the single farm field that contains the red dot. Follow its real edges: "
    "roads, fence lines and boundaries with neighbouring fields of a different colour or texture. "
    "Return exactly one polygon."
)
COLOUR_SENTENCE = " Use the colour of the crop under the red dot, and do not cross into a neighbour of a different colour."
VARIANTS = {
    "A": {"window": 200, "retry": 320, "colour": False},
    "B": {"window": 160, "retry": 320, "colour": False},
    "C": {"window": 200, "retry": 320, "colour": True},
}


def guard():
    START_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not START_FILE.exists():
        START_FILE.write_text(json.dumps({"usd": round(spent_usd(), 5)}))
    start = json.loads(START_FILE.read_text())["usd"]
    risen = spent_usd() - start
    if risen > SPEND_CAP:
        sys.exit(f"STOP spend rose ${risen:.4f} above ${start:.5f} (cap ${SPEND_CAP:.2f})")


def window_of(ix0, iy0, ix1, iy1, bounds, width, height):
    west, south, east, north = bounds
    return [
        west + (ix0 / width) * (east - west),
        north - (iy1 / height) * (north - south),
        west + (ix1 / width) * (east - west),
        north - (iy0 / height) * (north - south),
    ]


def points_of(text):
    found = []
    for ann in parse_annotations(text):
        if ann["kind"] == "point":
            found.extend(ann["coords"])
    return found


def edge_scan(points):
    if len(points) > 10:
        return True
    if not points:
        return False
    left = sum(1 for x, _y in points if x <= 5) / len(points)
    top = sum(1 for _x, y in points if y <= 5) / len(points)
    return left >= 0.70 or top >= 0.70


def dedupe(seeds, min_px=12):
    kept = []
    for seed in seeds:
        if any((seed["sx"] - other["sx"]) ** 2 + (seed["sy"] - other["sy"]) ** 2 <= min_px ** 2 for other in kept):
            continue
        kept.append(seed)
    return kept


def save_tile(crop, dest):
    image, _scale = upscale(crop, 768)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=85)
    return image


def run_seeds(data, dry_run):
    frames = load_json(data / "frames" / "frames.json")
    path = frame_path(frames, LATEST, data)
    frame = Image.open(path).convert("RGB")
    width, height = frame.size
    bounds = frames["bounds"]
    scene = landscape()["scene"]
    prompt = scene + " " + SEED_PROMPT
    seeds = []
    calls = []
    grid = 6
    for row in range(grid):
        for col in range(grid):
            ix0 = int(round(col * width / grid))
            ix1 = int(round((col + 1) * width / grid))
            iy0 = int(round(row * height / grid))
            iy1 = int(round((row + 1) * height / grid))
            crop = frame.crop((ix0, iy0, ix1, iy1))
            dest = EVAL / "seeds" / f"tile_{row}_{col}.jpg"
            save_tile(crop, dest)
            print(f"tile {row},{col} {ix1 - ix0}x{iy1 - iy0} -> {dest.name}")
            if dry_run:
                if row == 0 and col == 0:
                    print(prompt[:220])
                continue
            guard()
            try:
                text, _cost = ask([dest], prompt, annotation_format="point", label=f"seed_{row}_{col}", max_tokens=1024)
            except RuntimeError as exc:
                print(exc)
                sys.exit(1)
            guard()
            reason = perceptron_client.LAST_FINISH_REASON
            pts = points_of(text)
            dropped = edge_scan(pts)
            tile_seeds = []
            if not dropped:
                window = window_of(ix0, iy0, ix1, iy1, bounds, width, height)
                for x, y in pts[:10]:
                    sx = ix0 + (x / 1000.0) * (ix1 - ix0)
                    sy = iy0 + (y / 1000.0) * (iy1 - iy0)
                    lon, lat = px_to_lonlat(x / 1000.0 * (ix1 - ix0), y / 1000.0 * (iy1 - iy0), window, ix1 - ix0, iy1 - iy0)
                    tile_seeds.append({"lon": lon, "lat": lat, "sx": sx, "sy": sy, "tile": [row, col]})
            calls.append({"tile": [row, col], "finish_reason": reason, "n_points": len(pts), "dropped": dropped, "raw": text})
            seeds.extend(tile_seeds)
            print(f"  finish={reason} points={len(pts)} dropped={dropped}")
    if dry_run:
        print(f"dry run: {grid * grid} seed tiles, 0 API calls")
        return
    seeds = dedupe(seeds)
    zones = load_json(data / "zones.geojson")["features"]
    for seed in seeds:
        seed["zone_id"] = zone_of(seed["lon"], seed["lat"], zones)
    doc = {"model": MODEL, "seeds": seeds, "calls": calls}
    dest = EVAL / "seeds.json"
    dest.write_text(json.dumps(doc))
    print(f"{len(seeds)} seeds after dedupe, wrote {dest.relative_to(ROOT)}")


def sides_touched(coords):
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    sides = 0
    if min(xs) <= 5:
        sides += 1
    if max(xs) >= 995:
        sides += 1
    if min(ys) <= 5:
        sides += 1
    if max(ys) >= 995:
        sides += 1
    return sides


def crop_window(frame, bounds, sx, sy, size):
    width, height = frame.size
    half = size // 2
    ix0 = int(round(sx)) - half
    iy0 = int(round(sy)) - half
    ix1 = ix0 + size
    iy1 = iy0 + size
    crop = frame.crop((ix0, iy0, ix1, iy1))
    window = window_of(ix0, iy0, ix1, iy1, bounds, width, height)
    image, _scale = upscale(crop, 768)
    px = (sx - ix0) / size * image.width
    py = (sy - iy0) / size * image.height
    draw_seed(image, px, py)
    return image, window


def polygon_from(text, window, image):
    polys = [a for a in parse_annotations(text) if a["kind"] == "polygon" and len(a["coords"]) >= 4]
    if not polys:
        return None, None
    ann = polys[0]
    ring = []
    for x, y in ann["coords"]:
        lon, lat = px_to_lonlat(x / 1000.0 * image.width, y / 1000.0 * image.height, window, image.width, image.height)
        ring.append([lon, lat])
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    geom = shape({"type": "Polygon", "coordinates": [ring]})
    if not geom.is_valid:
        geom = geom.buffer(0)
    return ann["coords"], geom


def accept(coords, geom, seed):
    if coords is None or geom is None or geom.is_empty or geom.geom_type != "Polygon":
        return False
    if len({(round(x, 1), round(y, 1)) for x, y in coords}) < 4:
        return False
    hectares = area_ha(geom)
    if not 2 <= hectares <= 250:
        return False
    if not geom.buffer(1e-5).covers(Point(seed["lon"], seed["lat"])):
        return False
    return True


def trace_one(frame, bounds, seed, size, prompt, dest):
    image, window = crop_window(frame, bounds, seed["sx"], seed["sy"], size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=80)
    guard()
    text, _cost = ask([dest], prompt, annotation_format="polygon", label=dest.stem, max_tokens=512)
    guard()
    reason = perceptron_client.LAST_FINISH_REASON
    coords, geom = polygon_from(text, window, image)
    ok = accept(coords, geom, seed)
    return {
        "ok": ok,
        "reason": reason,
        "raw": text,
        "coords": coords,
        "geom": geom,
        "image": str(dest.relative_to(ROOT)),
        "touched": sides_touched(coords) if coords else 0,
        "window": window,
    }


def draw_result(path, coords):
    image = Image.open(path).convert("RGB")
    if not coords:
        return
    draw = ImageDraw.Draw(image)
    pts = [(x / 1000.0 * image.width, y / 1000.0 * image.height) for x, y in coords]
    draw.line(pts + [pts[0]], fill="#3dc2ff", width=3)
    image.save(path, quality=80)


def feature_of(seed, geom, retry, reason, image):
    return {
        "type": "Feature",
        "properties": {
            "seed_lonlat": [seed["lon"], seed["lat"]],
            "zone_id": seed.get("zone_id"),
            "area_ha": round(area_ha(geom), 2),
            "retry": retry,
            "finish_reason": reason,
            "image": image,
        },
        "geometry": {"type": "Polygon", "coordinates": [list(geom.exterior.coords)]},
    }


def run_trace(data, variant, zones, dry_run):
    spec = VARIANTS[variant]
    seeds_path = EVAL / "seeds.json"
    if not seeds_path.exists():
        sys.exit("STOP seeds.json missing; run --stage seeds first")
    doc = load_json(seeds_path)
    seeds = doc["seeds"]
    if zones:
        keep = set(zones.split(","))
        seeds = [s for s in seeds if s.get("zone_id") in keep]
    out_path = EVAL / f"outlines_{variant}.geojson"
    have = {}
    if out_path.exists():
        old = load_json(out_path)
        for feat in old["features"]:
            lon, lat = feat["properties"]["seed_lonlat"]
            have[(round(lon, 5), round(lat, 5))] = feat
    scene = landscape()["scene"]
    prompt = scene + " " + TRACE_PROMPT + (COLOUR_SENTENCE if spec["colour"] else "")
    frames = load_json(data / "frames" / "frames.json")
    frame = Image.open(frame_path(frames, LATEST, data)).convert("RGB")
    bounds = frames["bounds"]
    features = list(have.values())
    todo = [s for s in seeds if (round(s["lon"], 5), round(s["lat"], 5)) not in have]
    calls_path = EVAL / f"outlines_{variant}_calls.json"
    done_path = EVAL / f"outlines_{variant}_done.json"
    call_log = load_json(calls_path) if calls_path.exists() else []
    done = {tuple(pair) for pair in load_json(done_path)} if done_path.exists() else set()
    todo = [s for s in todo if (round(s["lon"], 5), round(s["lat"], 5)) not in done]
    print(f"variant {variant} window={spec['window']} traces={len(todo)} reused={len(have)} already={len(done)}")
    if dry_run:
        for i, seed in enumerate(todo[:3]):
            image, _window = crop_window(frame, bounds, seed["sx"], seed["sy"], spec["window"])
            dest = EVAL / "traces" / f"{variant}_{i:04d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            image.save(dest, quality=80)
        if todo:
            print(prompt[:240])
        print(f"dry run: {len(todo)} traces not sent, 0 API calls")
        return
    for i, seed in enumerate(todo):
        dest = EVAL / "traces" / f"{variant}_{seed.get('zone_id') or 'na'}_{i:04d}.jpg"
        try:
            result = trace_one(frame, bounds, seed, spec["window"], prompt, dest)
        except RuntimeError as exc:
            print(exc)
            break
        done.add((round(seed["lon"], 5), round(seed["lat"], 5)))
        done_path.write_text(json.dumps(sorted(done)))
        call_log.append({"finish_reason": result["reason"], "zone_id": seed.get("zone_id"), "retry": False})
        retry = False
        if result["touched"] >= 2:
            retry_dest = dest.with_name(dest.stem + "_retry.jpg")
            try:
                second = trace_one(frame, bounds, seed, spec["retry"], prompt, retry_dest)
            except RuntimeError as exc:
                print(exc)
                break
            retry = True
            call_log.append({"finish_reason": second["reason"], "zone_id": seed.get("zone_id"), "retry": True})
            if second["ok"]:
                result = second
                dest = retry_dest
        draw_result(dest, result["coords"])
        if result["ok"]:
            features.append(feature_of(seed, result["geom"], retry, result["reason"], str(dest.relative_to(ROOT))))
        print(f"  {i + 1}/{len(todo)} ok={result['ok']} finish={result['reason']} retry={retry} touched={result['touched']}")
    merged = merge_features(features, bounds)
    collection = {"type": "FeatureCollection", "features": merged}
    out_path.write_text(json.dumps(collection))
    reasons = {}
    for call in call_log:
        reasons[call["finish_reason"]] = reasons.get(call["finish_reason"], 0) + 1
    calls_path.write_text(json.dumps(call_log))
    done_path.write_text(json.dumps(sorted(done)))
    metrics = {"variant": variant, "n_seeds": len(seeds), "n_kept": len(merged), "n_calls": len(call_log),
               "finish_reasons": reasons, "all_trace_stop": all(c["finish_reason"] == "stop" for c in call_log)}
    (EVAL / f"outlines_{variant}_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"kept {len(merged)} wrote {out_path.relative_to(ROOT)}")


def merge_features(features, bounds):
    west, south, east, north = bounds
    ranked = []
    for feat in features:
        geom = shape(feat["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        centroid = geom.centroid
        if not (west <= centroid.x <= east and south <= centroid.y <= north):
            continue
        ranked.append((area_ha(geom), geom, feat))
    ranked.sort(key=lambda row: -row[0])
    kept_geoms, kept = [], []
    for _area, geom, feat in ranked:
        if any(_iou(geom, other) > 0.5 for other in kept_geoms):
            continue
        kept_geoms.append(geom)
        feat["geometry"] = {"type": "Polygon", "coordinates": [list(geom.exterior.coords)]}
        feat["properties"]["area_ha"] = round(area_ha(geom), 2)
        kept.append(feat)
    return kept


def _iou(a, b):
    inter = a.intersection(b).area
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--stage", choices=["seeds", "trace"], required=True)
    ap.add_argument("--variant", choices=["A", "B", "C"], default="A")
    ap.add_argument("--zones", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = ROOT / args.data
    if args.stage == "seeds":
        run_seeds(data, args.dry_run)
    else:
        run_trace(data, args.variant, args.zones, args.dry_run)


if __name__ == "__main__":
    main()
