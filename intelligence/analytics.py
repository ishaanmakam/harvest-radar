"""Analytics layer: discrete models over the same season files, written to analytics.json.

    python intelligence/analytics.py --data data/mock

Each module is a pure function analyze(data, config, prior) -> {"key", "fields", "landscape", "zones", "assumptions",
"method"}. Modules run in a fixed order so later ones can use earlier results (prior); a module that raises, or is not
installed, is recorded under "failed" and skipped, so one broken model never costs the others. analytics.json is
additive: nothing in the spec's plans.json / route.json / deliveries.json depends on it. The voice tools and the morning
words read it when it is present (farm_outlook below), and the map may.

Layout of analytics.json:
  as_of, source, modules (the keys that ran), failed {key: reason}, timings_s {key: seconds},
  method {key: paragraph}, assumptions {key: {name: text}},
  landscape {key: {...}}, zones {zone_id: {key: {...}}}, fields {field_id: {key: {...}}}
"""
import argparse
from datetime import date
import importlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import forecast_models as fm  # noqa: E402
from common import ROOT, load_region, read_json, season_as_of, write_json  # noqa: E402

# (module file, key) in dependency order: coverage and phenology feed nothing but are cheap; yield and workability feed
# economics; plans and route feed the landscape table and the officer's drive.
MODULES = [
    ("observation_quality", "coverage"),
    ("phenology", "phenology"),
    ("yield_model", "yield"),
    ("water_balance", "water"),
    ("anomaly", "anomaly"),
    ("events", "events"),
    ("workability", "workability"),
    ("harvest_economics", "economics"),
    ("route_optimizer", "route"),
    ("landscape_stats", "landscape"),
]


def read_optional(data_dir, name):
    """A season file that may not exist yet (read_json treats a None default as required)."""
    p = Path(data_dir) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_data(data_dir: Path):
    """Everything a module may want, with None for files the season does not have yet."""
    data_dir = Path(data_dir)
    chcs = None
    try:
        chcs = json.loads((ROOT / "config" / "chcs.json").read_text(encoding="utf-8")).get("chcs")
    except (OSError, ValueError):
        pass
    weather = read_json(data_dir, "weather.json", default={})
    ensemble = fm.load_ensemble_doc(data_dir)                      # tolerant shared reader (None when absent or malformed)
    as_of = None
    try:
        as_of = season_as_of(data_dir)
    except (TypeError, ValueError, KeyError):
        pass
    if ensemble and fm.is_stale(ensemble, as_of):
        print(f"  WARN weather_ensemble.json fetched {ensemble.get('fetched_at')} is stale for {as_of}: analytics use the pipeline forecast")
        ensemble = None
    overlay_ensemble(weather, ensemble)
    return {
        "fields": read_json(data_dir, "fields.geojson"),
        "zones": read_json(data_dir, "zones.geojson", default={"type": "FeatureCollection", "features": []}),
        "timeseries": read_json(data_dir, "timeseries.json"),
        "status": read_json(data_dir, "status.json"),
        "weather": weather,
        "ensemble": ensemble,
        "fires": read_json(data_dir, "fires.json", default={"detections": []}),
        "insights": read_optional(data_dir, "insights.json"),
        "plans": read_optional(data_dir, "plans.json"),
        "route": read_optional(data_dir, "route.json"),
        "truth": read_optional(data_dir, "truth.json"),
        "usda": read_optional(data_dir, "usda_crop_progress.json"),
        "yield_model": read_optional(data_dir, "yield_model.json"),     # the county model (yield_train.py), optional
        "field_env": read_optional(data_dir, "field_env.json"),         # soil and terrain per field (field_env.py), optional
        "chcs": chcs,
    }


def overlay_ensemble(weather, ensemble):
    """When the multi-model ensemble exists, the analytics read its median rain and its share of models calling a
    shower as the forecast's amount and probability, day by day where the dates overlap. The pipeline's single-model
    values stay beside them as precip_mm_single_model / precip_prob_single_model. Temperatures are left alone."""
    if not ensemble or not isinstance(weather, dict):
        return False
    fc = weather.get("forecast") or {}
    dates = list(fc.get("dates") or [])
    if not dates:
        return False
    by_date = {d.get("date"): d for d in ensemble.get("days", []) if isinstance(d, dict)}
    hit = False
    mm = list(fc.get("precip_mm") or [None] * len(dates))
    prob = list(fc.get("precip_prob") or [None] * len(dates))
    mm_single, prob_single = list(mm), list(prob)
    n_models = [0] * len(dates)
    for i, dt in enumerate(dates):
        day = by_date.get(str(dt)[:10])
        n = (day or {}).get("n_precip", (day or {}).get("n_models"))
        n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) else 0
        if not day or n < fm.MIN_MODELS:
            continue                                  # one or two opinions are not an ensemble: keep the feed's value
        med, share = day.get("precip_median_mm"), day.get("p_rain_5mm")
        med = float(med) if isinstance(med, (int, float)) and not isinstance(med, bool) and math.isfinite(med) else None
        share = float(share) if isinstance(share, (int, float)) and not isinstance(share, bool) and math.isfinite(share) and 0 <= share <= 1 else None
        if med is None and share is None:
            continue
        hit = True
        n_models[i] = n
        if med is not None and i < len(mm):
            mm[i] = med
        if share is not None and i < len(prob):
            prob[i] = round(100 * share)
    if not hit:
        return False
    fc["precip_mm_single_model"], fc["precip_prob_single_model"] = mm_single, prob_single
    fc["precip_mm"], fc["precip_prob"], fc["precip_n_models"] = mm, prob, n_models
    n = len(ensemble.get("models") or [])
    fc["source"] = f"{fc.get('source') or 'pipeline'} + multi-model ensemble ({n} models)"
    weather["forecast"] = fc
    return True


def clean(obj):
    """JSON hygiene: numpy scalars to Python numbers, NaN/inf to None, tuples to lists, date-like to ISO."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):                    # numpy float64 is a float subclass: cast to a plain float
        return None if (math.isnan(obj) or math.isinf(obj)) else float(obj)
    if hasattr(obj, "tolist"):                    # numpy array (checked before item: arrays have item too)
        return clean(obj.tolist())
    if hasattr(obj, "item"):                      # numpy scalar
        try:
            return clean(obj.item())
        except (TypeError, ValueError):
            return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def run_modules(data, config, modules=MODULES):
    """Run every module in order; returns (results by key, failed by key, timings by key)."""
    prior, failed, timings = {}, {}, {}
    for mod_name, key in modules:
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            failed[key] = f"module not available: {e}"
            continue
        try:
            out = clean(mod.analyze(data, config, prior))
            if not isinstance(out, dict):
                raise TypeError(f"analyze() returned {type(out).__name__}, not a dict")
            out.setdefault("key", key)
            out.setdefault("fields", {})
            out.setdefault("landscape", {})
            out.setdefault("zones", {})
            json.dumps(out)                       # must serialise, now, not at write time
            prior[key] = out
        except Exception as e:  # one broken model never costs the others
            failed[key] = f"{type(e).__name__}: {e}"
        timings[key] = round(time.perf_counter() - t0, 2)
    return prior, failed, timings


def assemble(results, failed, timings, data, config):
    fields = {}
    for feat in (data.get("fields") or {}).get("features", []):
        fields[feat["properties"]["field_id"]] = {}
    zones = {}
    for feat in (data.get("zones") or {}).get("features", []):
        zones[feat["properties"]["zone_id"]] = {}
    for key, out in results.items():
        for fid, v in (out.get("fields") or {}).items():
            fields.setdefault(fid, {})[key] = v
        for zid, v in (out.get("zones") or {}).items():
            zones.setdefault(zid, {})[key] = v
    status = data.get("status") or {}
    ens = data.get("ensemble") or {}
    fc_src = (data.get("weather") or {}).get("forecast", {}).get("source") or ""
    overlaid = bool(ens) and "ensemble" in fc_src
    forecast_source = (f"multi-model ensemble ({len(ens.get('models') or [])} models) over the pipeline forecast"
                       if overlaid else "pipeline forecast")
    forecast_note = ((f"Rain amounts and probabilities in the analytics are the ensemble median and the share of models at "
                      f"{fm.SHOWER_MM:.0f} mm or more, on days at least {fm.MIN_MODELS} models reach (precip_n_models in the forecast block); "
                      f"other days keep the pipeline forecast. The plan's rain line follows the pipeline forecast and quotes the agreement.")
                     if overlaid else "The analytics run on the pipeline forecast (weather.json).")
    return {
        "as_of": status.get("as_of") or config.get("as_of"),
        "source": "intelligence/analytics.py",
        "forecast_source": forecast_source,
        "forecast_note": forecast_note,
        "modules": list(results),
        "failed": failed,
        "timings_s": timings,
        "method": {k: v.get("method") for k, v in results.items()},
        "assumptions": {k: v.get("assumptions") for k, v in results.items()},
        "landscape": {k: v.get("landscape") for k, v in results.items()},
        "zones": zones,
        "fields": fields,
    }


def run(data_dir, modules=MODULES, write=True):
    data_dir = Path(data_dir)
    data = load_data(data_dir)
    config = load_region()
    config = {**config, "as_of": season_as_of(data_dir, config).isoformat()}   # run.json (live or replay) wins
    results, failed, timings = run_modules(data, config, modules)
    doc = assemble(results, failed, timings, data, config)
    if write:
        write_json(data_dir, "analytics.json", doc)
    ran = ", ".join(results) or "nothing"
    print(f"  analytics: {ran}" + (f" | failed: {failed}" if failed else "") + f" -> {data_dir / 'analytics.json'}")
    return doc


# ---------------------------------------------------------------- words for one farm
def _pct(x):
    return None if x is None else f"{round(100 * x):d}%"


def farm_outlook(doc, farm_id):
    """Short plain lines about one farm from analytics.json, for the voice tools and the morning words.
    Only says what the modules produced; an empty list when the farm has nothing."""
    f = (doc or {}).get("fields", {}).get(farm_id) or {}
    lines = []
    y = f.get("yield") or {}
    if y.get("index") is not None:
        est = y.get("estimate") or {}
        bu = est.get("bu_per_acre")
        band = y.get("band")
        cal = y.get("bu_per_acre_calibrated")
        if cal is not None:
            txt = f"Yield estimate: about {cal:.0f} bushels an acre"
            iv = y.get("interval_bu_per_acre")
            if iv:
                txt += f" ({iv[0]:.0f} to {iv[1]:.0f})"
            if band:
                txt += f", {band} for the zone"
            txt += ", from the county model and the greenness curve of this field"
        else:
            txt = f"Yield outlook: {band} for the zone" if band else "Yield outlook"
            if bu is not None:
                txt += f", about {bu:.0f} bushels an acre by the greenness curve (a relative index, not a measured yield)"
        lines.append(txt + ".")
    if y.get("env_note"):
        lines.append(f"Soil and terrain: {y['env_note'].rstrip('.')}.")
    e = f.get("economics") or {}
    if e.get("recommendation"):
        rec = {"harvest_now": "Harvest now", "wait": "Waiting a week looks cheaper", "either": "Harvest now or wait: about the same"}
        txt = rec.get(e["recommendation"], e["recommendation"])
        if e.get("reason"):
            txt += f": {e['reason'].rstrip('.')}"
        if e.get("savings_usd") is not None and abs(e["savings_usd"]) >= 1 and "$" not in txt and "dollar" not in txt:
            txt += f" (about {abs(e['savings_usd']):,.0f} dollars difference)"
        lines.append(txt + ".")
    w = f.get("workability") or {}
    if w.get("words"):
        lines.append(w["words"].rstrip(".") + ".")
    wa = f.get("water") or {}
    if wa.get("water_status"):
        txt = {"wet": "Soil is wet", "adequate": "Soil moisture is adequate", "dry": "Soil is dry"}.get(wa["water_status"], wa["water_status"])
        if wa.get("days_since_rain") is not None:
            txt += f", {wa['days_since_rain']} days since the last real rain"
        lines.append(txt + ".")
    a = f.get("anomaly") or {}
    if a.get("anomaly_type") and a["anomaly_type"] != "none" and a.get("note"):
        lines.append(a["note"].rstrip(".") + ".")
    ev = f.get("events") or {}
    if ev.get("scar_type") and ev["scar_type"] != "tillage_or_residue":
        lines.append(f"Burn scar: {ev['scar_type'].replace('_', ' ')} severity from the burn ratio.")
    if ev.get("fire_exposure") and ev["fire_exposure"] != "none":
        lines.append("Active fire " + ("inside the field" if ev["fire_exposure"] == "inside" else "within 2 km") + " in the last 5 days.")
    c = f.get("coverage") or {}
    if c.get("staleness") == "stale":
        days = c.get("days_since_last_clear")
        lines.append("Satellite view is stale: no clear pass over this field yet this season." if days is None
                     else f"Satellite view is stale: {days} days since the last clear pass.")
    return lines


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    run(Path(ap.parse_args().data))
