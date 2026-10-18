"""County yield model: measured county yields against growing-season weather, validated year by year.

    python intelligence/yield_train.py --data data/out                 # Story County, corn and soybeans

Labels: USDA county yields. With NASS_API_KEY in .env they come from NASS QuickStats (every Iowa county, 2008-2025);
without a key, from the CropNet dataset's USDA county tables (2016-2022, CC BY-NC 4.0, research use). Features: NASA
POWER daily weather at each county centroid (Census gazetteer), summed over May to September: degree days, heat-stress
days in July and August, rain by month, the longest dry spell, the first fall frost, and a year term for the technology
trend. Model: ridge regression on standardised features, numpy only, with leave-one-year-out cross-validation choosing
the penalty and reporting the error per held-out year. The target county's current season is observed to date and
filled from its own climatology for the days NASA POWER has not published yet.

The estimate is a county number with a 90% band from the hindcast error. yield_model.py distributes it across fields
with the relative NDVI index; the words everywhere say "estimate" and show the band. Nothing here is a field measurement.
"""
import argparse
import csv
import io
import json
import math
import os
import statistics
import sys
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, load_env, write_json  # noqa: E402
from providers import _urllib_http  # noqa: E402

POWER = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = "T2M_MAX,T2M_MIN,PRECTOTCORR"
CROPNET_BASE = "https://huggingface.co/datasets/CropNet/CropNet/resolve/main/USDA%20Crop%20Dataset/"
GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip"
NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
UA = {"User-Agent": "Overfield/1 (DubHacks 2026 field inspector)", "Accept": "application/json"}
CROPS = {
    "corn": {"cropnet": "Corn", "nass_commodity": "CORN", "nass_short": "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE"},
    "soybean": {"cropnet": "Soybean", "nass_commodity": "SOYBEANS", "nass_short": "SOYBEANS - YIELD, MEASURED IN BU / ACRE"},
}
STATE = {"usps": "IA", "ansi": "19"}
TARGET_FIPS = "19169"                              # Story County
CROPNET_YEARS = range(2016, 2023)
NASS_YEARS = range(2008, 2026)
SEASON_START, SEASON_END = (5, 1), (9, 30)
GDD_BASE, GDD_CAP = 10.0, 30.0
HEAT32, HEAT35 = 32.0, 35.0
FROST_C = 0.0
FEATURES = ["gdd", "heat32", "heat35", "p_may", "p_jun", "p_jul", "p_aug", "p_sep", "dry_spell", "frost_doy", "trend"]
LAMBDAS = (0.01, 0.1, 1.0, 10.0, 100.0)
Z90 = 1.64                                         # 90% band under a normal error
METHOD = ("Ridge regression of county yield on growing-season weather (degree days, heat-stress days, rain by month, dry "
          "spell, first frost), a year trend and the county baseline (its mean yield in the other years), trained on measured USDA county yields for every county in the state and "
          "validated leave-one-year-out; the band is 1.64 times the held-out error. A county estimate, not a field measurement.")


# ---------------------------------------------------------------- parsers
def parse_cropnet_csv(text, state_ansi=STATE["ansi"]):
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("state_ansi") or "").strip() != state_ansi:
            continue
        county_ansi = (r.get("county_ansi") or "").strip()
        name = (r.get("county_name") or "").strip()
        if len(county_ansi) != 3 or county_ansi == "999" or "OTHER" in name.upper():
            continue
        try:
            y = float(r.get("YIELD, MEASURED IN BU / ACRE") or "")
        except ValueError:
            continue
        try:
            prod = float(r.get("PRODUCTION, MEASURED IN BU") or "")
        except ValueError:
            prod = None
        rows.append({"year": int(r["year"]), "fips": state_ansi + county_ansi, "county": name, "yield_bu_ac": y, "production_bu": prod})
    return rows


def parse_gazetteer(text, state=STATE["usps"]):
    out = {}
    lines = [l for l in text.splitlines() if l.strip()]
    header = [h.strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("\t")]
        row = dict(zip(header, cells))
        if row.get("USPS") != state:
            continue
        try:
            out[row["GEOID"]] = {"name": row["NAME"], "lat": float(row["INTPTLAT"]), "lon": float(row["INTPTLONG"])}
        except (KeyError, ValueError):
            continue
    return out


def parse_power(doc):
    """{"dates": [...iso], "tmax": [...], "tmin": [...], "precip": [...]} with the fill value read as None."""
    p = ((doc or {}).get("properties") or {}).get("parameter") or {}
    fill = ((doc or {}).get("header") or {}).get("fill_value", -999.0)
    keys = sorted(set(p.get("T2M_MAX", {})) | set(p.get("T2M_MIN", {})) | set(p.get("PRECTOTCORR", {})))

    def val(series, k):
        v = series.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= -990 or v == fill:
            return None
        return float(v)
    return {"dates": [f"{k[:4]}-{k[4:6]}-{k[6:8]}" for k in keys],
            "tmax": [val(p.get("T2M_MAX", {}), k) for k in keys],
            "tmin": [val(p.get("T2M_MIN", {}), k) for k in keys],
            "precip": [val(p.get("PRECTOTCORR", {}), k) for k in keys]}


def parse_nass(doc):
    rows = []
    for r in (doc or {}).get("data") or []:
        county_ansi = (r.get("county_ansi") or "").strip()
        if len(county_ansi) != 3 or "OTHER" in (r.get("county_name") or "").upper():
            continue
        try:
            y = float(str(r.get("Value", "")).replace(",", ""))
            year = int(r.get("year"))
        except (TypeError, ValueError):
            continue
        rows.append({"year": year, "fips": (r.get("state_ansi") or "").strip() + county_ansi, "county": (r.get("county_name") or "").strip(),
                     "yield_bu_ac": y, "production_bu": None})
    return rows


# ---------------------------------------------------------------- fetchers (all cached)
def _get(http, url, timeout=120):
    status, body = http("GET", url, dict(UA), None, timeout)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url[:80]}")
    return body


def power_url(lat, lon, start, end):
    return POWER + "?" + urllib.parse.urlencode({"parameters": POWER_PARAMS, "community": "AG", "longitude": f"{lon:.4f}",
                                                 "latitude": f"{lat:.4f}", "start": start, "end": end, "format": "JSON"})


def fetch_power(lat, lon, start, end, http, cache_path):
    cache_path = Path(cache_path)
    if cache_path.is_file():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    body = _get(http, power_url(lat, lon, start, end))
    daily = parse_power(json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(daily), encoding="utf-8")
    return daily


def load_gazetteer(cache_dir, http, state=STATE["usps"]):
    p = Path(cache_dir) / "census" / "counties.txt"
    if not p.is_file():
        body = _get(http, GAZETTEER_URL)
        z = zipfile.ZipFile(io.BytesIO(body))
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return parse_gazetteer(p.read_text(encoding="utf-8"), state)


def load_cropnet_yields(cache_dir, http, crop, years, state_ansi=STATE["ansi"]):
    rows = []
    for y in years:
        fn = f"USDA_{CROPS[crop]['cropnet']}_County_{y}.csv"
        p = Path(cache_dir) / "cropnet" / fn
        if not p.is_file():
            try:
                body = _get(http, CROPNET_BASE + f"{CROPS[crop]['cropnet']}s/{y}/{fn}" if crop == "soybean" else CROPNET_BASE + f"{CROPS[crop]['cropnet']}/{y}/{fn}")
            except Exception:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        rows += parse_cropnet_csv(p.read_text(encoding="utf-8", errors="replace"), state_ansi)
    return rows


def load_nass_yields(cache_dir, http, crop, years, key, state_ansi=STATE["ansi"]):
    years = list(years)
    p = Path(cache_dir) / "nass" / f"{crop}_{state_ansi}_{years[0]}_{years[-1]}.json"
    if p.is_file():
        try:
            return parse_nass(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    params = {"key": key, "source_desc": "SURVEY", "sector_desc": "CROPS", "commodity_desc": CROPS[crop]["nass_commodity"],
              "statisticcat_desc": "YIELD", "short_desc": CROPS[crop]["nass_short"], "agg_level_desc": "COUNTY",
              "state_ansi": state_ansi, "year__GE": years[0], "year__LE": years[-1], "format": "JSON"}
    body = _get(http, NASS_URL + "?" + urllib.parse.urlencode(params))
    doc = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return parse_nass(doc)


# ---------------------------------------------------------------- features
def _in_season(d, year):
    return date(year, *SEASON_START) <= d <= date(year, *SEASON_END)


def season_features(daily, year):
    dates = [date.fromisoformat(x) for x in daily["dates"]]
    filled = daily.get("filled") or [False] * len(dates)
    gdd = 0.0
    heat32 = heat35 = 0
    months = {5: 0.0, 6: 0.0, 7: 0.0, 8: 0.0, 9: 0.0}
    observed = 0
    n_filled = 0
    dry_run = dry_best = 0
    frost_doy = None
    for d, hi, lo, pr, fl in zip(dates, daily["tmax"], daily["tmin"], daily["precip"], filled):
        if d.year != year:
            continue
        if _in_season(d, year):
            if hi is not None and lo is not None and pr is not None:
                observed += 1
                n_filled += 1 if fl else 0
            if hi is not None and lo is not None:
                gdd += max(0.0, (min(max(hi, GDD_BASE), GDD_CAP) + min(max(lo, GDD_BASE), GDD_CAP)) / 2 - GDD_BASE)
                if d.month in (7, 8):
                    heat32 += 1 if hi >= HEAT32 else 0
                    heat35 += 1 if hi >= HEAT35 else 0
            if pr is not None:
                months[d.month] += pr
                if 6 <= d.month <= 8:
                    dry_run = dry_run + 1 if pr < 1.0 else 0
                    dry_best = max(dry_best, dry_run)
        if frost_doy is None and d.month >= 9 and lo is not None and lo <= FROST_C:
            frost_doy = d.timetuple().tm_yday
    return {"gdd": round(gdd, 1), "heat32": heat32, "heat35": heat35, "p_may": round(months[5], 1), "p_jun": round(months[6], 1),
            "p_jul": round(months[7], 1), "p_aug": round(months[8], 1), "p_sep": round(months[9], 1), "dry_spell": dry_best,
            "frost_doy": frost_doy, "trend": year - 2000, "days_observed": observed, "days_filled": n_filled}


def climatology(dailies):
    """mmdd -> (tmax, tmin, precip) means across the given daily records."""
    acc = {}
    for daily in dailies:
        for dt, hi, lo, pr in zip(daily["dates"], daily["tmax"], daily["tmin"], daily["precip"]):
            if hi is None or lo is None or pr is None:
                continue
            key = dt[5:]
            s = acc.setdefault(key, [0.0, 0.0, 0.0, 0])
            s[0] += hi
            s[1] += lo
            s[2] += pr
            s[3] += 1
    return {k: (v[0] / v[3], v[1] / v[3], v[2] / v[3]) for k, v in acc.items() if v[3]}


def fill_season(daily, year, clim):
    """Fill missing days of the whole year from climatology; count the ones inside the growing season.
    Returns (filled daily with a 'filled' flag per day, n_filled_in_season)."""
    have = {d: i for i, d in enumerate(daily["dates"])}
    dates, tmax, tmin, precip, flags = [], [], [], [], []
    d = date(year, 1, 1)
    n_filled = 0
    while d.year == year:
        iso = d.isoformat()
        i = have.get(iso)
        hi = daily["tmax"][i] if i is not None else None
        lo = daily["tmin"][i] if i is not None else None
        pr = daily["precip"][i] if i is not None else None
        fl = False
        if hi is None or lo is None or pr is None:
            c = clim.get(iso[5:])
            if c:
                hi = hi if hi is not None else round(c[0], 1)
                lo = lo if lo is not None else round(c[1], 1)
                pr = pr if pr is not None else round(c[2], 1)
                fl = True
                if _in_season(d, year):
                    n_filled += 1
        dates.append(iso)
        tmax.append(hi)
        tmin.append(lo)
        precip.append(pr)
        flags.append(fl)
        d += timedelta(days=1)
    return {"dates": dates, "tmax": tmax, "tmin": tmin, "precip": precip, "filled": flags}, n_filled


# ---------------------------------------------------------------- model
def _matrix(rows, feats):
    return np.array([[float(r[f]) for f in feats] for r in rows], dtype=float), np.array([float(r["y"]) for r in rows], dtype=float)


def fit_ridge(rows, feats, lam):
    X, y = _matrix(rows, feats)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Z = (X - mean) / std
    yb = y.mean()
    A = Z.T @ Z + lam * np.eye(Z.shape[1])
    coef = np.linalg.solve(A, Z.T @ (y - yb))
    return {"features": list(feats), "coef": {f: float(c) for f, c in zip(feats, coef)}, "intercept": float(yb),
            "mean": {f: float(m) for f, m in zip(feats, mean)}, "std": {f: float(s) for f, s in zip(feats, std)}, "lam": float(lam)}


def predict(model, row):
    return float(model["intercept"] + sum(model["coef"][f] * (float(row[f]) - model["mean"][f]) / model["std"][f] for f in model["features"]))


def county_means(rows):
    """Mean yield per county over the given rows."""
    acc = {}
    for r in rows:
        s = acc.setdefault(r["fips"], [0.0, 0])
        s[0] += float(r["y"])
        s[1] += 1
    return {f: s[0] / s[1] for f, s in acc.items() if s[1]}


def with_county_baseline(train, apply, fallback=None):
    """Copies of `apply` rows carrying county_mean: the county's mean yield over `train` rows, leaving a row's own
    year out when it is itself in train (so the baseline never contains the answer). Counties absent from train get
    the overall mean."""
    sums = {}
    for r in train:
        s = sums.setdefault(r["fips"], [0.0, 0])
        s[0] += float(r["y"])
        s[1] += 1
    overall = (sum(s[0] for s in sums.values()) / sum(s[1] for s in sums.values())) if sums else (fallback or 0.0)
    train_ids = {(r["fips"], r["year"]) for r in train}
    out = []
    for r in apply:
        s = sums.get(r["fips"])
        if s and (r["fips"], r["year"]) in train_ids and s[1] > 1:
            cm = (s[0] - float(r["y"])) / (s[1] - 1)
        elif s and (r["fips"], r["year"]) not in train_ids:
            cm = s[0] / s[1]
        else:
            cm = overall
        out.append({**r, "county_mean": cm})
    return out


def loyo(rows, feats, lams=LAMBDAS, county_baseline=True):
    years = sorted({r["year"] for r in rows})
    best = None
    feats = list(feats) + (["county_mean"] if county_baseline and "county_mean" not in feats else [])
    for lam in lams:
        preds = {}
        for yr in years:
            train = [r for r in rows if r["year"] != yr]
            test = [r for r in rows if r["year"] == yr]
            if len(train) < len(feats) + 2 or not test:
                continue
            if county_baseline:
                test = with_county_baseline(train, test)
                train = with_county_baseline(train, train)
            m = fit_ridge(train, feats, lam)
            for r in test:
                preds[(r["fips"], r["year"])] = (predict(m, r), float(r["y"]))
        if not preds:
            continue
        err = np.array([p - a for p, a in preds.values()])
        rmse = float(math.sqrt(np.mean(err ** 2)))
        if best is None or rmse < best[0]:
            best = (rmse, lam, preds)
    if best is None:
        return {"lambda": lams[0], "years": [], "rmse": None, "bias": None, "r2": None, "predictions": {}}
    rmse, lam, preds = best
    actual = np.array([a for _, a in preds.values()])
    err = np.array([p - a for p, a in preds.values()])
    sst = float(np.sum((actual - actual.mean()) ** 2))
    per_year = []
    for yr in years:
        e = [p - a for (f, y), (p, a) in preds.items() if y == yr]
        if e:
            per_year.append({"year": yr, "n": len(e), "rmse": round(float(math.sqrt(np.mean(np.square(e)))), 2), "bias": round(float(np.mean(e)), 2)})
    return {"lambda": lam, "years": per_year, "rmse": round(rmse, 2), "bias": round(float(err.mean()), 2),
            "r2": round(1 - float(np.sum(err ** 2)) / sst, 3) if sst > 0 else None, "features": feats,
            "predictions": {f"{f}:{y}": round(p, 2) for (f, y), (p, a) in preds.items()}}


def interval(estimate, rmse):
    return (round(estimate - Z90 * rmse, 1), round(estimate + Z90 * rmse, 1))


# ---------------------------------------------------------------- run
def run(data_dir, cache_dir=None, http=None, years=None, target_year=None, target_fips=TARGET_FIPS, state=STATE, today=None,
        fetched_at=None, nass_key="auto", write=True, crops=("corn", "soybean")):
    data_dir = Path(data_dir)
    cache_dir = Path(cache_dir) if cache_dir else ROOT / "data" / "cache" / "yield"
    http = http or _urllib_http
    today = today or date.today()
    target_year = target_year or today.year
    if nass_key == "auto":
        load_env()
        nass_key = os.environ.get("NASS_API_KEY") or None
    years = list(years) if years is not None else list(NASS_YEARS if nass_key else CROPNET_YEARS)
    counties = load_gazetteer(cache_dir, http, state["usps"])
    if target_fips not in counties:
        raise ValueError(f"county {target_fips} not in the gazetteer for {state['usps']}")
    sources = ["NASA POWER daily T2M_MAX, T2M_MIN, PRECTOTCORR at each county centroid (no key)",
               "US Census Bureau 2023 county gazetteer (centroids)"]
    if nass_key:
        sources.insert(0, f"USDA NASS QuickStats county yields, {state['usps']}, {years[0]}-{years[-1]} (survey)")
    else:
        sources.insert(0, f"CropNet USDA Crop Dataset: NASS county yields {years[0]}-{years[-1]} (CC BY-NC 4.0, research use)")

    weather_cache = {}

    def weather(fips, year, end=None):
        key = (fips, year, end)
        if key not in weather_cache:
            c = counties[fips]
            start, stop = f"{year}0101", end or f"{year}1231"
            weather_cache[key] = fetch_power(c["lat"], c["lon"], start, stop, http, cache_dir / "power" / f"{fips}_{start}_{stop}.json")
        return weather_cache[key]

    out = {"as_of": today.isoformat(), "fetched_at": fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "sources": sources, "state": state["usps"], "target_fips": target_fips, "crops": {}}
    for crop in crops:
        if nass_key:
            ylds = load_nass_yields(cache_dir, http, crop, years, nass_key, state["ansi"])
        else:
            ylds = load_cropnet_yields(cache_dir, http, crop, years, state["ansi"])
        ylds = [r for r in ylds if r["fips"] in counties and r["year"] in years]
        rows, missing_frost = [], []
        for r in ylds:
            try:
                f = season_features(weather(r["fips"], r["year"]), r["year"])
            except Exception as e:  # a county's weather fetch failing drops that row, never the run
                continue
            if f["days_observed"] < 120:
                continue
            row = {"fips": r["fips"], "year": r["year"], "y": r["yield_bu_ac"], **f}
            rows.append(row)
            if f["frost_doy"] is None:
                missing_frost.append(row)
        frost_med = statistics.median([r["frost_doy"] for r in rows if r["frost_doy"] is not None]) if any(r["frost_doy"] is not None for r in rows) else 290
        for r in missing_frost:
            r["frost_doy"] = frost_med
        feats = [f for f in FEATURES if len({round(float(r[f]), 6) for r in rows}) > 1] if rows else list(FEATURES)
        if len(rows) < len(feats) + 3:
            out["crops"][crop] = {"county": counties[target_fips]["name"], "county_fips": target_fips, "error": f"only {len(rows)} county-seasons with yields and weather; no model"}
            continue
        cv = loyo(rows, feats)
        feats = cv.get("features", feats)
        fit_rows = with_county_baseline(rows, rows)
        model = fit_ridge(fit_rows, feats, cv["lambda"])
        target_means = county_means(rows)
        # the target season: observed to date, then the county's own climatology
        clim = climatology([weather(target_fips, y) for y in years if (target_fips, y, None) in weather_cache or True][:0] or
                           [weather(target_fips, y) for y in years])
        partial = weather(target_fips, target_year, end=today.strftime("%Y%m%d"))
        filled, n_filled = fill_season(partial, target_year, clim)
        tf = season_features(filled, target_year)
        if tf["frost_doy"] is None:
            tf["frost_doy"] = frost_med
        tf["county_mean"] = target_means.get(target_fips, sum(target_means.values()) / len(target_means) if target_means else 0.0)
        est = predict(model, tf)
        lo, hi = interval(est, cv["rmse"] or 0.0)
        hind = []
        for yr in years:
            actual = next((r["y"] for r in rows if r["fips"] == target_fips and r["year"] == yr), None)
            pred = cv["predictions"].get(f"{target_fips}:{yr}")
            if actual is not None or pred is not None:
                hind.append({"year": yr, "actual": actual, "predicted": pred})
        out["crops"][crop] = {
            "county": counties[target_fips]["name"], "county_fips": target_fips, "years": years, "n_train": len(rows),
            "n_counties": len({r["fips"] for r in rows}), "features": feats, "lambda": cv["lambda"],
            "coefficients": {f: round(v, 4) for f, v in model["coef"].items()}, "intercept": round(model["intercept"], 3),
            "feature_mean": {f: round(v, 3) for f, v in model["mean"].items()}, "feature_std": {f: round(v, 3) for f, v in model["std"].items()},
            "rmse_bu_per_acre": cv["rmse"], "bias_bu_per_acre": cv["bias"], "r2": cv["r2"], "loyo_years": cv["years"],
            "hindcast": hind, "estimate_bu_per_acre": round(est, 1), "interval_bu_per_acre": [lo, hi],
            "target": {**{k: v for k, v in tf.items()}, "year": target_year, "observed_through": partial["dates"][-1] if partial["dates"] else None,
                       "days_filled": n_filled},
            "method": METHOD,
        }
    if write:
        write_json(data_dir, "yield_model.json", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Train the county yield model and write yield_model.json")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--fips", default=TARGET_FIPS)
    ap.add_argument("--year", type=int, default=None, help="target year (default: this year)")
    a = ap.parse_args()
    doc = run(a.data, target_fips=a.fips, target_year=a.year)
    for crop, c in doc["crops"].items():
        if "error" in c:
            print(f"  {crop}: {c['error']}")
            continue
        print(f"  {crop}: {c['county']} {c['target']['year']} estimate {c['estimate_bu_per_acre']} bu/ac "
              f"(90% band {c['interval_bu_per_acre'][0]} to {c['interval_bu_per_acre'][1]}); held-out RMSE {c['rmse_bu_per_acre']} bu/ac, "
              f"R2 {c['r2']}, {c['n_train']} county-seasons over {c['n_counties']} counties, {c['years'][0]}-{c['years'][-1]}")
