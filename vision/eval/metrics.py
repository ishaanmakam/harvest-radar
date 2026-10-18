"""Scoring for field outlines and set-of-marks labels. No API calls.

Areas use an equal-area approximation: deg^2 * (111320^2 * cos(mean latitude)).
"""
import math

M_PER_DEG = 111320.0


def _as_geom(poly):
    from shapely.geometry import Polygon, shape

    if hasattr(poly, "geom_type"):
        geom = poly
    elif isinstance(poly, dict):
        geom = shape(poly)
    else:
        geom = Polygon(poly)
    if geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def _mean_lat(geoms):
    lats = [g.centroid.y for g in geoms if not g.is_empty]
    if not lats:
        return 0.0
    return sum(lats) / len(lats)


def _area_ha(geom, mean_lat):
    metres2 = geom.area * (M_PER_DEG ** 2) * math.cos(math.radians(mean_lat))
    return metres2 / 10000.0


def _iou(a, b):
    inter = a.intersection(b).area
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    if union <= 0:
        return 0.0
    return inter / union


def polygon_match(pred_polys, ref_polys, iou_min=0.5, min_area_ha=8):
    """Greedy one-to-one IoU match. Polygons under min_area_ha are left out.

    duplicates counts a later prediction that overlaps an earlier one at IoU >= iou_min.
    """
    pred = [(i, _as_geom(p)) for i, p in enumerate(pred_polys)]
    ref = [(i, _as_geom(p)) for i, p in enumerate(ref_polys)]
    mean_lat = _mean_lat([g for _, g in pred + ref])
    pred = [(i, g) for i, g in pred if _area_ha(g, mean_lat) >= min_area_ha]
    ref = [(i, g) for i, g in ref if _area_ha(g, mean_lat) >= min_area_ha]

    candidates = []
    for pi, (_, pg) in enumerate(pred):
        for ri, (_, rg) in enumerate(ref):
            score = _iou(pg, rg)
            if score >= iou_min:
                candidates.append((score, pi, ri))
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    used_p, used_r = set(), set()
    matched_pairs = []
    for score, pi, ri in candidates:
        if pi in used_p or ri in used_r:
            continue
        used_p.add(pi)
        used_r.add(ri)
        matched_pairs.append({"pred": pred[pi][0], "ref": ref[ri][0], "iou": round(score, 6)})

    duplicates = 0
    for i, (_, geom) in enumerate(pred):
        if any(_iou(geom, other) >= iou_min for _, other in pred[:i]):
            duplicates += 1

    n_ref, n_pred = len(ref), len(pred)
    n_hit = len(matched_pairs)
    return {
        "recall": (n_hit / n_ref) if n_ref else 1.0,
        "precision": (n_hit / n_pred) if n_pred else 1.0,
        "n_ref": n_ref,
        "n_pred": n_pred,
        "matched_pairs": matched_pairs,
        "duplicates": duplicates,
    }


def classification(pred, truth, labels):
    """Score labels. Truth 'unsure' is excluded. Predicted 'unsure' counts as wrong."""
    unsure_truth_ids = [i for i, label in truth.items() if label == "unsure"]
    scored = [i for i, label in truth.items() if label != "unsure"]
    n = len(scored)
    n_unsure_pred = sum(1 for i in scored if pred.get(i) == "unsure")
    correct = sum(1 for i in scored if pred.get(i) == truth[i])
    columns = list(labels) + ["unsure"]
    confusion = {label: {col: 0 for col in columns} for label in labels}
    for i in scored:
        actual = truth[i]
        guess = pred.get(i, "unsure")
        if actual not in confusion:
            continue
        if guess not in confusion[actual]:
            confusion[actual][guess] = 0
        confusion[actual][guess] += 1
    per_label = {}
    for label in labels:
        tp = sum(1 for i in scored if truth[i] == label and pred.get(i) == label)
        fp = sum(1 for i in scored if pred.get(i) == label and truth[i] != label)
        fn = sum(1 for i in scored if truth[i] == label and pred.get(i) != label)
        per_label[label] = {
            "precision": tp / (tp + fp) if (tp + fp) else 0.0,
            "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        }
    return {
        "accuracy": (correct / n) if n else 1.0,
        "per_label": per_label,
        "confusion": confusion,
        "n": n,
        "n_unsure_pred": n_unsure_pred,
        "n_unsure_truth": len(unsure_truth_ids),
    }


def _main():
    from shapely.geometry import box

    same = box(0, 0, 0.01, 0.01)
    shifted = box(0.005, 0, 0.015, 0.01)
    overlap = box(0.005, 0, 0.025, 0.02)
    base = box(0, 0, 0.02, 0.02)
    tiny = box(0, 0, 0.0001, 0.0001)

    perfect = polygon_match([same], [same])
    assert perfect["recall"] == 1.0 and perfect["precision"] == 1.0
    assert perfect["n_ref"] == 1 and perfect["n_pred"] == 1
    assert perfect["duplicates"] == 0 and len(perfect["matched_pairs"]) == 1
    assert perfect["matched_pairs"][0]["iou"] == 1.0

    missed = polygon_match([shifted], [same])
    assert missed["recall"] == 0.0 and missed["precision"] == 0.0 and missed["matched_pairs"] == []

    partial = polygon_match([overlap], [base])
    assert partial["recall"] == 1.0 and abs(partial["matched_pairs"][0]["iou"] - 0.6) < 1e-6

    dup = polygon_match([same, same], [same])
    assert dup["precision"] == 0.5 and dup["recall"] == 1.0 and dup["duplicates"] == 1 and dup["n_pred"] == 2

    filtered = polygon_match([tiny, same], [tiny, same])
    assert filtered["n_ref"] == 1 and filtered["n_pred"] == 1 and filtered["recall"] == 1.0

    labels = ["standing", "harvested"]
    both = {"a": "standing", "b": "harvested"}
    good = classification(both, both, labels)
    assert good["accuracy"] == 1.0 and good["n"] == 2
    assert good["n_unsure_pred"] == 0 and good["n_unsure_truth"] == 0
    assert good["per_label"]["standing"] == {"precision": 1.0, "recall": 1.0}
    assert good["per_label"]["harvested"] == {"precision": 1.0, "recall": 1.0}

    truth = {"a": "standing", "b": "harvested", "c": "standing", "d": "harvested"}
    half = {"a": "standing", "b": "harvested", "c": "harvested", "d": "standing"}
    scored = classification(half, truth, labels)
    assert scored["accuracy"] == 0.5 and scored["n"] == 4
    assert scored["confusion"]["standing"]["harvested"] == 1
    assert scored["confusion"]["harvested"]["standing"] == 1

    truth_u = {"a": "standing", "b": "harvested", "c": "standing", "d": "unsure"}
    pred_u = {"a": "standing", "b": "standing", "c": "unsure", "d": "harvested"}
    mixed = classification(pred_u, truth_u, labels)
    assert mixed["n"] == 3 and mixed["n_unsure_truth"] == 1 and mixed["n_unsure_pred"] == 1
    assert mixed["accuracy"] == 1 / 3
    assert mixed["per_label"]["standing"]["precision"] == 0.5
    assert mixed["per_label"]["standing"]["recall"] == 0.5
    assert mixed["per_label"]["harvested"]["recall"] == 0.0
    print("metrics ok")


if __name__ == "__main__":
    _main()
