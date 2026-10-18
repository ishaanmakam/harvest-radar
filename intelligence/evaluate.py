"""Score the harvest model against ground truth. Mock data only (that is the only place the truth is known).

Inputs:  status.json (model output), truth.json (written by scripts/make_mock_data.py)
Output:  evaluation.json in the data dir + a printed report

    python intelligence/evaluate.py --data data/mock

Metrics
  harvest_date       error in days for fields the model calls harvested / burned / sown
  predicted_harvest  error of the predicted-window midpoint, and how often the true date falls inside the window
  stage              exact and coarse accuracy (coarse folds growing/maturing/ready into "standing") + confusion matrix
  burn, sown         precision / recall / F1 (burn also gets the date error on true positives)
  peak_to_harvest    the regional lag the model learned vs the true median
"""
import argparse
import statistics
from datetime import date
from pathlib import Path

from common import d, load_region, read_json, s, write_json

STANDING = ("growing", "maturing", "ready")


def true_stage(t: dict, as_of: date) -> str:
    """What the model should say for a field given what really happened (mirrors harvest_model's stage rules)."""
    if t["crop"] != "rice":
        return "unknown"
    if t["burned"] and t["burn_date"] and d(t["burn_date"]) <= as_of:
        return "burned"
    if t["sown"]:
        return "sown"
    h = d(t["harvest_date"])
    if h < as_of:
        return "harvested"
    days_out = (h - as_of).days
    return "ready" if days_out <= 3 else ("maturing" if days_out <= 14 else "growing")


def coarse(stage: str) -> str:
    return "standing" if stage in STANDING else stage


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _date_stats(errs):
    if not errs:
        return {"n": 0}
    ab = sorted(abs(e) for e in errs)
    return {
        "n": len(errs),
        "mae_days": round(statistics.mean(ab), 2),
        "median_abs_days": statistics.median(ab),
        "p90_abs_days": ab[int(0.9 * (len(ab) - 1))],
        "bias_days": round(statistics.mean(errs), 2),
        "within_3d_pct": _pct(sum(x <= 3 for x in ab), len(ab)),
        "within_5d_pct": _pct(sum(x <= 5 for x in ab), len(ab)),
    }


def _prf(pred: set, truth: set):
    tp, fp, fn = len(pred & truth), len(pred - truth), len(truth - pred)
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and (p + r) else None
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision_pct": None if p is None else round(100 * p, 1),
            "recall_pct": None if r is None else round(100 * r, 1),
            "f1_pct": None if f1 is None else round(100 * f1, 1)}


def _days(delta):
    return round(delta.total_seconds() / 86400)


def evaluate(status_fields, truth, as_of: date) -> dict:
    """status_fields: status.json["fields"]; truth: truth.json["fields"] (keyed by field_id)."""
    recs = [r for r in status_fields if r["field_id"] in truth]
    harvest_errs, pred_errs, in_window, already = [], [], 0, 0
    confusion, exact, coarse_ok = {}, 0, 0
    pred_burn, true_burn, burn_date_errs = set(), set(), []
    pred_sown, true_sown, true_lags = set(), set(), []

    for r in recs:
        fid = r["field_id"]
        t = truth[fid]
        ts, ps = true_stage(t, as_of), r["stage"]
        confusion.setdefault(ts, {})
        confusion[ts][ps] = confusion[ts].get(ps, 0) + 1
        exact += ts == ps
        coarse_ok += coarse(ts) == coarse(ps)

        if t["crop"] == "rice":
            th = d(t["harvest_date"])
            if r.get("harvest_date"):
                harvest_errs.append((d(r["harvest_date"]) - th).days)
            elif r.get("predicted_harvest_start") and r.get("predicted_harvest_end"):
                a, b = d(r["predicted_harvest_start"]), d(r["predicted_harvest_end"])
                pred_errs.append(_days(a + (b - a) / 2 - th))
                in_window += a <= th <= b
                already += th < as_of
            if th < as_of and t.get("peak_date"):
                true_lags.append((th - d(t["peak_date"])).days)

        if t["burned"]:
            true_burn.add(fid)
        if ps == "burned":
            pred_burn.add(fid)
            if t["burned"] and r.get("burn_date") and t.get("burn_date"):
                burn_date_errs.append((d(r["burn_date"]) - d(t["burn_date"])).days)
        if t["sown"]:
            true_sown.add(fid)
        if ps == "sown":
            pred_sown.add(fid)

    n = len(recs)
    burn = _prf(pred_burn, true_burn)
    burn["date_mae_days"] = round(statistics.mean(abs(e) for e in burn_date_errs), 2) if burn_date_errs else None
    pred = _date_stats(pred_errs)
    pred.update({"in_window_pct": _pct(in_window, len(pred_errs)), "n_truth_already_harvested": already})
    return {
        "n_fields": n,
        "harvest_date": _date_stats(harvest_errs),
        "predicted_harvest": pred,
        "stage": {"n": n, "accuracy_exact_pct": _pct(exact, n), "accuracy_coarse_pct": _pct(coarse_ok, n),
                  "confusion": confusion},
        "burn": burn,
        "sown": _prf(pred_sown, true_sown),
        "truth_peak_to_harvest_days": statistics.median(true_lags) if true_lags else None,
    }


def print_report(m: dict):
    hd, ph, st, b, so = m["harvest_date"], m["predicted_harvest"], m["stage"], m["burn"], m["sown"]
    print(f"  fields scored: {m['n_fields']}")
    if hd["n"]:
        print(f"  harvest date (n={hd['n']}): MAE {hd['mae_days']} d, median {hd['median_abs_days']} d, "
              f"p90 {hd['p90_abs_days']} d, bias {hd['bias_days']:+} d, within 3 d {hd['within_3d_pct']}%")
    if ph["n"]:
        print(f"  predicted harvest (n={ph['n']}): midpoint MAE {ph['mae_days']} d, bias {ph['bias_days']:+} d, "
              f"true date inside window {ph['in_window_pct']}%, already harvested in truth {ph['n_truth_already_harvested']}")
    print(f"  stage accuracy: exact {st['accuracy_exact_pct']}%, coarse {st['accuracy_coarse_pct']}%")
    for ts in sorted(st["confusion"]):
        row = ", ".join(f"{ps}={k}" for ps, k in sorted(st["confusion"][ts].items(), key=lambda x: -x[1]))
        print(f"    truth {ts:9s} -> {row}")
    print(f"  burn: precision {b['precision_pct']}%, recall {b['recall_pct']}%, F1 {b['f1_pct']}% "
          f"(tp {b['tp']}, fp {b['fp']}, fn {b['fn']}), burn-date MAE {b['date_mae_days']} d")
    print(f"  sown: precision {so['precision_pct']}%, recall {so['recall_pct']}% (tp {so['tp']}, fp {so['fp']}, fn {so['fn']})")
    if "peak_to_harvest_days" in m:
        p = m["peak_to_harvest_days"]
        print(f"  peak->harvest lag: model {p['model']} d vs truth median {p['truth_median']} d")


def run(data_dir: Path) -> dict:
    region = load_region()
    as_of = d(region["as_of"])
    status = read_json(data_dir, "status.json")
    truth = read_json(data_dir, "truth.json")
    m = evaluate(status["fields"], truth["fields"], as_of)
    m["as_of"] = s(as_of)
    m["peak_to_harvest_days"] = {"model": status["peak_to_harvest_days"],
                                 "truth_median": m.pop("truth_peak_to_harvest_days")}
    print_report(m)
    write_json(data_dir, "evaluation.json", m)
    return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/mock")
    run(Path(ap.parse_args().data))
