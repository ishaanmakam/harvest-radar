"""The latest runs of several numerical weather models, folded into one ensemble per day.

    python intelligence/forecast_models.py --data data/out          # box centre from config/region.json

Open-Meteo (free, no key) serves the current runs of ECMWF IFS 0.25, NOAA GFS (with HRRR for the first two days over
the US), DWD ICON, the Canadian GEM, Meteo-France, the UK Met Office, JMA and NOAA's National Blend of Models in one
request. Instead of trusting one of them, the ensemble reads their agreement: how many models put a shower (>= 5 mm),
a blocking rain (>= 12 mm), the plan's act-now rain (>= 15 mm) or a soaking (>= 25 mm) on each day, the median and
range of the amounts, frost and hard-freeze counts, and the strongest gust. Counts are kept per variable (a model can
report rain and no temperature, or the reverse), and values a model does not report are left out, never read as zero.
Written to weather_ensemble.json next to weather.json (additive: weather.json stays the pipeline's single feed; the
decision layer quotes the agreement in words and the analytics run on the ensemble median where enough models reach).
"""
import argparse
import json
import math
import os
import statistics
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone as _utc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_region, write_json  # noqa: E402
from providers import _urllib_http  # noqa: E402
from weather_model import FROST_C, HARD_FREEZE_C, RAIN_BLOCK_MM, SHOWER_MM, SOAK_MM  # noqa: E402

MODELS = {                                   # Open-Meteo model ids -> names farmers and judges recognise
    "ecmwf_ifs025": "ECMWF IFS 0.25 degree",
    "gfs_seamless": "NOAA GFS (HRRR for the first 48 h over the US)",
    "icon_seamless": "DWD ICON",
    "gem_seamless": "Canadian GEM",
    "meteofrance_seamless": "Meteo-France ARPEGE and AROME",
    "ukmo_seamless": "UK Met Office",
    "jma_seamless": "JMA GSM and MSM",
    "ncep_nbm_conus": "NOAA National Blend of Models (CONUS)",
}
DAILY = ("precipitation_sum", "precipitation_probability_max", "temperature_2m_min", "temperature_2m_max", "wind_gusts_10m_max")
API = "https://api.open-meteo.com/v1/forecast"
ACT_NOW_MM = 15.0        # the plan's harvest-before-rain line (region.json rain_act_now_mm); recorded in the file so readers can check
KMH_PER_MS = 3.6         # Open-Meteo reports gusts in km/h
MIN_MODELS = 3           # fewer models reaching a day is one or two opinions, not an ensemble: no agreement words, no overlay
MAX_AGE_DAYS = 2         # a file fetched more than this many days before as_of is stale and ignored
THRESHOLDS = {"shower_mm": SHOWER_MM, "block_mm": RAIN_BLOCK_MM, "rain_act_now_mm": ACT_NOW_MM, "soak_mm": SOAK_MM,
              "frost_c": FROST_C, "hard_freeze_c": HARD_FREEZE_C}


class ForecastError(RuntimeError):
    pass


def box_centre(region):
    """(lat, lon) of the region's bbox [west, south, east, north]."""
    w, s, e, n = region["bbox"]
    return ((s + n) / 2, (w + e) / 2)


def fetch(lat, lon, timezone, days=14, http=None, models=None):
    """The raw multi-model daily document for one point. http(method, url, headers, body, timeout) -> (status, bytes)."""
    http = http or _urllib_http
    ids = list(models or MODELS)
    url = API + "?" + urllib.parse.urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": timezone,
                                              "forecast_days": int(days), "daily": ",".join(DAILY), "models": ",".join(ids)})
    try:
        status, body = http("GET", url, {"Accept": "application/json"}, None, 30)
    except Exception as e:
        raise ForecastError(f"forecast request failed: {e}") from e
    if status != 200:
        raise ForecastError(f"forecast request returned HTTP {status}: {body[:200]!r}")
    try:
        doc = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    except ValueError as e:
        raise ForecastError(f"forecast response was not JSON: {e}") from e
    if not isinstance(doc, dict) or "daily" not in doc:
        raise ForecastError("forecast response has no daily block")
    return doc


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _col(daily, var, model, i):
    arr = daily.get(f"{var}_{model}")
    if not isinstance(arr, list) or i >= len(arr):
        return None
    return _num(arr[i])


def _count(values, threshold, at_most=False):
    return sum(1 for v in values if (v <= threshold if at_most else v >= threshold))


def ensemble(doc):
    """Per-day agreement across whatever models answered. Each day carries integer counts per variable
    (n_precip, n_tmin, ...) and hit counts (k_rain_15mm, k_frost, ...) next to the shares, so any 'k of n' a reader
    prints is exact."""
    daily = (doc or {}).get("daily") or {}
    dates = [str(x)[:10] for x in (daily.get("time") or [])]
    found = {k[len("precipitation_sum_"):] for k in daily if k.startswith("precipitation_sum_")}
    order = list(MODELS)
    models = sorted(found, key=lambda m: (order.index(m) if m in order else len(order), m))
    days = []
    for i, dt in enumerate(dates):
        pr = [v for v in (_col(daily, "precipitation_sum", m, i) for m in models) if v is not None]
        prob = [v for v in (_col(daily, "precipitation_probability_max", m, i) for m in models) if v is not None]
        tmin = [v for v in (_col(daily, "temperature_2m_min", m, i) for m in models) if v is not None]
        tmax = [v for v in (_col(daily, "temperature_2m_max", m, i) for m in models) if v is not None]
        gust = [v for v in (_col(daily, "wind_gusts_10m_max", m, i) for m in models) if v is not None]
        n, nt = len(pr), len(tmin)
        k5, k12, k15, k25 = (_count(pr, SHOWER_MM), _count(pr, RAIN_BLOCK_MM), _count(pr, ACT_NOW_MM), _count(pr, SOAK_MM))
        kf, kh = _count(tmin, FROST_C, True), _count(tmin, HARD_FREEZE_C, True)
        days.append({
            "date": dt, "n_models": n, "n_precip": n, "n_tmin": nt, "n_prob": len(prob), "n_gust": len(gust),
            "precip_median_mm": round(statistics.median(pr), 1) if pr else None,
            "precip_mean_mm": round(statistics.fmean(pr), 1) if pr else None,
            "precip_max_mm": round(max(pr), 1) if pr else None,
            "precip_min_mm": round(min(pr), 1) if pr else None,
            "k_rain_5mm": k5, "k_rain_12mm": k12, "k_rain_15mm": k15, "k_rain_25mm": k25,
            "p_rain_5mm": k5 / n if n else 0.0, "p_rain_12mm": k12 / n if n else 0.0,
            "p_rain_15mm": k15 / n if n else 0.0, "p_rain_25mm": k25 / n if n else 0.0,
            "prob_mean_pct": round(statistics.fmean(prob)) if prob else None,
            "tmin_min_c": round(min(tmin), 1) if tmin else None,
            "tmin_median_c": round(statistics.median(tmin), 1) if tmin else None,
            "k_frost": kf, "k_hard_freeze": kh,
            "p_frost": kf / nt if nt else 0.0, "p_hard_freeze": kh / nt if nt else 0.0,
            "tmax_median_c": round(statistics.median(tmax), 1) if tmax else None,
            "gust_max_ms": round(max(gust) / KMH_PER_MS, 2) if gust else None,
            "agreement": None if n == 0 else ("dry" if k5 == 0 else ("wet" if k5 == n else "split")),
        })
    out = {"dates": dates, "models": models, "n_models_listed": len(models), "thresholds": dict(THRESHOLDS),
           "days": days, "summary": _summary(days, len(models))}
    for key in ("precip_median_mm", "precip_max_mm", "p_rain_5mm", "p_rain_12mm", "p_rain_15mm", "p_rain_25mm",
                "k_rain_15mm", "p_frost", "p_hard_freeze", "k_frost", "tmin_min_c", "tmin_median_c", "tmax_median_c",
                "gust_max_ms", "n_models", "n_tmin"):
        out[key] = [d[key] for d in days]
    return out


def _ranges(dates_in_order, picked):
    """Consecutive picked dates (by position) collapsed to 'a to b' spans."""
    spans, run = [], []
    for dt in dates_in_order:
        if dt in picked:
            run.append(dt)
        elif run:
            spans.append(run)
            run = []
    if run:
        spans.append(run)
    return [f"{r[0]} to {r[-1]}" if len(r) > 1 else r[0] for r in spans]


def _summary(days, listed):
    """One sentence per thing worth saying: dry spans, split or wet days with the count and amounts, frost
    agreement, and how many models still reach the later days."""
    live = [d for d in days if d["n_precip"] or d["n_tmin"]]
    if not live:
        return "No forecast models returned."
    parts = []
    dry = {d["date"] for d in days if d["agreement"] == "dry"}
    if dry:
        parts.append("under 5 mm in every model " + " and ".join(_ranges([d["date"] for d in days], dry)))
    for d in days:
        if d["agreement"] in ("split", "wet"):
            parts.append(f"{d['k_rain_5mm']} of {d['n_precip']} put {SHOWER_MM:.0f} mm or more on {d['date']} "
                         f"(median {d['precip_median_mm']:.0f} mm, up to {d['precip_max_mm']:.0f} mm)")
    for d in days:
        if d["k_frost"]:
            parts.append(f"{d['k_frost']} of {d['n_tmin']} see frost on {d['date']}" + (" (a hard freeze for some)" if d["k_hard_freeze"] else ""))
    short = [d for d in days if d["n_precip"] < listed]
    if short and listed:
        parts.append(f"only {min(d['n_precip'] for d in short)} to {max(d['n_precip'] for d in short)} of the {listed} models reach "
                     + " and ".join(_ranges([d["date"] for d in days], {d["date"] for d in short})))
    return "Weather models: " + "; ".join(parts) + "."


def run(data_dir, lat=None, lon=None, timezone=None, http=None, fetched_at=None, days=14, write=True):
    data_dir = Path(data_dir)
    if lat is None or lon is None:
        lat, lon = box_centre(load_region())
    timezone = timezone or os.environ.get("DEFAULT_TIMEZONE") or "America/Chicago"
    doc = fetch(lat, lon, timezone, days=days, http=http)
    ens = ensemble(doc)
    out = {"source": "open-meteo multi-model daily forecast", "fetched_at": fetched_at or datetime.now(_utc.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "lat": round(float(lat), 4), "lon": round(float(lon), 4), "timezone": timezone,
           "model_names": {m: MODELS.get(m, m) for m in ens["models"]}, **ens}
    if write:
        write_json(data_dir, "weather_ensemble.json", out)
    return out


# ---------------------------------------------------------------- readers (shared by decide.py and analytics.py)
def load_ensemble_doc(data_dir):
    """The file as a dict with only well-formed days, or None when absent, unreadable or the wrong shape."""
    p = Path(data_dir) / "weather_ensemble.json"
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(doc, dict):
        return None
    days = []
    for d in doc.get("days") if isinstance(doc.get("days"), list) else []:
        if not isinstance(d, dict) or not isinstance(d.get("date"), str):
            continue
        try:
            date.fromisoformat(d["date"][:10])
        except ValueError:
            continue
        days.append({**d, "date": d["date"][:10]})
    doc = dict(doc)
    doc["days"] = days
    return doc


def is_stale(doc, as_of, max_age_days=MAX_AGE_DAYS):
    """True when fetched_at is more than max_age_days before as_of. A missing or unreadable fetched_at is not stale
    (hand-made files), and a fetch after as_of is fine (as_of is the plan date, not the clock)."""
    if not doc or as_of is None:
        return False
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, str):
        return False
    try:
        fetched_day = date.fromisoformat(fetched[:10])
    except ValueError:
        return False
    return fetched_day < as_of - timedelta(days=max_age_days)


def load_ensemble(data_dir, as_of=None, max_age_days=MAX_AGE_DAYS):
    """{date: day dict} for the decision layer, or {} when absent, malformed or stale (a WARN is printed when stale).
    Each day also carries the file's thresholds so a reader can check them against the region's."""
    doc = load_ensemble_doc(data_dir)
    if not doc:
        return {}
    if is_stale(doc, as_of, max_age_days):
        print(f"  WARN weather_ensemble.json was fetched {doc.get('fetched_at')}, more than {max_age_days} days before {as_of}: ignored")
        return {}
    th = doc.get("thresholds") if isinstance(doc.get("thresholds"), dict) else {}
    out = {}
    for d in doc["days"]:
        day = dict(d)
        day["rain_act_now_mm"] = th.get("rain_act_now_mm", ACT_NOW_MM)
        day["frost_c"] = th.get("frost_c", FROST_C)
        out[date.fromisoformat(d["date"])] = day
    return out


def count_of(day, kind):
    """(k, n) integers for a kind of agreement from a day dict, tolerant of files that carry only shares.
    kind: 'rain' (act-now rain, precip models), 'frost', 'hard_freeze' (tmin models). (0, 0) when unknown."""
    if not isinstance(day, dict):
        return 0, 0
    if kind == "rain":
        n = day.get("n_precip", day.get("n_models"))
        k = day.get("k_rain_15mm")
        share = day.get("p_rain_15mm")
    else:
        n = day.get("n_tmin", day.get("n_models"))
        k = day.get("k_hard_freeze" if kind == "hard_freeze" else "k_frost")
        share = day.get("p_hard_freeze" if kind == "hard_freeze" else "p_frost")
    n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0 else 0
    if isinstance(k, (int, float)) and not isinstance(k, bool) and math.isfinite(k):
        k = int(k)
    elif isinstance(share, (int, float)) and not isinstance(share, bool) and math.isfinite(share) and 0 <= share <= 1:
        k = round(share * n)
    else:
        return 0, 0
    return max(0, min(k, n)), n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch the latest runs of several weather models and write weather_ensemble.json.")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    try:
        out = run(a.data, days=a.days)
    except ForecastError as e:
        print(f"  forecast models: {e}; the previous weather_ensemble.json (if any) is left in place")
        sys.exit(1)
    print(f"  {len(out['models'])} models, {len(out['dates'])} days -> {Path(a.data) / 'weather_ensemble.json'}")
    print("  " + out["summary"])
