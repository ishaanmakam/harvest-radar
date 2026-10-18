"""Score the fixed tests. Pass bars and the dev/held-out split are not adjusted here.

    python vision/eval/evaluate.py --summarize-dev
    python vision/eval/evaluate.py
"""
import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import (  # noqa: E402
    DEV_ZONES, HELD_ZONES, ROOT, area_ha, check_truth_geometry, load_json,
)
from eval.metrics import classification, polygon_match  # noqa: E402

EVAL = ROOT / "data" / "out" / "perceptron" / "eval"
CHOSEN = EVAL / "chosen.json"
STATE_LABELS = ["standing", "harvested"]
CHANGE_LABELS = ["changed", "unchanged"]
RENAME = {"yes": "changed", "no": "unchanged", "unsure": "unsure"}


def phrase(stats, label, kind):
    tp = stats["confusion"][label].get(label, 0)
    if kind == "recall":
        support = sum(stats["confusion"][label].values())
        return f"{stats['per_label'][label]['recall']:.2f} ({tp} of {support})"
    predicted = sum(row.get(label, 0) for row in stats["confusion"].values())
    return f"{stats['per_label'][label]['precision']:.2f} ({tp} of {predicted})"


def class_view(predictions, truth_map, labels, focus=None):
    usable = {fid: truth_map[fid] for fid in predictions if fid in truth_map}
    skipped = sorted(fid for fid in predictions if fid not in truth_map)
    stats = classification({fid: predictions[fid] for fid in usable}, usable, labels)
    correct = sum(1 for fid, label in usable.items() if label != "unsure" and predictions.get(fid) == label)
    stats["accuracy_text"] = f"{stats['accuracy']:.2f} ({correct} of {stats['n']})"
    stats["unsure_text"] = f"{stats['n_unsure_pred']} of {stats['n']}"
    stats["n_skipped"] = len(skipped)
    if focus and focus in stats["per_label"]:
        stats["precision_text"] = phrase(stats, focus, "precision")
        stats["recall_text"] = phrase(stats, focus, "recall")
    return stats


def state_pass(stats):
    unsure = (stats["n_unsure_pred"] / stats["n"]) if stats["n"] else 1
    return stats["accuracy"] >= 0.90 and unsure <= 0.10 and stats["n"] > 0


def change_pass(stats):
    row = stats["per_label"]["changed"]
    support = sum(stats["confusion"]["changed"].values())
    return row["precision"] >= 0.85 and row["recall"] >= 0.80 and support > 0


def field_zones(fields_path):
    zones = {}
    for feat in load_json(fields_path)["features"]:
        zones[feat["properties"]["field_id"]] = feat["properties"]["zone_id"]
    return zones


def truth_maps():
    truth = load_json(ROOT / "vision" / "eval" / "truth.json")["fields"]
    state = {fid: row["state_oct17"] for fid, row in truth.items()}
    change = {fid: RENAME[row["changed"]] for fid, row in truth.items()}
    return state, change


def subset(predictions, zones, keep):
    return {fid: label for fid, label in predictions.items() if zones.get(fid) in keep}


def load_classify(task, variant):
    path = EVAL / f"classify_{task}_{variant}.json"
    if not path.exists():
        return None
    return load_json(path)


def dev_score_of(doc):
    if doc.get("tuning") and doc["tuning"].get("score"):
        return doc["tuning"]["score"]
    return doc.get("score") or {}


def iou(a, b):
    inter = a.intersection(b).area
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union else 0.0


def dup_gt(geoms):
    n = 0
    for i, geom in enumerate(geoms):
        if any(iou(geom, other) > 0.5 for other in geoms[:i]):
            n += 1
    return n


def outline_view(features, ref_feats):
    preds = []
    for feat in features:
        geom = shape(feat["geometry"])
        if area_ha(geom) >= 8:
            preds.append(geom)
    refs = []
    for feat in ref_feats:
        geom = shape(feat["geometry"])
        if area_ha(geom) >= 8:
            refs.append(geom)
    matched = polygon_match(preds, refs, iou_min=0.5, min_area_ha=0)
    hits = len(matched["matched_pairs"])
    matched["recall_text"] = f"{matched['recall']:.2f} ({hits} of {matched['n_ref']})"
    matched["precision_text"] = f"{matched['precision']:.2f} ({hits} of {matched['n_pred']})"
    matched["duplicates_gt"] = dup_gt(preds)
    return matched


def outline_pass(stats, all_stop):
    return (stats["recall"] >= 0.75 and stats["precision"] >= 0.80 and stats["duplicates_gt"] == 0
            and all_stop and stats["n_ref"] > 0)


def features_in(features, zone_ids):
    return [feat for feat in features if feat["properties"].get("zone_id") in zone_ids]


def refs_in(fields, zone_ids):
    return [f for f in fields if f["properties"]["zone_id"] in zone_ids]


def ndvi_predictions(zones):
    series = load_json(ROOT / "data" / "out" / "timeseries.json")
    dates = series["dates"]
    i10, i17 = dates.index("2026-10-10"), dates.index("2026-10-17")
    state, change = {}, {}
    for fid, row in series["fields"].items():
        if zones.get(fid) is None and fid not in zones:
            continue
        values = row["ndvi"]
        latest, prev = values[i17], values[i10]
        state[fid] = "unsure" if latest is None else ("harvested" if latest <= 0.25 else "standing")
        if latest is None or prev is None:
            change[fid] = "unsure"
        else:
            change[fid] = "changed" if (prev - latest) >= 0.15 else "unchanged"
    return state, change


def summarize_dev():
    """Print dev-zone scores only. Used to pick a variant before any held-out call."""
    zones = field_zones(ROOT / "data" / "out" / "fields.geojson")
    state_truth, change_truth = truth_maps()
    print("state variants (dev zones only)")
    for name in "ABC":
        doc = load_classify("state", name)
        if not doc:
            print(f"  {name} missing")
            continue
        preds = subset(doc["predictions"], zones, DEV_ZONES)
        stats = class_view(preds, state_truth, STATE_LABELS)
        print(f"  {name} accuracy {stats['accuracy_text']} unsure {stats['unsure_text']} pass {state_pass(stats)}")
    print("change variants (dev zones only)")
    for name in "ABC":
        doc = load_classify("change", name)
        if not doc:
            print(f"  {name} missing")
            continue
        preds = subset(doc["predictions"], zones, DEV_ZONES)
        stats = class_view(preds, change_truth, CHANGE_LABELS, "changed")
        print(f"  {name} precision {stats['precision_text']} recall {stats['recall_text']} pass {change_pass(stats)}")
    fields = load_json(ROOT / "data" / "out" / "fields.geojson")["features"]
    print("outline variants (seeds in dev zones only)")
    for name in "ABC":
        path = EVAL / f"outlines_{name}.geojson"
        if not path.exists():
            print(f"  {name} missing")
            continue
        feats = features_in(load_json(path)["features"], DEV_ZONES)
        stats = outline_view(feats, refs_in(fields, DEV_ZONES))
        calls = EVAL / f"outlines_{name}_calls.json"
        stop = True
        if calls.exists():
            stop = all(c["finish_reason"] == "stop" for c in load_json(calls))
        print(f"  {name} recall {stats['recall_text']} precision {stats['precision_text']} "
              f"dup {stats['duplicates_gt']} stop {stop}")


def board_row(test, method, variant, dev, held, overall, bar, passed, note=""):
    return {
        "test": test, "method": method, "variant": variant,
        "metrics_dev": dev, "metrics_heldout": held, "metrics_all": overall,
        "bar": bar, "pass": passed, "note": note,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summarize-dev", action="store_true")
    args = ap.parse_args()
    extra = check_truth_geometry(ROOT / "data" / "out" / "fields.geojson")
    print(f"fields skipped (not in truth.json): {len(extra)}")
    if args.summarize_dev:
        summarize_dev()
        return
    if not CHOSEN.exists():
        sys.exit("STOP chosen.json missing; pick variants from --summarize-dev before the held-out run")
    chosen = load_json(CHOSEN)
    zones = field_zones(ROOT / "data" / "out" / "fields.geojson")
    state_truth, change_truth = truth_maps()
    fields = load_json(ROOT / "data" / "out" / "fields.geojson")["features"]
    rows = []

    state_doc = load_classify("state", chosen["state"])
    change_doc = load_classify("change", chosen["change"])
    if state_doc is None or change_doc is None:
        sys.exit("STOP classify output missing for the chosen variant")

    def splits(doc, truth, labels, focus, passed):
        views = {}
        for key, keep in (("dev", DEV_ZONES), ("heldout", HELD_ZONES), ("all", DEV_ZONES + HELD_ZONES)):
            views[key] = class_view(subset(doc["predictions"], zones, keep), truth, labels, focus)
        return views

    state_views = splits(state_doc, state_truth, STATE_LABELS, None, state_pass)
    change_views = splits(change_doc, change_truth, CHANGE_LABELS, "changed", change_pass)
    state_ok = state_pass(state_views["heldout"])
    change_ok = change_pass(change_views["heldout"])
    rows.append(board_row(
        "standing_vs_harvested", "set_of_marks", chosen["state"],
        state_views["dev"], state_views["heldout"], state_views["all"],
        "held-out accuracy >= 0.90 and predicted unsure <= 10% of scored fields",
        state_ok,
    ))
    rows.append(board_row(
        "change", "set_of_marks", chosen["change"],
        change_views["dev"], change_views["heldout"], change_views["all"],
        "held-out changed precision >= 0.85 and recall >= 0.80",
        change_ok,
        "Small sample: 17 changed fields, so each miss moves recall by about 6 points.",
    ))

    outline_path = EVAL / f"outlines_{chosen['outline']}.geojson"
    call_path = EVAL / f"outlines_{chosen['outline']}_calls.json"
    if not outline_path.exists():
        empty = {"recall": 0, "precision": 0, "n_ref": 0, "n_pred": 0, "matched_pairs": [], "duplicates": 0,
                 "duplicates_gt": 0, "recall_text": "0.00 (0 of 0)", "precision_text": "0.00 (0 of 0)"}
        rows.append(board_row("outlines_full", "seed_then_trace", chosen["outline"], empty, empty, empty,
                              "recall >= 0.75 and precision >= 0.80, duplicates IoU > 0.5 == 0, every trace finish_reason stop",
                              False, "no outline file"))
        rows.append(board_row("outlines_tile_z07", "seed_then_trace", chosen["outline"], empty, empty, empty,
                              "same bar on zone Z07", False, "no outline file"))
        outline_ok = False
        features = []
        all_stop = False
    else:
        features = load_json(outline_path)["features"]
        calls = load_json(call_path) if call_path.exists() else []
        all_stop = bool(calls) and all(c["finish_reason"] == "stop" for c in calls)
        z07_calls = [c for c in calls if c.get("zone_id") == "Z07"]
        z07_stop = bool(z07_calls) and all(c["finish_reason"] == "stop" for c in z07_calls)
        full = {
            "dev": outline_view(features_in(features, DEV_ZONES), refs_in(fields, DEV_ZONES)),
            "heldout": outline_view(features_in(features, HELD_ZONES), refs_in(fields, HELD_ZONES)),
            "all": outline_view(features, fields),
        }
        for part in full.values():
            part["all_trace_stop"] = all_stop
        outline_ok = outline_pass(full["heldout"], all_stop)
        rows.append(board_row(
            "outlines_full", "seed_then_trace", chosen["outline"],
            full["dev"], full["heldout"], full["all"],
            "recall >= 0.75 and precision >= 0.80 on fields >= 8 ha, IoU >= 0.5; duplicates with IoU > 0.5 == 0; every trace finish_reason stop",
            outline_pass(full["all"], all_stop),
        ))
        z07 = outline_view(features_in(features, ("Z07",)), refs_in(fields, ("Z07",)))
        z07["all_trace_stop"] = z07_stop
        rows.append(board_row(
            "outlines_tile_z07", "seed_then_trace", chosen["outline"],
            z07, None, z07,
            "same outline bar restricted to zone Z07",
            outline_pass(z07, z07_stop),
        ))
        # Wiring follows the held-out full-frame bar, per the task.
        outline_ok = outline_pass(full["heldout"], all_stop)

    ndvi_state, ndvi_change = ndvi_predictions(zones)
    ndvi_state_views = {
        key: class_view(subset(ndvi_state, zones, keep), state_truth, STATE_LABELS)
        for key, keep in (("dev", DEV_ZONES), ("heldout", HELD_ZONES), ("all", DEV_ZONES + HELD_ZONES))
    }
    ndvi_change_views = {
        key: class_view(subset(ndvi_change, zones, keep), change_truth, CHANGE_LABELS, "changed")
        for key, keep in (("dev", DEV_ZONES), ("heldout", HELD_ZONES), ("all", DEV_ZONES + HELD_ZONES))
    }
    rows.append(board_row("ndvi_state", "ndvi_rule", "oct17<=0.25", ndvi_state_views["dev"], ndvi_state_views["heldout"],
                          ndvi_state_views["all"], "comparison only, not a pass bar", None))
    rows.append(board_row("ndvi_change", "ndvi_rule", "drop>=0.15", ndvi_change_views["dev"], ndvi_change_views["heldout"],
                          ndvi_change_views["all"], "comparison only, not a pass bar", None))

    insights_path = ROOT / "data" / "out" / "insights.json"
    zone_pass = False
    if insights_path.exists():
        items = load_json(insights_path).get("items") or []
        zone_pass = bool(items) and all(not i.get("gate", {}).get("flood") and not i.get("gate", {}).get("stress") for i in items)
    rows.append(board_row(
        "zone_summaries", "narration", "images",
        {"n": 2}, {"n": 7}, {"n": 9, "no_false_flood_or_stress": zone_pass},
        "no false flood or damage; keep the existing narration",
        zone_pass,
    ))

    EVAL.mkdir(parents=True, exist_ok=True)
    (EVAL / "scoreboard.json").write_text(json.dumps(rows, indent=2))
    print(f"wrote {EVAL.relative_to(ROOT) / 'scoreboard.json'}")
    for row in rows:
        print(f"  {row['test']} variant {row['variant']} pass {row['pass']}")

    if outline_ok:
        write_perceptron_fields(features)
    apply = {
        "state_pass": state_ok,
        "change_pass": change_ok,
        "outline_pass": outline_ok,
        "state_variant": chosen["state"],
        "change_variant": chosen["change"],
        "outline_variant": chosen["outline"],
        "n_skipped": len(extra),
    }
    (EVAL / "apply.json").write_text(json.dumps(apply, indent=2))
    print("apply", json.dumps(apply))


def write_perceptron_fields(features):
    out = []
    for index, feat in enumerate(features, start=1):
        zone = feat["properties"].get("zone_id")
        area = feat["properties"].get("area_ha") or 0
        if not zone or area <= 0:
            continue
        out.append({
            "type": "Feature",
            "properties": {
                "field_id": f"P{index:04d}",
                "zone_id": zone,
                "area_ha": area,
                "crop": "unknown",
                "source": "perceptron",
            },
            "geometry": feat["geometry"],
        })
    dest = ROOT / "data" / "out" / "perceptron" / "fields_perceptron.geojson"
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": out}))
    print(f"wrote {dest.relative_to(ROOT)} ({len(out)} fields)")


if __name__ == "__main__":
    main()
