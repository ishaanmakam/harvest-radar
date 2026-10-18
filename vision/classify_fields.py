"""Set-of-marks standing/harvested and change labels. No box annotations.

    python vision/classify_fields.py --task state --variant A --zones Z01,Z07 --dry-run
    python vision/classify_fields.py --task state --variant A --zones Z01,Z07
    python vision/classify_fields.py --task change --variant A

Variants (fixed before looking at held-out zones):
  A  the prompt below, 12 fields per group
  B  A plus "First look at the colour inside each outline, then answer."
  C  A's prompt, 8 fields per group
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perceptron_client  # noqa: E402
from eval.common import ROOT, SPEND_CAP, check_truth_geometry, frame_path, load_json  # noqa: E402
from landscape import landscape  # noqa: E402
from marks import crop_bounds, draw_marks, group_fields, upscale  # noqa: E402
from perceptron_client import MODEL, ask, spent_usd  # noqa: E402

EVAL = ROOT / "data" / "out" / "perceptron" / "eval"
MARKS = EVAL / "marks"
START_FILE = EVAL / "spend_start.json"
STATE_LINE = re.compile(r"^(\d+)\s*:\s*(standing|harvested|unsure)\s*$", re.I)
CHANGE_LINE = re.compile(r"^(\d+)\s*:\s*(changed|unchanged|unsure)\s*$", re.I)
PREV, LATEST = "2026-10-10", "2026-10-17"

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


def variant_spec(task, name):
    ask = STATE_ASK if task == "state" else CHANGE_ASK
    if name == "B":
        ask = ask.replace("Answer one line per number", LOOK + " Answer one line per number")
    return {"prompt": ask, "max_per_group": 8 if name == "C" else 12}


def guard():
    START_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not START_FILE.exists():
        START_FILE.write_text(json.dumps({"usd": round(spent_usd(), 5)}))
    start = json.loads(START_FILE.read_text())["usd"]
    risen = spent_usd() - start
    if risen > SPEND_CAP:
        sys.exit(f"STOP spend rose ${risen:.4f} above ${start:.5f} (cap ${SPEND_CAP:.2f})")
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


def render_group(frame, bounds, bbox, fields, dest):
    crop, window = crop_bounds(frame, bounds, bbox)
    crop, _scale = upscale(crop, 768)
    marked, id_map = draw_marks(crop, window, fields)
    dest.parent.mkdir(parents=True, exist_ok=True)
    marked.save(dest, quality=85)
    return dest, {int(k): v for k, v in id_map.items()}


def score_block(task, predictions):
    sys.path.insert(0, str(ROOT / "vision"))
    from eval.metrics import classification

    truth = load_json(ROOT / "vision" / "eval" / "truth.json")["fields"]
    skipped = sorted(fid for fid in predictions if fid not in truth)
    if task == "state":
        labelled = {fid: truth[fid]["state_oct17"] for fid in predictions if fid in truth}
        labels = ["standing", "harvested"]
        focus = None
    else:
        rename = {"yes": "changed", "no": "unchanged", "unsure": "unsure"}
        labelled = {fid: rename[truth[fid]["changed"]] for fid in predictions if fid in truth}
        labels = ["changed", "unchanged"]
        focus = "changed"
    stats = classification({fid: predictions[fid] for fid in labelled}, labelled, labels)
    return stats, skipped, focus


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--task", choices=["state", "change"], required=True)
    ap.add_argument("--variant", choices=["A", "B", "C"], required=True)
    ap.add_argument("--zones", default="", help="comma list; default every zone")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = ROOT / args.data
    spec = variant_spec(args.task, args.variant)
    extra = check_truth_geometry(data / "fields.geojson")
    if extra:
        print(f"fields not in truth.json (classified, not scored): {len(extra)}")
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

    predictions = {}
    calls = []
    n_would = 0
    for zone in zones:
        zid = zone["properties"]["zone_id"]
        groups = group_fields(by_zone.get(zid, []), spec["max_per_group"])
        for index, (bbox, fields) in enumerate(groups):
            n_would += 1
            tag = f"{args.task}_{args.variant}_{zid}_{index}"
            dest = MARKS / f"{tag}.jpg"
            path, id_map = render_group(latest, frames["bounds"], bbox, fields, dest)
            images = [str(path.relative_to(ROOT))]
            if args.task == "change":
                prev_dest = MARKS / f"{tag}_prev.jpg"
                prev_path, prev_map = render_group(prev, frames["bounds"], bbox, fields, prev_dest)
                if prev_map != id_map:
                    sys.exit(f"STOP numbering differs on {tag}")
                images = [str(prev_path.relative_to(ROOT)), images[0]]
            print(f"{tag} fields={len(fields)} images={len(images)} prompt_chars={len(prompt)}")
            if args.dry_run:
                if index == 0 and zone is zones[0]:
                    print(prompt[:240])
                continue
            guard()
            try:
                text, _cost = ask([ROOT / rel for rel in images], prompt, label=tag, max_tokens=512)
            except RuntimeError as exc:
                print(exc)
                sys.exit(1)
            guard()
            reason = perceptron_client.LAST_FINISH_REASON
            labels = parse_lines(text, pattern, id_map)
            predictions.update(labels)
            calls.append({
                "zone_id": zid,
                "group": index,
                "field_ids": [feat["properties"]["field_id"] for feat in fields],
                "id_map": {str(k): v for k, v in id_map.items()},
                "finish_reason": reason,
                "raw": text,
                "images": images,
            })
            print(f"  finish={reason} parsed={len(labels)}")

    if args.dry_run:
        print(f"dry run: {args.task} variant {args.variant}, {n_would} calls would be made, 0 API calls")
        return

    out_path = EVAL / f"classify_{args.task}_{args.variant}.json"
    tuning = None
    if out_path.exists():
        old = json.loads(out_path.read_text())
        old_zones = set(old.get("zones") or [])
        new_zones = {z["properties"]["zone_id"] for z in zones}
        if old_zones and old_zones < new_zones:
            tuning = {"zones": old.get("zones"), "predictions": old.get("predictions"), "score": old.get("score"), "calls": old.get("calls")}
    stats, skipped, focus = score_block(args.task, predictions)
    correct = round(stats["accuracy"] * stats["n"])
    print(f"accuracy {stats['accuracy']:.2f} ({correct} of {stats['n']}) unsure {stats['n_unsure_pred']} of {stats['n']}")
    if focus:
        print(f"changed precision {phrase(stats, focus, 'precision')} recall {phrase(stats, focus, 'recall')}")
    print(f"skipped not in truth: {len(skipped)}")
    doc = {
        "task": args.task,
        "variant": args.variant,
        "max_per_group": spec["max_per_group"],
        "zones": [z["properties"]["zone_id"] for z in zones],
        "model": MODEL,
        "predictions": predictions,
        "calls": calls,
        "skipped_not_in_truth": skipped,
        "score": stats,
        "tuning": tuning,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc))
    print(f"wrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
