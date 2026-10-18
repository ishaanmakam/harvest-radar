"""Set-of-marks standing/harvested and change labels. No box annotations.

Round 1 (kept on disk, not retuned):
    python vision/classify_fields.py --task state --variant A --zones Z01,Z07 --dry-run

Round 2 (this prompt, variants D-F only):
    python vision/classify_fields.py --task state --variant D --zones Z01,Z07 --dry-run
    python vision/classify_fields.py --task change --variant E --zones Z01,Z07

State D is colour-only. E adds two captioned examples from Z01 and drops them from scoring.
F also sends the same outlines on 2026-10-02. Change D/E/F colour-match Oct 10 to Oct 17.
E and F then ask a yes/no close-up for every field the group call marked changed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perceptron_client  # noqa: E402
from eval.common import DEV_ZONES, ROOT, check_truth_geometry, frame_path, load_json  # noqa: E402
from landscape import landscape  # noqa: E402
from marks import bold_font, crop_bounds, draw_marks, group_fields, upscale  # noqa: E402
from perceptron_client import MODEL, ask, spent_usd  # noqa: E402

EVAL = ROOT / "data" / "out" / "perceptron" / "eval"
MARKS = EVAL / "marks"
START_FILE = EVAL / "spend_round2_start.json"
ROUND2_CAP = 2.00
STATE_LINE = re.compile(r"^(\d+)\s*:\s*(standing|harvested|unsure)\s*$", re.I)
CHANGE_LINE = re.compile(r"^(\d+)\s*:\s*(changed|unchanged|unsure)\s*$", re.I)
YESNO = re.compile(r"\b(yes|no)\b", re.I)
PREV, MID, LATEST = "2026-10-10", "2026-10-02", "2026-10-17"

STATE_ASK = (
    "Each numbered yellow outline is one farm field. For each number, say whether the crop is still "
    "STANDING (dark brown or maroon, uniform canopy, no stubble lines) or HARVESTED (light tan stubble "
    "or freshly tilled dark soil with visible pass lines). Answer one line per number, exactly: "
    "<number>: standing | harvested | unsure. Use unsure only if you cannot tell."
)
CHANGE_ASK = (
    f"Image 0 = {PREV}, image 1 = {LATEST}. Each numbered yellow outline is one farm field, and the "
    "numbers match across the two images. For each number, did the field visibly change between image 0 "
    "and image 1 (harvested, tilled, flooded or otherwise different), ignoring small colour shifts between "
    "passes? Answer one line per number, exactly: <number>: changed | unchanged | unsure."
)
LOOK = "First look at the colour inside each outline, then answer."
STATE_D = (
    "Each numbered yellow outline is one farm field. "
    "STANDING = dark brown or maroon, fairly uniform (in mid-October this is mature crop still in the field). "
    "HARVESTED = light tan or beige stubble, often with visible combine pass lines. "
    "Answer one line per number, exactly: <number>: standing | harvested | unsure. Use unsure only if you cannot tell."
)
STATE_F_EXTRA = (
    "Fields that were dark on Oct 2 and are light tan on Oct 17 were harvested; "
    "fields dark on both dates are standing."
)
CHANGE_D = (
    f"The first field image is {PREV} colour-matched to {LATEST}. The second is {LATEST}. "
    "Each numbered yellow outline is one farm field, and the numbers match across the two images. "
    "CHANGED only if the field turned from dark crop to light tan stubble, was freshly tilled from tan to dark, "
    "or shows new standing water. Ignore overall brightness or colour differences between the two images. "
    "Answer one line per number, exactly: <number>: changed | unchanged | unsure."
)
VERIFY_ASK = (
    "Left is Oct 10, right is Oct 17. Did this field clearly go from dark crop to light tan stubble, "
    "from tan to dark tilled soil, or gain standing water? Answer yes or no."
)


def variant_spec(task, name):
    ask = STATE_ASK if task == "state" else CHANGE_ASK
    if name == "B":
        ask = ask.replace("Answer one line per number", LOOK + " Answer one line per number")
    return {"prompt": ask, "max_per_group": 8 if name == "C" else 12}


def guard():
    """Abort if this prompt's spend has risen more than $2.00 from its own start."""
    START_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not START_FILE.exists():
        START_FILE.write_text(json.dumps({"usd": round(spent_usd(), 5)}))
        print(f"round 2 spend start ${spent_usd():.5f} cap ${ROUND2_CAP:.2f}")
    start = json.loads(START_FILE.read_text())["usd"]
    risen = spent_usd() - start
    if risen > ROUND2_CAP:
        sys.exit(f"STOP spend rose ${risen:.4f} above ${start:.5f} (cap ${ROUND2_CAP:.2f})")
    return start


def parse_lines(text, pattern, id_map):
    found = {}
    for line in (text or "").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        number = int(match.group(1))
        if number in id_map:
            found[number] = match.group(2).lower()
    labels = {}
    for number, field_id in id_map.items():
        labels[field_id] = found.get(number, "unsure")
    return labels


def parse_yes_no(text):
    match = YESNO.search(text or "")
    return match.group(1).lower() if match else None


def render_group(frame, bounds, bbox, fields, dest, source=None):
    crop, window = crop_bounds(frame, bounds, bbox)
    crop, _scale = upscale(crop, 768)
    if source is not None:
        if source.size != crop.size:
            source = source.resize(crop.size, Image.LANCZOS)
        crop = source
    marked, id_map = draw_marks(crop, window, fields)
    dest.parent.mkdir(parents=True, exist_ok=True)
    marked.save(dest, quality=85)
    return dest, {int(k): v for k, v in id_map.items()}, window


def histogram_match(source, reference):
    """Match each RGB channel of source onto reference. Both are PIL images."""
    src = np.asarray(source.convert("RGB"))
    ref = np.asarray(reference.convert("RGB"))
    out = np.empty_like(src)
    for channel in range(3):
        out[..., channel] = _match_channel(src[..., channel], ref[..., channel])
    return Image.fromarray(out)


def _match_channel(source, reference):
    src_values, src_counts = np.unique(source, return_counts=True)
    ref_values, ref_counts = np.unique(reference, return_counts=True)
    src_cdf = np.cumsum(src_counts).astype(np.float64)
    src_cdf /= src_cdf[-1]
    ref_cdf = np.cumsum(ref_counts).astype(np.float64)
    ref_cdf /= ref_cdf[-1]
    mapped = np.interp(src_cdf, ref_cdf, ref_values.astype(np.float64))
    lut = np.arange(256, dtype=np.uint8)
    lut[src_values] = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
    return lut[source]


def burn_caption(image, text):
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 40], fill="#18221d")
    draw.text((10, 8), text, fill="#ffe14d", font=bold_font(22))
    return image


def field_bbox(feat, frac, frame_bounds):
    geom = shape(feat["geometry"])
    minx, miny, maxx, maxy = geom.bounds
    width = max(maxx - minx, 1e-5)
    height = max(maxy - miny, 1e-5)
    west, south, east, north = frame_bounds
    return [
        max(west, minx - frac * width),
        max(south, miny - frac * height),
        min(east, maxx + frac * width),
        min(north, maxy + frac * height),
    ]


def stitch(left, right):
    height = max(left.height, right.height)

    def fit(image):
        if image.height == height:
            return image
        width = max(1, round(image.width * height / image.height))
        return image.resize((width, height), Image.LANCZOS)

    left, right = fit(left), fit(right)
    canvas = Image.new("RGB", (left.width + right.width, height))
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    long_side = max(canvas.size)
    if long_side > 768:
        scale = 768 / long_side
        canvas = canvas.resize((max(1, round(canvas.width * scale)), max(1, round(canvas.height * scale))), Image.LANCZOS)
    return canvas


def score_block(task, predictions, exclude):
    sys.path.insert(0, str(ROOT / "vision"))
    from eval.metrics import classification

    truth = load_json(ROOT / "vision" / "eval" / "truth.json")["fields"]
    excluded = sorted(set(exclude) & set(predictions))
    kept = {fid: label for fid, label in predictions.items() if fid not in exclude}
    skipped = sorted(fid for fid in kept if fid not in truth)
    if task == "state":
        labelled = {fid: truth[fid]["state_oct17"] for fid in kept if fid in truth}
        labels = ["standing", "harvested"]
        focus = None
    else:
        rename = {"yes": "changed", "no": "unchanged", "unsure": "unsure"}
        labelled = {fid: rename[truth[fid]["changed"]] for fid in kept if fid in truth}
        labels = ["changed", "unchanged"]
        focus = "changed"
    stats = classification({fid: kept[fid] for fid in labelled}, labelled, labels)
    return stats, skipped, excluded, focus


def phrase(stats, label, kind):
    confusion = stats["confusion"][label]
    tp = confusion.get(label, 0)
    if kind == "recall":
        support = sum(confusion.values())
        value = stats["per_label"][label]["recall"]
        return f"{value:.2f} ({tp} of {support})"
    predicted = sum(row.get(label, 0) for row in stats["confusion"].values())
    value = stats["per_label"][label]["precision"]
    return f"{value:.2f} ({tp} of {predicted})"


def example_fields(task, by_id, truth):
    """Lowest Z01 field of each class. These are the only example fields."""
    z01 = sorted(fid for fid, feat in by_id.items() if feat["properties"]["zone_id"] == "Z01" and fid in truth)
    if task == "state":
        standing = next(fid for fid in z01 if truth[fid]["state_oct17"] == "standing")
        harvested = next(fid for fid in z01 if truth[fid]["state_oct17"] == "harvested")
        return {"standing": standing, "harvested": harvested}
    changed = next(fid for fid in z01 if truth[fid]["changed"] == "yes")
    unchanged = next(fid for fid in z01 if truth[fid]["changed"] == "no")
    return {"changed": changed, "unchanged": unchanged}


def matched_crops(prev_path, latest_path, bounds, bbox):
    latest, window = crop_bounds(latest_path, bounds, bbox)
    prev, _prev_window = crop_bounds(prev_path, bounds, bbox)
    latest, _scale = upscale(latest, 768)
    prev, _scale = upscale(prev, 768)
    if prev.size != latest.size:
        prev = prev.resize(latest.size, Image.LANCZOS)
    return histogram_match(prev, latest), latest, window


def save_state_examples(by_id, picked, latest, bounds):
    paths = {}
    for label, fid in picked.items():
        dest = MARKS / f"r2_example_state_{label}_{fid}.jpg"
        bbox = field_bbox(by_id[fid], 0.30, bounds)
        _path, _id_map, _window = render_group(latest, bounds, bbox, [by_id[fid]], dest)
        image = burn_caption(Image.open(dest).convert("RGB"), f"EXAMPLE: {label.upper()}")
        image.save(dest, quality=85)
        paths[label] = dest
    return paths


def save_change_examples(by_id, picked, prev_path, latest_path, bounds):
    paths = {}
    for label, fid in picked.items():
        dest = MARKS / f"r2_example_change_{label}_{fid}.jpg"
        bbox = field_bbox(by_id[fid], 0.30, bounds)
        matched, latest, window = matched_crops(prev_path, latest_path, bounds, bbox)
        left, _map = draw_marks(matched, window, [by_id[fid]])
        right, _map2 = draw_marks(latest, window, [by_id[fid]])
        image = burn_caption(stitch(left, right), f"EXAMPLE: {label.upper()}  |  left Oct 10, right Oct 17")
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, quality=85)
        paths[label] = dest
    return paths


def verify_field(fid, feat, prev_path, latest_path, bounds, dest):
    bbox = field_bbox(feat, 0.30, bounds)
    matched, latest, window = matched_crops(prev_path, latest_path, bounds, bbox)
    left, _map = draw_marks(matched, window, [feat])
    right, _map2 = draw_marks(latest, window, [feat])
    image = stitch(left, right)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=85)
    return dest


def ask_guarded(images, prompt, label, max_tokens):
    guard()
    try:
        text, _cost = ask(images, prompt, label=label, max_tokens=max_tokens)
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)
    guard()
    return text, perceptron_client.LAST_FINISH_REASON


def run_round1(args, data):
    spec = variant_spec(args.task, args.variant)
    frames = load_json(data / "frames" / "frames.json")
    zones = load_json(data / "zones.geojson")["features"]
    if args.zones:
        keep = set(args.zones.split(","))
        zones = [z for z in zones if z["properties"]["zone_id"] in keep]
    by_zone = {}
    for feat in load_json(data / "fields.geojson")["features"]:
        by_zone.setdefault(feat["properties"]["zone_id"], []).append(feat)
    scene = landscape()["scene"]
    prompt = scene + " " + spec["prompt"]
    latest = frame_path(frames, LATEST, data)
    prev = frame_path(frames, PREV, data)
    pattern = STATE_LINE if args.task == "state" else CHANGE_LINE
    predictions, calls, n_would = {}, [], 0
    for zone in zones:
        zid = zone["properties"]["zone_id"]
        groups = group_fields(by_zone.get(zid, []), spec["max_per_group"])
        for index, (bbox, fields) in enumerate(groups):
            n_would += 1
            tag = f"{args.task}_{args.variant}_{zid}_{index}"
            dest = MARKS / f"{tag}.jpg"
            path, id_map, _window = render_group(latest, frames["bounds"], bbox, fields, dest)
            images = [str(path.relative_to(ROOT))]
            if args.task == "change":
                prev_dest = MARKS / f"{tag}_prev.jpg"
                prev_path, prev_map, _window = render_group(prev, frames["bounds"], bbox, fields, prev_dest)
                if prev_map != id_map:
                    sys.exit(f"STOP numbering differs on {tag}")
                images = [str(prev_path.relative_to(ROOT)), images[0]]
            print(f"{tag} fields={len(fields)} images={len(images)} prompt_chars={len(prompt)}")
            if args.dry_run:
                if index == 0 and zone is zones[0]:
                    print(prompt[:240])
                continue
            text, reason = ask_guarded([ROOT / rel for rel in images], prompt, tag, 512)
            labels = parse_lines(text, pattern, id_map)
            predictions.update(labels)
            calls.append({
                "zone_id": zid, "group": index,
                "field_ids": [feat["properties"]["field_id"] for feat in fields],
                "id_map": {str(k): v for k, v in id_map.items()},
                "finish_reason": reason, "raw": text, "images": images,
            })
            print(f"  finish={reason} parsed={len(labels)}")
    if args.dry_run:
        print(f"dry run: {args.task} variant {args.variant}, {n_would} calls would be made, 0 API calls")
        return
    write_doc(args, zones, spec["max_per_group"], predictions, calls, [], {})


def run_round2(args, data):
    frames = load_json(data / "frames" / "frames.json")
    bounds = frames["bounds"]
    zones = load_json(data / "zones.geojson")["features"]
    if args.zones:
        keep = set(args.zones.split(","))
        zones = [z for z in zones if z["properties"]["zone_id"] in keep]
    by_id = {feat["properties"]["field_id"]: feat for feat in load_json(data / "fields.geojson")["features"]}
    by_zone = {}
    for feat in by_id.values():
        by_zone.setdefault(feat["properties"]["zone_id"], []).append(feat)
    truth = load_json(ROOT / "vision" / "eval" / "truth.json")["fields"]
    picked = example_fields(args.task, by_id, truth)
    uses_examples = (args.task == "state" and args.variant in ("E", "F")) or (args.task == "change" and args.variant == "F")
    excluded = sorted(picked.values()) if uses_examples else []
    print(f"examples {picked} excluded_from_scoring {excluded}")
    latest = frame_path(frames, LATEST, data)
    prev = frame_path(frames, PREV, data)
    mid = frame_path(frames, MID, data)
    scene = landscape()["scene"]
    example_paths = {}
    if args.task == "state" and args.variant in ("E", "F"):
        example_paths = save_state_examples(by_id, picked, latest, bounds)
    elif args.task == "change" and args.variant == "F":
        example_paths = save_change_examples(by_id, picked, prev, latest, bounds)
    if args.task == "state":
        prompt = scene + " " + STATE_D
        if args.variant == "F":
            prompt += " " + STATE_F_EXTRA
            prompt += " The last image is 2026-10-17. The image just before it is 2026-10-02, with the same numbers."
        if args.variant in ("E", "F"):
            prompt += " Caption images that say EXAMPLE are references. Do not answer for them."
        pattern = STATE_LINE
    else:
        prompt = scene + " " + CHANGE_D
        if args.variant == "F":
            prompt += (
                " Caption images that say EXAMPLE are references. Do not answer for them. "
                "The last two images are the fields to label: the earlier is Oct 10 colour-matched to Oct 17, "
                "and the later is Oct 17."
            )
        pattern = CHANGE_LINE
    predictions, calls, n_would = {}, [], 0
    stage1 = {}
    for zone in zones:
        zid = zone["properties"]["zone_id"]
        groups = group_fields(by_zone.get(zid, []), 8)
        for index, (bbox, fields) in enumerate(groups):
            n_would += 1
            tag = f"r2_{args.task}_{args.variant}_{zid}_{index}"
            dest = MARKS / f"{tag}.jpg"
            images = []
            if args.task == "state":
                for label in ("standing", "harvested"):
                    if label in example_paths:
                        images.append(example_paths[label])
                if args.variant == "F":
                    mid_dest = MARKS / f"{tag}_oct02.jpg"
                    _path, mid_map, _window = render_group(mid, bounds, bbox, fields, mid_dest)
                    images.append(mid_dest)
                path, id_map, _window = render_group(latest, bounds, bbox, fields, dest)
                images.append(path)
                if args.variant == "F" and mid_map != id_map:
                    sys.exit(f"STOP numbering differs on {tag}")
            else:
                matched, latest_img, window = matched_crops(prev, latest, bounds, bbox)
                matched_dest = MARKS / f"{tag}_prev_matched.jpg"
                matched_dest.parent.mkdir(parents=True, exist_ok=True)
                matched.save(matched_dest, quality=85)
                prev_marked, prev_map = draw_marks(matched.copy(), window, fields)
                prev_dest = MARKS / f"{tag}_prev.jpg"
                prev_marked.save(prev_dest, quality=85)
                latest_marked, id_map = draw_marks(latest_img, window, fields)
                latest_marked.save(dest, quality=85)
                if prev_map != id_map:
                    sys.exit(f"STOP numbering differs on {tag}")
                for label in ("changed", "unchanged"):
                    if label in example_paths:
                        images.append(example_paths[label])
                images.extend([prev_dest, dest])
            rels = [str(path.relative_to(ROOT)) for path in images]
            print(f"{tag} fields={len(fields)} images={len(images)} prompt_chars={len(prompt)}")
            if args.dry_run:
                if index == 0 and zone is zones[0]:
                    print(prompt[:280])
                    if args.task == "change":
                        sample = fields[0]
                        sample_dest = MARKS / f"r2_{args.task}_{args.variant}_verify_sample.jpg"
                        verify_field(sample["properties"]["field_id"], sample, prev, latest, bounds, sample_dest)
                        print(f"sample verification stitch {sample_dest.relative_to(ROOT)}")
                continue
            text, reason = ask_guarded(images, prompt, tag, 512)
            labels = parse_lines(text, pattern, id_map)
            predictions.update(labels)
            stage1.update(labels)
            calls.append({
                "stage": 1, "zone_id": zid, "group": index,
                "field_ids": [feat["properties"]["field_id"] for feat in fields],
                "id_map": {str(k): v for k, v in id_map.items()},
                "finish_reason": reason, "raw": text, "images": rels,
            })
            print(f"  finish={reason} parsed={len(labels)}")
    if args.task == "change" and args.variant in ("E", "F") and not args.dry_run:
        for fid, label in list(stage1.items()):
            if label != "changed":
                continue
            n_would += 1
            dest = MARKS / f"r2_change_{args.variant}_verify_{fid}.jpg"
            verify_field(fid, by_id[fid], prev, latest, bounds, dest)
            prompt_v = scene + " " + VERIFY_ASK
            text, reason = ask_guarded([dest], prompt_v, f"r2_change_{args.variant}_verify_{fid}", 64)
            answer = parse_yes_no(text)
            final = "changed" if answer == "yes" else ("unchanged" if answer == "no" else "unsure")
            predictions[fid] = final
            calls.append({
                "stage": 2, "field_id": fid, "stage1": "changed", "stage2": answer,
                "finish_reason": reason, "raw": text,
                "images": [str(dest.relative_to(ROOT))],
            })
            print(f"  verify {fid} stage2={answer} final={final} finish={reason}")
    if args.dry_run:
        print(f"dry run: {args.task} variant {args.variant}, {n_would} group calls prepared, 0 API calls")
        return
    write_doc(args, zones, 8, predictions, calls, excluded, picked if uses_examples else {}, stage1)


def write_doc(args, zones, max_per_group, predictions, calls, excluded, examples, stage1=None):
    out_path = EVAL / f"classify_{args.task}_{args.variant}.json"
    tuning = None
    if out_path.exists():
        old = json.loads(out_path.read_text())
        old_zones = set(old.get("zones") or [])
        new_zones = {z["properties"]["zone_id"] for z in zones}
        if old_zones and old_zones < new_zones:
            tuning = {
                "zones": old.get("zones"), "predictions": old.get("predictions"),
                "score": old.get("score"), "calls": old.get("calls"),
                "excluded_from_scoring": old.get("excluded_from_scoring"),
            }
    stats, skipped, excluded_present, focus = score_block(args.task, predictions, set(excluded))
    correct = round(stats["accuracy"] * stats["n"])
    print(f"accuracy {stats['accuracy']:.2f} ({correct} of {stats['n']}) unsure {stats['n_unsure_pred']} of {stats['n']}")
    if focus:
        print(f"changed precision {phrase(stats, focus, 'precision')} recall {phrase(stats, focus, 'recall')}")
    print(f"skipped not in truth: {len(skipped)} excluded examples: {excluded_present}")
    doc = {
        "task": args.task,
        "variant": args.variant,
        "round": 2 if args.variant in "DEF" else 1,
        "max_per_group": max_per_group,
        "zones": [z["properties"]["zone_id"] for z in zones],
        "model": MODEL,
        "predictions": predictions,
        "stage1_predictions": stage1,
        "calls": calls,
        "examples": examples,
        "excluded_from_scoring": excluded_present,
        "skipped_not_in_truth": skipped,
        "score": stats,
        "tuning": tuning,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc))
    print(f"wrote {out_path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--task", choices=["state", "change"], required=True)
    ap.add_argument("--variant", choices=list("ABCDEF"), required=True)
    ap.add_argument("--zones", default="", help="comma list; default every zone")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = ROOT / args.data
    extra = check_truth_geometry(data / "fields.geojson")
    if extra:
        print(f"fields not in truth.json (classified, not scored): {len(extra)} {', '.join(extra)}")
    if args.variant in "ABC":
        run_round1(args, data)
    else:
        if args.zones:
            asked = set(args.zones.split(","))
            if not asked <= set(DEV_ZONES):
                sys.exit("STOP round 2 --zones may only list dev zones Z01 and Z07; omit --zones for the full run")
        run_round2(args, data)


if __name__ == "__main__":
    main()
