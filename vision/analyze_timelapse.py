"""Perceptron reads each zone's season as a time-lapse and reports when/where harvest and burning happened.

    python vision/analyze_timelapse.py --data data/out [--out data/out] [--mode images|video] [--zones Z01,Z05] [--dry-run] [--verdict vegetation_index]

Inputs (frames/frames.json, frame JPEGs, zones.geojson) come from --data.
Outputs go to --out: insights.json and perceptron/crops/ (zone JPEGs, plus an mp4 in video mode).
Writing into data/mock is refused. --dry-run builds crops and the prompt and makes no API call.

mode=images (default, robust): sends the last N dated frames, cropped to the zone, as separate images.
mode=video : stitches the cropped frames into an mp4 at 1 frame/second and asks for clip annotations;
             clip seconds map back to dates (second k = frame k).
Cost control: each call is small (a few cropped images); check data/out/perceptron/spend.json.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image
from shapely.geometry import box as shapely_box
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perceptron_client import MODEL, ask, clean_text, norm_to_lonlat, parse_annotations, spent_usd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CUE_MENTIONS = {"flood", "stress", "change"}
CUE_KIND = {"flood": "flood", "stress": "stress", "change": "visible_change"}
CROP_AREA = 1000.0 * 1000.0


def crop_zone(img_path, bounds, zbbox, out_path):
    """Save the zone crop. Also return the lon/lat box of the int-rounded pixel window, [W, S, E, N]."""
    im = Image.open(img_path)
    W, S, E, N = bounds
    x0 = (zbbox[0] - W) / (E - W) * im.width
    x1 = (zbbox[2] - W) / (E - W) * im.width
    y0 = (N - zbbox[3]) / (N - S) * im.height
    y1 = (N - zbbox[1]) / (N - S) * im.height
    ix0, iy0, ix1, iy1 = int(x0), int(y0), int(x1), int(y1)
    im.crop((ix0, iy0, ix1, iy1)).save(out_path, quality=90)
    west = W + (ix0 / im.width) * (E - W)
    east = W + (ix1 / im.width) * (E - W)
    north = N - (iy0 / im.height) * (N - S)
    south = N - (iy1 / im.height) * (N - S)
    return out_path, [west, south, east, north]


def zone_bbox(feat):
    xs = [p[0] for p in feat["geometry"]["coordinates"][0]]
    ys = [p[1] for p in feat["geometry"]["coordinates"][0]]
    return [min(xs), min(ys), max(xs), max(ys)]


def parse_counts(text):
    counts = {}
    for key in ("standing", "harvested", "burned"):
        m = re.search(rf"(\d+)\s+(?:\w+\s+){{0,2}}{key}|{key}\D{{0,15}}(\d+)", text, re.I)
        if m:
            counts[key] = int(m.group(1) or m.group(2))
    return counts


def zone_prompt(mode, legend):
    base_q = ("Sentinel-2 satellite images (10 m pixels, north up) of the same ~3 km patch of rice farmland in Punjab, India. "
              "Green = standing rice, tan/light brown = harvested, very dark/black = burned stubble. ")
    if mode == "video":
        return (base_q + f"This video shows one satellite pass per second: {legend.replace('image', 'second')}. "
                "Return clip annotations for when large-scale harvesting happens and when burn scars appear. "
                "Also note any standing water (flooding) or browning or declining patches inside green fields. "
                "Then give approximate counts of standing, harvested and burned fields in the final frame.")
    return (base_q + f"The images are in date order: {legend}. "
            "In one or two sentences, describe when and where harvesting and burning happened across these dates, "
            "and any standing water (flooding) or browning or declining patches inside green fields. "
            "Then give approximate counts of standing, harvested and burned fields in the last image, "
            "formatted exactly as: standing=N harvested=N burned=N")


def cue_prompt(prev_date, latest_date):
    return (
        f"Two Sentinel-2 true-colour images (10 m pixels, north up) of the same ~3 km patch of farmland. "
        f"Image 0 = {prev_date}, image 1 = {latest_date}. On image 1 only, draw boxes around: "
        "'flood' = standing water on fields (dark blue/black, smooth, often along low ground); "
        "'stress' = parts of green fields that are browning or patchy but not cleanly harvested; "
        "'change' = areas that look clearly different from image 0. Use exactly those mention labels. "
        "Mark at most 25 boxes in total. If there is none of a kind, draw no boxes for it."
    )


def load_fields(data):
    path = data / "fields.geojson"
    if not path.exists():
        print(f"WARN: {path} missing; skipping cue calls, field_cues will be empty")
        return None
    by_zone = {}
    for feat in json.loads(path.read_text())["features"]:
        props = feat["properties"]
        by_zone.setdefault(props["zone_id"], []).append((props["field_id"], shape(feat["geometry"])))
    return by_zone


def compass_where(field, inter, overlap):
    if overlap >= 0.8:
        return "whole field"
    minx, miny, maxx, maxy = field.bounds
    dx = (inter.centroid.x - field.centroid.x) / ((maxx - minx) or 1)
    dy = (inter.centroid.y - field.centroid.y) / ((maxy - miny) or 1)
    ns = "N" if dy > 0.15 else ("S" if dy < -0.15 else "")
    ew = "E" if dx > 0.15 else ("W" if dx < -0.15 else "")
    return f"{ns}{ew} part" if ns or ew else "center"


def cue_detail(kind, pct, latest, prev):
    if kind == "flood":
        return f"Perceptron marked standing water on about {pct}% of the field on {latest}."
    if kind == "stress":
        return f"Perceptron marked browning or patchy decline on about {pct}% of the field on {latest}."
    return f"Perceptron marked a clear change on about {pct}% of the field between {prev} and {latest}."


def boxes_from_cues(text):
    parsed = parse_annotations(text)
    boxes = []
    for a in parsed:
        mention = (a.get("mention") or "").strip().lower()
        if a["kind"] == "point_box" and mention in CUE_MENTIONS and len(a["coords"]) == 2:
            boxes.append({**a, "mention": mention})
    if not boxes and re.search(r"<(box|bbox|rect)\b", text or "", re.I):
        print(text)
        sys.exit("cue response used a box tag parse_annotations does not handle")
    on_latest = [b for b in boxes if b["asset_idx"] == 1]
    use = on_latest if on_latest else boxes
    kept = []
    for b in use:
        (x0, y0), (x1, y1) = b["coords"]
        area = abs(x1 - x0) * abs(y1 - y0)
        if area > 0.40 * CROP_AREA or area < 0.0005 * CROP_AREA:
            continue
        b["area"] = area
        kept.append(b)
    return kept[:25]


def cues_for_boxes(boxes, fields, crop_bounds, latest, prev):
    best = {}
    for b in boxes:
        (x0, y0), (x1, y1) = b["coords"]
        lon0, lat0 = norm_to_lonlat(x0, y0, crop_bounds)
        lon1, lat1 = norm_to_lonlat(x1, y1, crop_bounds)
        geom = shapely_box(min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1))
        kind = CUE_KIND[b["mention"]]
        for field_id, field in fields:
            if field.is_empty or field.area <= 0:
                continue
            inter = geom.intersection(field)
            if inter.is_empty:
                continue
            overlap = inter.area / field.area
            if overlap < 0.2 and not geom.covers(field.centroid):
                continue
            key = (field_id, kind)
            prev_best = best.get(key)
            if prev_best and prev_best[0] >= overlap:
                continue
            pct = int(round(overlap * 100))
            cue = {
                "field_id": field_id,
                "kind": kind,
                "observed": True,
                "date": latest,
                "where": compass_where(field, inter, overlap),
                "detail": cue_detail(kind, pct, latest, prev),
                "confidence": round(min(0.9, 0.4 + 0.5 * overlap), 2),
            }
            if kind == "visible_change":
                cue["window"] = [prev, latest]
            best[key] = (overlap, cue)
    return [cue for _, cue in best.values()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--mode", choices=["images", "video"], default="images")
    ap.add_argument("--frames", type=int, default=6, help="how many most-recent clear frames to send")
    ap.add_argument("--zones", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verdict", choices=["perceptron", "vegetation_index"], default="vegetation_index")
    args = ap.parse_args()
    out_dir = Path(args.out)
    mock = (ROOT / "data" / "mock").resolve()
    resolved = out_dir.resolve()
    if resolved == mock or resolved.is_relative_to(mock):
        print("refusing to write into data/mock (committed mock data); use --out data/out")
        sys.exit(1)
    data = Path(args.data)
    frames = json.loads((data / "frames" / "frames.json").read_text())
    zones = json.loads((data / "zones.geojson").read_text())["features"]
    if args.zones:
        keep = set(args.zones.split(","))
        zones = [z for z in zones if z["properties"]["zone_id"] in keep]
    picks = frames["frames"][-args.frames:]
    fields_by_zone = load_fields(data)
    tmp = out_dir / "perceptron" / "crops"
    tmp.mkdir(parents=True, exist_ok=True)

    items = []
    field_cues = []
    n_crops = 0
    cue_totals = {"flood": 0, "stress": 0, "visible_change": 0}
    for z in zones:
        zid, name = z["properties"]["zone_id"], z["properties"]["name"]
        cropped = [crop_zone(data / f["rgb"], frames["bounds"], zone_bbox(z), tmp / f"{zid}_{f['date']}.jpg") for f in picks]
        crops = [path for path, _ in cropped]
        crop_bounds = [bounds for _, bounds in cropped]
        n_crops += len(crops)
        legend = ", ".join(f"image {i} = {f['date']}" for i, f in enumerate(picks))
        prompt = zone_prompt(args.mode, legend)
        zone_fields = fields_by_zone.get(zid, []) if fields_by_zone is not None else []
        prev_date = picks[-2]["date"] if len(picks) >= 2 else None
        latest_date = picks[-1]["date"]
        if args.mode == "video":
            import imageio.v2 as imageio
            mp4 = tmp / f"{zid}.mp4"
            imageio.mimsave(mp4, [imageio.imread(c) for c in crops], fps=1)
        if args.dry_run:
            with Image.open(crops[0]) as im:
                w, h = im.size
            print(f"{zid} crops={len(crops)} size={w}x{h} prompt_chars={len(prompt)}")
            if args.mode == "video":
                print(f"{zid} cue call skipped in video mode")
            elif fields_by_zone is None or prev_date is None:
                print(f"{zid} cue call skipped fields={len(zone_fields)}")
            else:
                print(f"{zid} cue_frames={prev_date} {latest_date} fields={len(zone_fields)}")
            continue
        if args.mode == "video":
            text, _ = ask([mp4], prompt, annotation_format="clip", label=f"timelapse_{zid}", max_tokens=1024)
        else:
            text, _ = ask(crops, prompt, label=f"timelapse_{zid}", max_tokens=1024)
        clips = []
        for a in parse_annotations(text):
            if a["kind"] == "clip" and a["t"]:
                i0 = min(len(picks) - 1, max(0, int(a["t"][0])))
                i1 = min(len(picks) - 1, max(0, int(a["t"][-1])))
                clips.append({"start": picks[i0]["date"], "end": picks[i1]["date"], "label": a["mention"] or ""})
        m = re.search(r"standing\s*=\s*(\d+).*?harvested\s*=\s*(\d+).*?burned\s*=\s*(\d+)", text, re.I | re.S)
        counts = ({"standing": int(m.group(1)), "harvested": int(m.group(2)), "burned": int(m.group(3))} if m else parse_counts(text))
        summary = re.sub(r"standing\s*=.*$", "", clean_text(text), flags=re.I | re.S).strip()
        raw_cues = ""
        kept = []
        zone_cues = []
        if args.mode == "video":
            print(f"  {name}: cue call skipped in video mode")
        elif fields_by_zone is None or prev_date is None:
            print(f"  {name}: cue call skipped")
        else:
            raw_cues, _ = ask(
                [crops[-2], crops[-1]],
                cue_prompt(prev_date, latest_date),
                annotation_format="box",
                label=f"cues_{zid}",
                max_tokens=4096,
            )
            kept = boxes_from_cues(raw_cues)
            zone_cues = cues_for_boxes(kept, zone_fields, crop_bounds[-1], latest_date, prev_date)
            field_cues.extend(zone_cues)
        box_counts = {k: 0 for k in ("flood", "stress", "change")}
        for b in kept:
            box_counts[b["mention"]] += 1
        for cue in zone_cues:
            cue_totals[cue["kind"]] += 1
        items.append({"zone_id": zid, "kind": "timelapse", "summary": summary[:400] or "(no summary)",
                      "counts": counts, "clips": clips, "raw": text, "raw_cues": raw_cues})
        print(f"  {name}: {counts} | boxes {box_counts} | cues {len(zone_cues)} | {summary[:120]}")

    if args.dry_run:
        print(f"dry run: {len(zones)} zones, {n_crops} crops written to {tmp}, 0 API calls")
        return

    field_cues.sort(key=lambda c: (c["field_id"], c["kind"]))
    out = {
        "model": MODEL,
        "spent_usd": round(spent_usd(), 4),
        "as_of": picks[-1]["date"] if picks else None,
        "verdict": args.verdict,
        "field_cues": field_cues,
        "items": items,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    insights = out_dir / "insights.json"
    insights.write_text(json.dumps(out, separators=(",", ":")))
    print(f"cues: {cue_totals} spend ${out['spent_usd']}")
    print(f"wrote {insights}")


if __name__ == "__main__":
    main()
