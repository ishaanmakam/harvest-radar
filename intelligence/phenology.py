"""Phenology: the shape of each field's season, and a second harvest date read off the greenness decline.

For every field the clear NDVI passes give
  green_up_date        the day NDVI crossed 0.40 on the way up (interpolated between the two passes around it)
  peak_date, peak_ndvi the top of the season (status.json's plateau-centred peak when it has one)
  plateau_days         how long the crop stayed within 0.04 of its peak
  senescence_onset     the first post-peak pass at or below 90 % of the peak: the crop starts drying down
  senescence_rate      least-squares NDVI per day from onset through the last standing pass (negative)
  curve_harvest_date   where that fitted line reaches harvest_ndvi_max (0.25, residue level): an estimate of the
                       harvest date that is independent of the lag model in harvest_model.py
and the two estimates are compared: agree / earlier / later against the lag model's window, plus an ensemble date.
Fields are sorted into early / mid / late green-up cohorts within their crop, and when truth.json exists the
landscape carries a score: mean absolute error of the curve date and of the lag model's window centre, each
computed as if the field were still standing.

Pure: analyze(data, config, prior) reads the dicts it is given and returns plain JSON-ready values. Nothing is
printed; problems go to landscape["notes"].
"""
import math
from datetime import date, timedelta

import numpy as np

KEY = "phenology"

GREEN_UP_NDVI = 0.40            # the first pass at or above this is green-up (about V6 for corn); same line as decide.green_up_date
PLATEAU_DROP = 0.04             # default for config thresholds.peak_plateau_drop: passes within this of the peak form the plateau (harvest_model.py)
SENESCENCE_FRACTION = 0.90      # onset = first post-peak smoothed pass at or below this share of the peak. ASSUMPTION: a 10 % loss
                                # of greenness is past the plateau noise (about 0.04 to 0.05) on an NDVI of 0.8 to 0.9
MIN_FIT_POINTS = 3              # fewer decline passes than this give no slope and no curve date
MIN_DECLINE_SLOPE = -0.002      # NDVI per day; a fitted decline flatter than this is not extrapolated to a harvest date
HARVEST_NDVI_MAX = 0.25         # default for config thresholds.harvest_ndvi_max: residue level the decline is extrapolated to
AGREE_TOLERANCE_DAYS = 3        # curve date inside the lag window, or this close to it, counts as agreement
CURVE_MAX_DAYS_AHEAD = 90       # ASSUMPTION: a crossing more than this many days after the last pass is not a forecast
COHORT_MIN_N = 3                # ASSUMPTION: green-up tertiles need at least three dated fields in the crop
DEFAULT_LAG_DAYS = 58           # default for config thresholds.default_peak_to_harvest_days, used only in the evaluation
TERTILES = (100.0 / 3.0, 200.0 / 3.0)
SMOOTH_WINDOW = 3               # running median of three passes, the same smoothing harvest_model.py applies before its peak


# ----------------------------------------------------------------------------------------------- small helpers

def _d(x):
    """ISO string -> date, None when missing or unreadable."""
    if x is None:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _iso(x):
    return x.isoformat() if isinstance(x, date) else None


def _num(x):
    """A finite float or None (numpy scalars, NaN and inf included)."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _round(x, nd):
    v = _num(x)
    return None if v is None else round(v, nd)


def _plain(obj):
    """Recursively turn numpy scalars into Python numbers and non-finite floats into None."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _median_date(dates):
    """ISO date at the median ordinal (the lower middle for an even count), None for an empty list."""
    ords = [x.toordinal() for x in dates if isinstance(x, date)]
    if not ords:
        return None
    return date.fromordinal(int(math.floor(float(np.median(ords))))).isoformat()


def _median(vals, nd):
    vals = [v for v in (_num(x) for x in vals) if v is not None]
    return round(float(np.median(vals)), nd) if vals else None


def _from_ordinal(x):
    return date.fromordinal(int(round(x)))


# ----------------------------------------------------------------------------------------------- the season shape

def clear_passes(dates, series):
    """[(date, ndvi)] of the clear passes, sorted by date; cloudy (null) entries are dropped."""
    if not dates or not isinstance(series, dict):
        return []
    out = []
    for dt, v in zip(dates, series.get("ndvi") or []):
        day, val = _d(dt), _num(v)
        if day is not None and val is not None:
            out.append((day, val))
    return sorted(out)


def median_smooth(vals, window=SMOOTH_WINDOW):
    """Running median over `window` passes (shorter at the ends), as a numpy array."""
    v = np.asarray(vals, dtype=float)
    half = window // 2
    return np.array([np.median(v[max(0, i - half): i + half + 1]) for i in range(len(v))]) if len(v) else v


def green_up_date(passes, threshold=GREEN_UP_NDVI):
    """The day NDVI crossed `threshold` on the way up, interpolated between the last pass below it and the first
    pass at or above it. None when the field was already that green on its first clear pass (the crossing is
    before the record) or never got there."""
    for i, (day, val) in enumerate(passes):
        if val >= threshold:
            if i == 0:
                return None
            d0, v0 = passes[i - 1]
            gap = (day - d0).days
            frac = (threshold - v0) / (val - v0) if val != v0 else 0.0
            return d0 + timedelta(days=round(frac * gap))
    return None


def plateau_bounds(sm, pk, drop):
    """Index range (lo, hi) of the run of smoothed values within `drop` of sm[pk] around the peak index."""
    lo = hi = pk
    while lo > 0 and sm[lo - 1] >= sm[pk] - drop:
        lo -= 1
    while hi < len(sm) - 1 and sm[hi + 1] >= sm[pk] - drop:
        hi += 1
    return lo, hi


def senescence_onset_index(passes, sm, peak_date, peak_ndvi, fraction=SENESCENCE_FRACTION):
    """Index of the first pass after peak_date whose smoothed NDVI is at or below fraction * peak, else None."""
    if peak_date is None or peak_ndvi is None:
        return None
    line = fraction * peak_ndvi
    for i, (day, _) in enumerate(passes):
        if day > peak_date and sm[i] <= line:
            return i
    return None


def fit_decline(points):
    """Least-squares line through [(date, ndvi)]: (slope per day, intercept at the first date, n). None under
    MIN_FIT_POINTS. Raw NDVI is fitted: the regression does its own averaging."""
    if len(points) < MIN_FIT_POINTS:
        return None
    t0 = points[0][0]
    x = np.array([(p[0] - t0).days for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept), len(points)


def field_shape(passes, rec, today, th):
    """The season shape of one field from its clear passes and its status.json record (may be empty).
    today = the as_of date (or the evaluation cutoff). Returns the per-field dict minus cohort."""
    rec = rec or {}
    drop = float(th.get("peak_plateau_drop", PLATEAU_DROP))
    residue = float(th.get("harvest_ndvi_max", HARVEST_NDVI_MAX))
    harvest = _d(rec.get("harvest_date"))
    out = {"stage": rec.get("stage"), "n_clear": len(passes), "green_up_date": None, "peak_date": None, "peak_ndvi": None,
           "peak_source": None, "plateau_days": None, "senescence_onset": None, "senescence_rate": None, "fit_points": 0,
           "days_green_up_to_peak": None, "season_length_days": None, "curve_harvest_date": None, "curve_status": "no_data",
           "observed_harvest_date": _iso(harvest), "lag_centre": None, "agreement": None, "delta_days": None,
           "ensemble_harvest_date": None}
    if not passes:
        return out
    days = [p[0] for p in passes]
    sm = median_smooth([p[1] for p in passes])

    # peak: status.json's plateau-centred peak when it has one, else the smoothed maximum (its plateau centre in time)
    pk = int(np.argmax(sm))
    lo, hi = plateau_bounds(sm, pk, drop)
    peak_date, peak_ndvi, source = _d(rec.get("peak_date")), _num(rec.get("peak_ndvi")), "status"
    if peak_date is None or peak_ndvi is None:
        peak_ndvi, source = float(sm[pk]), "series"
        peak_date = days[lo] + timedelta(days=round((days[hi] - days[lo]).days / 2))
    out.update(peak_date=_iso(peak_date), peak_ndvi=round(peak_ndvi, 3), peak_source=source,
               plateau_days=(days[hi] - days[lo]).days)

    # green-up and the season's length
    gu = green_up_date(passes)
    out["green_up_date"] = _iso(gu)
    if gu is not None:
        out["days_green_up_to_peak"] = (peak_date - gu).days
        out["season_length_days"] = ((harvest or today) - gu).days

    # senescence: onset, then the fitted decline through the last standing pass
    onset = senescence_onset_index(passes, sm, peak_date, peak_ndvi)
    if onset is None:
        out["curve_status"] = "harvested" if harvest else "no_onset"
        return out
    out["senescence_onset"] = _iso(days[onset])
    standing = [p for p in passes[onset:] if harvest is None or p[0] < harvest]
    out["fit_points"] = len(standing)
    fit = fit_decline(standing)
    if fit is None:
        out["curve_status"] = "harvested" if harvest else "few_points"
        return out
    slope, intercept, _ = fit
    # the fitted slope as it is: a crop that already dried to its standing level before the first post-onset pass
    # gives a flat, occasionally slightly positive line, and curve_status says why it is not extrapolated
    out["senescence_rate"] = round(slope, 4)
    if harvest:
        out["curve_status"] = "harvested"
        return out
    if slope >= MIN_DECLINE_SLOPE:
        out["curve_status"] = "flat"
        return out
    t_cross = (residue - intercept) / slope                  # days after the onset pass
    crossing = days[onset] + timedelta(days=round(t_cross))
    if (crossing - standing[-1][0]).days > CURVE_MAX_DAYS_AHEAD:
        out["curve_status"] = "too_far"
        return out
    out["curve_harvest_date"], out["curve_status"] = _iso(crossing), "ok"
    return out


def compare_with_window(curve, rec, today, tolerance=AGREE_TOLERANCE_DAYS):
    """Curve date against the lag model's window: lag_centre, agreement (agree / earlier / later), delta_days
    (curve minus centre, signed) and the ensemble date (mean of both). For a ready field the window starts today."""
    rec = rec or {}
    start, end = _d(rec.get("predicted_harvest_start")), _d(rec.get("predicted_harvest_end"))
    out = {"lag_centre": None, "agreement": None, "delta_days": None, "ensemble_harvest_date": None}
    if start is None or end is None:
        return out
    if rec.get("stage") == "ready" and today is not None:
        start = today
    if end < start:
        end = start
    centre = (start.toordinal() + end.toordinal()) / 2.0
    out["lag_centre"] = _from_ordinal(centre).isoformat()
    if curve is None:
        return out
    c = curve.toordinal()
    out["delta_days"] = int(round(c - centre))
    if start.toordinal() - tolerance <= c <= end.toordinal() + tolerance:
        out["agreement"] = "agree"
    else:
        out["agreement"] = "earlier" if c < start.toordinal() else "later"
    out["ensemble_harvest_date"] = _from_ordinal((c + centre) / 2.0).isoformat()
    return out


def cohorts_by_crop(fields):
    """field_id -> early / mid / late by green-up tertile within the crop; None without a green-up date or when the
    crop has fewer than COHORT_MIN_N dated fields."""
    groups = {}
    for fid, f in fields.items():
        gu = _d(f.get("green_up_date"))
        if gu is not None:
            groups.setdefault(f.get("crop"), []).append((fid, gu.toordinal()))
    out = {fid: None for fid in fields}
    for crop, rows in groups.items():
        if len(rows) < COHORT_MIN_N:
            continue
        ords = np.array([o for _, o in rows], dtype=float)
        q1, q2 = np.percentile(ords, TERTILES)
        for fid, o in rows:
            out[fid] = "early" if o <= q1 else ("late" if o > q2 else "mid")
    return out


# ----------------------------------------------------------------------------------------------- aggregates

def _summary(rows):
    """Per-crop or per-zone aggregate of per-field dicts."""
    agree = {k: sum(1 for f in rows if f.get("agreement") == k) for k in ("agree", "earlier", "later")}
    judged = sum(agree.values())
    return {
        "n": len(rows),
        "median_green_up": _median_date([_d(f.get("green_up_date")) for f in rows]),
        "median_peak_date": _median_date([_d(f.get("peak_date")) for f in rows]),
        "median_peak_ndvi": _median([f.get("peak_ndvi") for f in rows], 3),
        "median_plateau_days": _median([f.get("plateau_days") for f in rows], 1),
        "median_senescence_onset": _median_date([_d(f.get("senescence_onset")) for f in rows]),
        "median_senescence_rate": _median([f.get("senescence_rate") for f in rows], 4),
        "median_curve_harvest_date": _median_date([_d(f.get("curve_harvest_date")) for f in rows]),
        "cohorts": {**{k: sum(1 for f in rows if f.get("cohort") == k) for k in ("early", "mid", "late")},
                    "none": sum(1 for f in rows if f.get("cohort") is None)},
        "agreement": {**agree, "rate": round(agree["agree"] / judged, 3) if judged else None},
    }


def _errors(pairs):
    """{n, mae_days, bias_days} of (estimate, truth) date pairs; bias is estimate minus truth."""
    diffs = [(a - b).days for a, b in pairs if a is not None and b is not None]
    if not diffs:
        return {"n": 0, "mae_days": None, "bias_days": None}
    arr = np.array(diffs, dtype=float)
    return {"n": len(diffs), "mae_days": round(float(np.mean(np.abs(arr))), 2), "bias_days": round(float(np.mean(arr)), 2)}


def evaluate(truth, passes_by_fid, recs, crop_of, today, th, lag_by_crop, pooled_lag, notes):
    """Score, not feature: for fields harvested by today in truth.json, the curve date computed from the passes
    before truth harvest_date minus one day (as if the field were still standing) against the true harvest date,
    and the lag model's window centre (status peak_date plus the regional lag of the crop) against the same."""
    rows = []
    for fid, t in (truth.get("fields") or {}).items():
        hd = _d((t or {}).get("harvest_date"))
        if hd is None or hd > today:
            continue
        passes = passes_by_fid.get(fid) or []
        cutoff = hd - timedelta(days=1)
        early = [p for p in passes if p[0] < cutoff]
        rec = dict(recs.get(fid) or {})
        rec.pop("harvest_date", None)
        rec["stage"] = "standing"
        try:
            shape = field_shape(early, rec, cutoff, th)
        except Exception as e:  # one odd field must not sink the score
            notes.append(f"evaluation skipped {fid}: {e}")
            continue
        curve = _d(shape.get("curve_harvest_date"))
        peak = _d(shape.get("peak_date"))
        lag = lag_by_crop.get(crop_of.get(fid), pooled_lag)
        lag_centre = peak + timedelta(days=round(lag)) if (peak is not None and lag is not None) else None
        rows.append((curve, lag_centre, hd))
    paired = [(c, l, h) for c, l, h in rows if c is not None and l is not None]
    return {
        "n_harvested_in_truth": len(rows),
        "curve": _errors([(c, h) for c, _, h in rows]),
        "lag": _errors([(l, h) for _, l, h in rows]),
        "paired": {"n": len(paired),
                   "curve_mae_days": _errors([(c, h) for c, _, h in paired])["mae_days"],
                   "lag_mae_days": _errors([(l, h) for _, l, h in paired])["mae_days"]},
        "lag_days_used": {**{k: _round(v, 1) for k, v in lag_by_crop.items()}, "*": _round(pooled_lag, 1)},
        "note": ("curve date from passes before truth harvest_date minus 1 day; lag centre = status peak_date plus the "
                 "end-of-season regional lag, which the lag model learned from these same fields"),
    }


# ----------------------------------------------------------------------------------------------- entry point

def analyze(data, config, prior=None):
    """Season shape per field, cohorts, per-crop and per-zone medians, agreement with the lag model and, with
    truth.json, the harvest-date score. Never raises on bad input: the field gets None values and a note."""
    data = data or {}
    config = config or {}
    notes = []
    th = dict(config.get("thresholds") or {})
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    ts = data.get("timeseries") if isinstance(data.get("timeseries"), dict) else {}
    today = _d(config.get("as_of")) or _d(status.get("as_of"))
    if today is None:
        last = [_d(x) for x in (ts.get("dates") or [])]
        today = max([x for x in last if x], default=None)
        notes.append("no as_of in config or status: using the last time series date" if today else "no as_of and no dates")
    if not ts:
        notes.append("timeseries missing: no season shapes")

    # roster: fields.geojson, else whatever the time series knows
    feats = (data.get("fields") or {}).get("features") if isinstance(data.get("fields"), dict) else None
    roster = []
    if feats:
        for ft in feats:
            p = (ft or {}).get("properties") or {}
            if p.get("field_id"):
                roster.append((p["field_id"], p.get("crop"), p.get("zone_id")))
    else:
        notes.append("fields.geojson missing: roster taken from the time series, no crop or zone")
        roster = [(fid, None, None) for fid in (ts.get("fields") or {})]
    recs = {r.get("field_id"): r for r in (status.get("fields") or []) if isinstance(r, dict)}
    dates = ts.get("dates") or []
    series_by_fid = ts.get("fields") or {}
    zone_names = {}
    for ft in ((data.get("zones") or {}).get("features") or []) if isinstance(data.get("zones"), dict) else []:
        p = (ft or {}).get("properties") or {}
        if p.get("zone_id"):
            zone_names[p["zone_id"]] = p.get("name")

    fields, passes_by_fid = {}, {}
    for fid, crop, zone in roster:
        passes = clear_passes(dates, series_by_fid.get(fid))
        passes_by_fid[fid] = passes
        if fid not in series_by_fid:
            notes.append(f"{fid}: not in timeseries")
        rec = recs.get(fid)
        try:
            shape = field_shape(passes, rec, today, th)
            shape.update(compare_with_window(_d(shape.get("curve_harvest_date")), rec, today))
        except Exception as e:  # never lose the field: report it empty
            notes.append(f"{fid}: {e}")
            shape = field_shape([], rec, today, th)
        fields[fid] = {"crop": crop, "zone_id": zone or (rec or {}).get("zone_id"), **shape}

    for fid, cohort in cohorts_by_crop(fields).items():
        fields[fid]["cohort"] = cohort

    by_crop, by_zone = {}, {}
    for fid, f in fields.items():
        by_crop.setdefault(f.get("crop") or "unknown", []).append(f)
        by_zone.setdefault(f.get("zone_id") or "unknown", []).append(f)
    crops = {c: _summary(rows) for c, rows in sorted(by_crop.items())}
    zones = {z: {"name": zone_names.get(z), **_summary(rows)} for z, rows in sorted(by_zone.items())}
    overall = _summary(list(fields.values()))

    evaluation = None
    truth = data.get("truth")
    if isinstance(truth, dict) and truth.get("fields") and today is not None:
        pooled = _num(status.get("peak_to_harvest_days"))
        if pooled is None:
            pooled = _num(th.get("default_peak_to_harvest_days")) or float(DEFAULT_LAG_DAYS)
        lag_by_crop = {k: v for k, v in ((k, _num(v)) for k, v in (status.get("peak_to_harvest_days_by_crop") or {}).items())
                       if v is not None}
        crop_of = {fid: crop for fid, crop, _ in roster}
        try:
            evaluation = evaluate(truth, passes_by_fid, recs, crop_of, today, th, lag_by_crop, pooled, notes)
        except Exception as e:
            notes.append(f"evaluation failed: {e}")

    counts = {
        "fields": len(fields),
        "with_green_up": sum(1 for f in fields.values() if f.get("green_up_date")),
        "with_onset": sum(1 for f in fields.values() if f.get("senescence_onset")),
        "with_curve_harvest_date": sum(1 for f in fields.values() if f.get("curve_harvest_date")),
        "harvested": sum(1 for f in fields.values() if f.get("observed_harvest_date")),
        "curve_status": {k: sum(1 for f in fields.values() if f.get("curve_status") == k)
                         for k in ("ok", "harvested", "no_onset", "few_points", "flat", "too_far", "no_data")},
    }
    landscape = {"as_of": _iso(today), "counts": counts, "crops": crops, "all": overall,
                 "agreement": overall["agreement"], "evaluation": evaluation, "notes": notes}

    assumptions = {
        "green_up_ndvi": f"{GREEN_UP_NDVI}: first clear pass at or above it, crossing interpolated between passes (decide.py uses the same line for degree days)",
        "peak": "status.json plateau-centred peak when present, else the three-pass running median maximum and its plateau centre (harvest_model.py)",
        "plateau_drop": f"{th.get('peak_plateau_drop', PLATEAU_DROP)} NDVI below the peak (config thresholds.peak_plateau_drop)",
        "senescence_fraction": f"{SENESCENCE_FRACTION} of the peak marks onset; ASSUMPTION, a 10 % loss sits past plateau noise",
        "decline_fit": f"least squares on raw NDVI from onset through the last standing pass, at least {MIN_FIT_POINTS} passes",
        "min_decline_slope": f"{MIN_DECLINE_SLOPE} NDVI per day; flatter declines are not extrapolated",
        "harvest_ndvi_max": f"{th.get('harvest_ndvi_max', HARVEST_NDVI_MAX)} residue level the line is extrapolated to (config thresholds.harvest_ndvi_max)",
        "curve_max_days_ahead": f"{CURVE_MAX_DAYS_AHEAD} days past the last pass; ASSUMPTION, further is not a forecast",
        "agree_tolerance_days": f"{AGREE_TOLERANCE_DAYS} days outside the lag window still counts as agreement",
        "ready_window_start": "for stage ready the lag window starts on as_of (status.json clamps it there)",
        "cohorts": f"green-up tertiles within crop, at least {COHORT_MIN_N} dated fields (ASSUMPTION)",
        "evaluation": "truth.json fields harvested by as_of; curve from passes before truth harvest_date minus 1 day; lag centre = status peak_date + regional lag per crop",
        "prior": "not used; this module reads the time series and status.json only",
    }
    method = ("Each field's clear satellite passes are read as a season curve: the day it greened past 0.40, the top of "
              "the season and how long it stayed there, the first pass that lost a tenth of that greenness, and the slope "
              "of the decline since. Extending that slope to residue level gives a harvest date that owes nothing to the "
              "neighbours-based lag model, so the two can be checked against each other and averaged. Fields are grouped "
              "into early, mid and late green-up cohorts within their crop, and on the stand-in season both harvest "
              "estimates are scored against the known harvest dates.")
    return _plain({"key": KEY, "fields": fields, "landscape": landscape, "zones": zones, "assumptions": assumptions,
                   "method": method})
