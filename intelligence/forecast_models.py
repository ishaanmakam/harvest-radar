"""The latest runs of several numerical weather models, folded into one ensemble per day.

    python intelligence/forecast_models.py --data data/out          # box centre from config/region.json

Open-Meteo (free, no key) serves the current runs of ECMWF IFS 0.25, NOAA GFS (with HRRR for the first two days over
the US), DWD ICON, the Canadian GEM, Meteo-France, the UK Met Office, JMA and NOAA's National Blend of Models in one
request. Instead of trusting one of them, the ensemble reads their agreement: the share of models that put a shower
(>= 5 mm), a blocking rain (>= 12 mm), the plan's act-now rain (>= 15 mm) or a soaking (>= 25 mm) on each day, the
median and range of the amounts, frost and hard-freeze agreement, and the strongest gust. It is written to
weather_ensemble.json next to weather.json (additive: weather.json stays the pipeline's single feed; the decision
layer and the analytics read the ensemble when it is present and say how many models agree).
"""
import argparse
import json
import os
import statistics
import sys
import urllib.parse
from datetime import date, datetime, timezone as _utc
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
ACT_NOW_MM = 15.0        # the plan's harvest-before-rain line (region.json rain_act_now_mm); the agreement share uses it too
KMH_PER_MS = 3.6         # Open-Meteo reports gusts in km/h


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


def _share(values, threshold, at_most=False):
    if not values:
        return 0.0
    hits = sum(1 for v in values if (v <= threshold if at_most else v >= threshold))
    return hits / len(values)


def _col(daily, var, model, i):
    arr = daily.get(f"{var}_{model}")
    if not isinstance(arr, list) or i >= len(arr):
        return None
    v = arr[i]
    return float(v) if isinstance(v, (int, float)) else None


def ensemble(doc):
    """Per-day agreement across whatever models answered. Values a model did not report are left out of that day's
    count, never read as zero."""
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
        n = len(pr)
        p5 = _share(pr, SHOWER_MM)
        days.append({
            "date": dt, "n_models": n,
            "precip_median_mm": round(statistics.median(pr), 1) if pr else None,
            "precip_mean_mm": round(statistics.fmean(pr), 1) if pr else None,
            "precip_max_mm": round(max(pr), 1) if pr else None,
            "precip_min_mm": round(min(pr), 1) if pr else None,
            "p_rain_5mm": p5, "p_rain_12mm": _share(pr, RAIN_BLOCK_MM), "p_rain_15mm": _share(pr, ACT_NOW_MM), "p_rain_25mm": _share(pr, SOAK_MM),
            "prob_mean_pct": round(statistics.fmean(prob)) if prob else None,
            "tmin_min_c": round(min(tmin), 1) if tmin else None,
            "tmin_median_c": round(statistics.median(tmin), 1) if tmin else None,
            "p_frost": _share(tmin, FROST_C, at_most=True), "p_hard_freeze": _share(tmin, HARD_FREEZE_C, at_most=True),
            "tmax_median_c": round(statistics.median(tmax), 1) if tmax else None,
            "gust_max_ms": round(max(gust) / KMH_PER_MS, 2) if gust else None,
            "agreement": None if n == 0 else ("dry" if p5 == 0 else ("wet" if p5 == 1 else "split")),
        })
    out = {"dates": dates, "models": models, "days": days, "summary": _summary(days)}
    for key in ("precip_median_mm", "precip_max_mm", "p_rain_5mm", "p_rain_12mm", "p_rain_15mm", "p_rain_25mm",
                "p_frost", "p_hard_freeze", "tmin_min_c", "tmin_median_c", "tmax_median_c", "gust_max_ms", "n_models"):
        out[key] = [d[key] for d in days]
    return out


def _summary(days):
    """One sentence per thing worth saying: dry days, split or wet days with the count and amounts, frost agreement."""
    if not days or all(d["n_models"] == 0 for d in days):
        return "No forecast models returned."
    parts = []
    dry = [d["date"] for d in days if d["agreement"] == "dry"]
    if dry:
        parts.append("all models dry on " + ", ".join(dry))
    for d in days:
        if d["agreement"] in ("split", "wet"):
            k = round(d["p_rain_5mm"] * d["n_models"])
            parts.append(f"{k} of {d['n_models']} put {SHOWER_MM:.0f} mm or more on {d['date']} "
                         f"(median {d['precip_median_mm']:.0f} mm, up to {d['precip_max_mm']:.0f} mm)")
    frost = [d for d in days if d["p_frost"] > 0]
    for d in frost:
        k = round(d["p_frost"] * d["n_models"])
        parts.append(f"{k} of {d['n_models']} see frost on {d['date']}" + (" (a hard freeze for some)" if d["p_hard_freeze"] > 0 else ""))
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


def load_ensemble(data_dir):
    """{date: day dict} from weather_ensemble.json, or {} when the file is absent or unreadable."""
    p = Path(data_dir) / "weather_ensemble.json"
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return {date.fromisoformat(d["date"]): d for d in doc.get("days", []) if d.get("date")}
    except (ValueError, KeyError, TypeError, OSError):
        return {}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch the latest runs of several weather models and write weather_ensemble.json.")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    out = run(a.data, days=a.days)
    print(f"  {len(out['models'])} models, {len(out['dates'])} days -> {Path(a.data) / 'weather_ensemble.json'}")
    print("  " + out["summary"])
