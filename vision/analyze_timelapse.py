"""Perceptron reads each zone's season as a time-lapse and reports when/where harvest and burning happened.

    python vision/analyze_timelapse.py --data data/out [--out data/out] [--mode images|video] [--zones Z01,Z05] [--dry-run] [--verdict vegetation_index] [--cues] [--min-clear 0.85]

Inputs (frames/frames.json, frame JPEGs, zones.geojson) come from --data.
Outputs go to --out: insights.json and perceptron/crops/ (zone JPEGs, plus an mp4 in video mode).
Writing into data/mock is refused. --dry-run builds crops and the prompt and makes no API call.
Frames used are those with clear_frac >= --min-clear, then the last --frames. Box cues run only with --cues.

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
from landscape import landscape  # noqa: E402
from perceptron_client import MODEL, ask, clean_text, norm_to_lonlat, parse_annotations, spent_usd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CUE_MENTIONS = {"flood", "stress", "change"}
CUE_KIND = {"flood": "flood", "stress": "stress", "change": "visible_change"}
CROP_AREA = 1000.0 * 1000.0


def crop_zone(img_path, bounds, zbbox, out_path, crop_px):
    """Save the zone crop. Also return the lon/lat box of the int-rounded pixel window, [W, S, E, N].

    The saved image is resized so its long side is crop_px, and is never downscaled.
    Bounds stay on the source pixel window; box coords are normalized 0-1000.
    """
    im = Image.open(img_path)
    W, S, E, N = bounds
    x0 = (zbbox[0] - W) / (E - W) * im.width
    x1 = (zbbox[2] - W) / (E - W) * im.width
    y0 = (N - zbbox[3]) / (N - S) * im.height
    y1 = (N - zbbox[1]) / (N - S) * im.height
    ix0, iy0, ix1, iy1 = int(x0), int(y0), int(x1), int(y1)
    cropped = im.crop((ix0, iy0, ix1, iy1))
    long_side = max(cropped.size)
    if 0 < long_side < crop_px:
        scale = crop_px / long_side
        cropped = cropped.resize((max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))), Image.LANCZOS)
    cropped.save(out_path, quality=90)
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


def zone_prompt(mode, legend, land):
    scene = land["scene"] + " "
    if mode == "video":
        return (scene + f"This video shows one satellite pass per second: {legend.replace('image', 'second')}. "
                "Return clip annotations for when large-scale harvesting happens and when burn scars appear. "
                "Also note any standing water (flooding) or browning or declining patches inside green fields. "
                "Then give approximate counts of standing, harvested and burned fields in the final frame.")
    return (scene + f"The images are in date order: {legend}. "
            "In two short sentences, say which parts of this patch (north/south/east/west/centre) changed and on which dates, "
            "e.g. fields drying down or being harvested, and mention any flooding or patchy damage only if clearly visible. "
            "Then give approximate counts of standing, harvested and burned fields in the last image, "
            "formatted exactly as: standing=N harvested=N burned=N. "
            "Finally, comparing the last image with the one before it, answer on one line exactly: "
            "flood=yes|no stress=yes|no change=yes|no")


CHANGE_DEF = "'change' = areas that look clearly different from image 0"
_GATE_KEY = re.compile(r"\b(flood|stress|change)\s*=\s*(yes|no)", re.I)


def parse_gate(text):
    found = {}
    for match in _GATE_KEY.finditer(text or ""):
        found[match.group(1).lower()] = match.group(2).lower() == "yes"
    return {"flood": found.get("flood", False), "stress": found.get("stress", False), "change": found.get("change", False)}


def summary_text(text):
    summary = clean_text(text)
    summary = _GATE_KEY.sub("", summary)
    summary = re.sub(r"standing\s*=.*$", "", summary, flags=re.I | re.S)
    return re.sub(r"\s+", " ", summary).strip()


def cue_prompt(prev_date, latest_date, kinds, land):
    defs = {"flood": land["flood_def"], "stress": land["stress_def"], "change": CHANGE_DEF}
    labels = "; ".join(defs[k] for k in ("flood", "stress", "change") if k in kinds)
    return (
        f"{land['scene']} Image 0 = {prev_date}, image 1 = {latest_date}. On image 1 only, draw boxes around: {labels}. "
        "Use exactly those mention labels. Mark at most 25 boxes in total. If there is none of a kind, draw no boxes for it."
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


def _round5(v):
    return int(round(v / 5.0) * 5)


def guard_kind(boxes):
    """After the area filter. Returns (boxes to keep, note after the kind name)."""
    n = len(boxes)
    if n == 0:
        return [], "kept 0 boxes"
    groups = {}
    for b in boxes:
        (x0, y0), (x1, y1) = b["coords"]
        key = (_round5(x0), _round5(y0), _round5(x1), _round5(y1))
        groups.setdefault(key, b)
    if n - len(groups) > n / 2:
        return [], f"dropped (duplicates, {n} boxes)"
    deduped = list(groups.values())

    def edge_frac(idx):
        return sum(1 for b in deduped if b["coords"][0][idx] <= 5) / len(deduped)

    if len(deduped) >= 6 and (edge_frac(0) >= 0.7 or edge_frac(1) >= 0.7):
        return [], f"dropped (edge_scan, {len(deduped)} boxes)"
    if len(deduped) > 12:
        return [], f"dropped (too_many, {len(deduped)} boxes)"
    return deduped, f"kept {len(deduped)} boxes"


def boxes_from_cues(text, allowed):
    """Returns (boxes, notes). boxes is None when the response used an unknown box tag."""
    parsed = parse_annotations(text)
    boxes = []
    for a in parsed:
        mention = (a.get("mention") or "").strip().lower()
        if a["kind"] == "point_box" and mention in allowed and len(a["coords"]) == 2:
            boxes.append({**a, "mention": mention})
    if not boxes and re.search(r"<(box|bbox|rect)\b", text or "", re.I):
        return None, ["cues: unknown tag"]
    on_latest = [b for b in boxes if b["asset_idx"] == 1]
    use = on_latest if on_latest else boxes
    by_kind = {k: [] for k in allowed}
    for b in use:
        (x0, y0), (x1, y1) = b["coords"]
        area = abs(x1 - x0) * abs(y1 - y0)
        if area > 0.40 * CROP_AREA or area < 0.0005 * CROP_AREA:
            continue
        b["area"] = area
        by_kind[b["mention"]].append(b)
    notes, kept = [], []
    for kind in ("flood", "stress", "change"):
        if kind not in allowed:
            notes.append(f"{kind}: gate=no")
            continue
        kept_kind, note = guard_kind(by_kind[kind])
        notes.append(f"{kind}: {note}")
        kept.extend(kept_kind)
    return kept, notes


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


CUES_OFF = "cues: off (box annotations unreliable on real frames; see gate verdict)"


def select_frames(all_frames, min_clear, n):
    """Frames with clear_frac >= min_clear, then the last n. Prints the chosen dates once."""
    clear = [f for f in all_frames if float(f.get("clear_frac") or 0) >= min_clear]
    if len(clear) < 4:
        print(f"STOP: {len(clear)} frames have clear_frac >= {min_clear}; need at least 4")
        sys.exit(1)
    picks = clear[-n:]
    print("frames:", ", ".join(f["date"] for f in picks))
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--mode", choices=["images", "video"], default="images")
    ap.add_argument("--frames", type=int, default=6, help="how many most-recent clear frames to send")
    ap.add_argument("--min-clear", type=float, default=0.85, help="keep frames with clear_frac at least this")
    ap.add_argument("--zones", default="")
    ap.add_argument("--cues", action="store_true", help="box cues when the narration gate says yes (off by default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verdict", choices=["perceptron", "vegetation_index"], default="vegetation_index")
    ap.add_argument("--crop-px", type=int, default=768)
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
    picks = select_frames(frames["frames"], args.min_clear, args.frames)
    chosen_dates = [f["date"] for f in picks]
    land = landscape()
    fields_by_zone = load_fields(data)
    tmp = out_dir / "perceptron" / "crops"
    tmp.mkdir(parents=True, exist_ok=True)

    items = []
    field_cues = []
    n_crops = 0
    cue_totals = {"flood": 0, "stress": 0, "visible_change": 0}
    for z in zones:
        zid, name = z["properties"]["zone_id"], z["properties"]["name"]
        cropped = [crop_zone(data / f["rgb"], frames["bounds"], zone_bbox(z), tmp / f"{zid}_{f['date']}.jpg", args.crop_px) for f in picks]
        crops = [path for path, _ in cropped]
        crop_bounds = [bounds for _, bounds in cropped]
        n_crops += len(crops)
        legend = ", ".join(f"image {i} = {f['date']}" for i, f in enumerate(picks))
        prompt = zone_prompt(args.mode, legend, land)
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
            if z is zones[0]:
                print(prompt[:120])
            print(f"{zid} crops={len(crops)} size={w}x{h} prompt_chars={len(prompt)}")
            if not args.cues:
                if z is zones[0]:
                    print(CUES_OFF)
            elif args.mode == "video":
                print(f"{zid} cue call skipped in video mode")
            elif fields_by_zone is None or prev_date is None:
                print(f"{zid} cue call skipped fields={len(zone_fields)}")
            else:
                print(f"{zid} cue_frames={prev_date} {latest_date} fields={len(zone_fields)}")
            continue
        try:
            if args.mode == "video":
                text, _ = ask([mp4], prompt, annotation_format="clip", label=f"timelapse_{zid}", max_tokens=1024)
            else:
                text, _ = ask(crops, prompt, label=f"timelapse_{zid}", max_tokens=1024)
        except RuntimeError as e:
            print(e)
            break
        clips = []
        for a in parse_annotations(text):
            if a["kind"] == "clip" and a["t"]:
                i0 = min(len(picks) - 1, max(0, int(a["t"][0])))
                i1 = min(len(picks) - 1, max(0, int(a["t"][-1])))
                clips.append({"start": picks[i0]["date"], "end": picks[i1]["date"], "label": a["mention"] or ""})
        m = re.search(r"standing\s*=\s*(\d+).*?harvested\s*=\s*(\d+).*?burned\s*=\s*(\d+)", text, re.I | re.S)
        counts = ({"standing": int(m.group(1)), "harvested": int(m.group(2)), "burned": int(m.group(3))} if m else parse_counts(text))
        summary = summary_text(text)
        gate = parse_gate(text) if args.mode != "video" else {"flood": False, "stress": False, "change": False}
        raw_cues = ""
        zone_cues = []
        cue_notes = []
        yes_kinds = {k for k, on in gate.items() if on}
        if not args.cues:
            cue_notes = [CUES_OFF]
        elif args.mode == "video":
            cue_notes = ["cues: skipped in video mode"]
        elif fields_by_zone is None or prev_date is None:
            cue_notes = [f"{k}: gate={'yes' if gate[k] else 'no'}" for k in ("flood", "stress", "change")]
            cue_notes.append("cues: skipped")
        elif not yes_kinds:
            cue_notes = [f"{k}: gate=no" for k in ("flood", "stress", "change")]
        else:
            try:
                raw_cues, _ = ask(
                    [crops[-2], crops[-1]],
                    cue_prompt(prev_date, latest_date, yes_kinds, land),
                    annotation_format="box",
                    label=f"cues_{zid}",
                    max_tokens=4096,
                )
            except RuntimeError as e:
                print(e)
                break
            kept, cue_notes = boxes_from_cues(raw_cues, yes_kinds)
            if kept is None:
                print(raw_cues)
            else:
                zone_cues = cues_for_boxes(kept, zone_fields, crop_bounds[-1], latest_date, prev_date)
                field_cues.extend(zone_cues)
        for cue in zone_cues:
            cue_totals[cue["kind"]] += 1
        items.append({"zone_id": zid, "kind": "timelapse", "summary": summary[:400] or "(no summary)",
                      "counts": counts, "clips": clips, "raw": text, "raw_cues": raw_cues,
                      "gate": gate, "cue_notes": cue_notes, "frames": chosen_dates})
        print(f"  {name}: {counts} | {cue_notes} | cues {len(zone_cues)} | {summary[:120]}")

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
