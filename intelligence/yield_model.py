"""A relative yield index per field from the NDVI curve (module key "yield").

  green-up   the day NDVI first crosses GREEN_UP_NDVI, interpolated between the two passes around it
  integral   trapezoid area under (NDVI - BASELINE_NDVI, clipped at 0) over the clear passes, from green-up to
             min(harvest_date, as_of); greenness integrated over the season tracks biomass
  index      that integral over the median integral of the same crop across the landscape (1.00 = a typical field)
  estimate   a central Iowa reference yield scaled by the index (clipped), plus production from the field area
  band       low / typical / high by crop tertile; rank_in_zone 1 = greenest field of that crop in its zone

This is a relative, uncalibrated index. The bushel figures only scale a reference yield; turning the integral into
real yield needs yield monitor data from these fields. Every number derives from timeseries.json, status.json and
fields.geojson; nothing here reads weather, fires or vision cues. Pure functions, no I/O.

    analyze(data, config, prior=None) -> {"key": "yield", "fields": {...}, "landscape": {...}, "zones": {...},
                                          "assumptions": {...}, "method": "..."}
"""
import math
from datetime import date

import numpy as np

KEY = "yield"

GREEN_UP_NDVI = 0.40      # same line decide.py and weather_model.py use for the degree-day start (about V6 for corn)
BASELINE_NDVI = 0.20      # ASSUMPTION: bare soil and residue read 0.15 to 0.25 here; only greenness above this is crop
GAP_NOTE_DAYS = 20        # a gap between clear passes longer than this is named in the field's notes
HIGH_MIN_PASSES = 8       # confidence high: at least this many clear passes inside the window ...
HIGH_MAX_GAP_DAYS = 15    # ... and no gap longer than this
LOW_MAX_PASSES = 5        # confidence low: fewer passes than this ...
LOW_MIN_GAP_DAYS = 25     # ... or a gap longer than this
REFERENCE_YIELD = {       # ASSUMPTION: central Iowa recent county averages (USDA NASS); 56 lb/bu corn, 60 lb/bu soybean
    "corn": {"bu_per_acre": 200.0, "t_per_ha": 12.55},
    "soybean": {"bu_per_acre": 60.0, "t_per_ha": 4.03},
}
INDEX_CLIP = (0.5, 1.4)   # ASSUMPTION: the estimate never leaves half to 1.4 times the reference (NDVI saturates on dense canopies)
ACRES_PER_HA = 2.471
DEFAULT_THRESHOLDS = {"peak_ndvi_min": 0.55, "peak_plateau_drop": 0.04}   # region.json overrides both

METHOD = (
    "For every corn and soybean field we add up how green it was over its season: the area under the NDVI curve above "
    "a bare-soil line of 0.20, from the day it first crossed 0.40 (green-up) until harvest or today, using only clear "
    "satellite passes. That green-season total tracks biomass, so a field at 1.10 made about ten percent more green "
    "growth than the median field of the same crop in this landscape. This is a relative, uncalibrated index: the "
    "bushel figures only scale a typical central Iowa yield (200 bu/ac corn, 60 bu/ac soybean) by that index, clipped "
    "to 0.5 to 1.4, and real calibration needs yield monitor data from these fields. Confidence drops when clear "
    "passes are few or far apart."
)


# ---------------------------------------------------------------- small helpers

def _num(x):
    """A finite float, or None for null, NaN, inf and anything that is not a number."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _day(x):
    """date from an ISO string (a datetime is cut to its day); None when it will not parse."""
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _iso(ordinal):
    """ISO date for a (possibly fractional) proleptic ordinal, rounded to the nearest day."""
    if ordinal is None:
        return None
    return date.fromordinal(int(math.floor(float(ordinal) + 0.5))).isoformat()


def _r(x, nd=2):
    v = _num(x)
    return None if v is None else round(v, nd)


def _clean(obj):
    """Plain Python all the way down: numpy scalars become floats/ints, NaN and inf become None."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def clear_passes(dates, series):
    """[(date, ndvi)] for the clear passes of one field, sorted by date; null, NaN and unparseable entries skipped."""
    out = []
    ndvi = (series or {}).get("ndvi") or []
    for x, v in zip(dates or [], ndvi):
        d, val = _day(x), _num(v)
        if d is not None and val is not None:
            out.append((d, val))
    out.sort(key=lambda p: p[0])
    return out


# ---------------------------------------------------------------- the curve

def green_up(passes):
    """(green-up as a fractional ordinal day, known) from sorted clear passes [(date, ndvi)].
    The first crossing of GREEN_UP_NDVI, interpolated linearly between the last pass below and the first at or above.
    (None, False) when the first clear pass is already at or above (the crossing is unknown) or it is never reached;
    green_state() tells those two apart."""
    pts = [p for p in passes if p[1] is not None]
    for i, (d1, v1) in enumerate(pts):
        if v1 >= GREEN_UP_NDVI:
            if i == 0:
                return None, False
            d0, v0 = pts[i - 1]
            t = (GREEN_UP_NDVI - v0) / (v1 - v0) if v1 != v0 else 0.0
            return d0.toordinal() + t * (d1.toordinal() - d0.toordinal()), True
    return None, False


def green_state(passes):
    """How the series relates to the GREEN_UP_NDVI line: "crossed" (a pass below it precedes a pass at or above it),
    "already_green" (the first clear pass is at or above it) or "never" (no clear pass reaches it, or no passes)."""
    pts = sorted((p for p in passes if p[1] is not None), key=lambda p: p[0])
    if not pts or max(v for _, v in pts) < GREEN_UP_NDVI:
        return "never"
    return "already_green" if pts[0][1] >= GREEN_UP_NDVI else "crossed"


def curve(passes, end, plateau_drop=DEFAULT_THRESHOLDS["peak_plateau_drop"]):
    """Integral, peak and plateau of one field's NDVI curve between green-up and `end` (an ordinal day: the harvest
    date or as_of). The window never runs past the last clear pass (no extrapolation); NDVI at the window edges is
    interpolated on the pass curve, then everything below BASELINE_NDVI is clipped to zero and integrated by the
    trapezoid rule over the actual pass dates. None when there are fewer than two clear passes or the window is empty.
      start, end         ordinal days of the window
      green_up           ordinal day or None; green_up_known False means the window starts at the first clear pass
      integral           NDVI-days above the baseline
      passes             clear passes inside the window; max_gap_days the longest unobserved stretch inside it: window
                         start to the first pass inside, pass to pass, last pass inside to the window end. The edges
                         count because an interpolated green-up or a harvest cut can sit weeks from the nearest pass,
                         and that stretch is integrated on interpolated values alone.
      peak_ndvi, peak_date (ordinal of the greenest pass, first on ties), plateau_days (span of the consecutive
      passes around the peak that stay within plateau_drop of it)"""
    pts = sorted((p for p in passes if p[1] is not None), key=lambda p: p[0])
    if len(pts) < 2:
        return None
    x = np.array([p[0].toordinal() for p in pts], dtype=float)
    y = np.array([p[1] for p in pts], dtype=float)
    gu, known = green_up(pts)
    start = float(gu) if known else float(x[0])
    end = min(float(end), float(x[-1]))
    if end <= start:
        return None
    inside = (x > start) & (x < end)
    xs = np.concatenate(([start], x[inside], [end]))
    ys = np.concatenate(([np.interp(start, x, y)], y[inside], [np.interp(end, x, y)]))
    z = np.clip(ys - BASELINE_NDVI, 0.0, None)
    integral = float(np.sum((z[1:] + z[:-1]) / 2.0 * np.diff(xs)))

    win = np.where((x >= start) & (x <= end))[0]
    n = int(len(win))
    edges = np.concatenate(([start], x[win], [end]))          # the window edges bracket the passes inside
    max_gap = int(round(float(np.diff(edges).max())))
    if n:
        wy = y[win]
        pk = int(np.argmax(wy))
        lo = hi = pk
        while lo > 0 and wy[lo - 1] >= wy[pk] - plateau_drop:
            lo -= 1
        while hi < n - 1 and wy[hi + 1] >= wy[pk] - plateau_drop:
            hi += 1
        peak_ndvi, peak_date = float(wy[pk]), int(x[win[pk]])
        plateau_days = int(round(float(x[win[hi]] - x[win[lo]])))
    else:
        peak_ndvi, peak_date, plateau_days = None, None, None
    return {"start": start, "end": end, "green_up": gu if known else None, "green_up_known": known,
            "integral": integral, "passes": n, "max_gap_days": max_gap,
            "peak_ndvi": peak_ndvi, "peak_date": peak_date, "plateau_days": plateau_days}


# ---------------------------------------------------------------- rules

def confidence(n_passes, max_gap_days):
    """high: HIGH_MIN_PASSES or more clear passes in the window and no gap over HIGH_MAX_GAP_DAYS;
    low: fewer than LOW_MAX_PASSES passes or a gap over LOW_MIN_GAP_DAYS; medium otherwise."""
    gap = max_gap_days if max_gap_days is not None else 0
    if n_passes < LOW_MAX_PASSES or gap > LOW_MIN_GAP_DAYS:
        return "low"
    if n_passes >= HIGH_MIN_PASSES and gap <= HIGH_MAX_GAP_DAYS:
        return "high"
    return "medium"


def estimate(crop, index, area_ha):
    """Reference yield scaled by the clipped index, and production over the field area. None for a crop without a
    reference (other) or without an index."""
    ref = REFERENCE_YIELD.get((crop or "").lower())
    k = _num(index)
    if ref is None or k is None:
        return None
    k = min(max(k, INDEX_CLIP[0]), INDEX_CLIP[1])
    bu, t = ref["bu_per_acre"] * k, ref["t_per_ha"] * k
    area = _num(area_ha)
    return {"bu_per_acre": bu, "t_per_ha": t,
            "production_bu": None if area is None else bu * area * ACRES_PER_HA,
            "production_t": None if area is None else t * area}


def band_cuts(indices):
    """(lower, upper) tertile cuts of the index within a crop; None when there is nothing to cut."""
    vals = [v for v in (_num(i) for i in indices) if v is not None]
    if not vals:
        return None
    lo, hi = np.percentile(np.array(vals, dtype=float), [100.0 / 3.0, 200.0 / 3.0])
    return float(lo), float(hi)


def band(index, cuts):
    """low below the lower tertile cut, high above the upper one, typical between (and when cuts coincide)."""
    v = _num(index)
    if v is None or cuts is None:
        return None
    if v < cuts[0]:
        return "low"
    if v > cuts[1]:
        return "high"
    return "typical"


def ranks(index_by_field):
    """Competition rank, 1 = highest index; equal indices share a rank (1, 1, 3). Fields without an index are left out."""
    scored = {k: _num(v) for k, v in index_by_field.items() if _num(v) is not None}
    return {k: 1 + sum(1 for o in scored.values() if o > v) for k, v in scored.items()}


# ---------------------------------------------------------------- analyze

def _skip(crop, note, zone=None):
    return {"index": None, "crop": crop, "zone_id": zone, "note": note}


def analyze(data, config, prior=None):
    """The module contract. data holds the parsed files (fields, timeseries, status at least; the rest is unused here),
    config is region.json; prior is accepted for the contract and not needed. Never raises on bad input: a field
    that cannot be scored gets {"index": None, "note": ...} and the reason lands in landscape["notes"]."""
    data = data or {}
    config = config or {}
    th = {**DEFAULT_THRESHOLDS, **{k: v for k, v in (config.get("thresholds") or {}).items() if _num(v) is not None}}
    peak_min, plateau_drop = float(th["peak_ndvi_min"]), float(th["peak_plateau_drop"])
    notes = []
    assumptions = {
        "green_up_ndvi": f"{GREEN_UP_NDVI}: first clear pass at or above this is green-up, interpolated from the pass before; the same line the degree-day start uses (about V6 for corn)",
        "baseline_ndvi": f"{BASELINE_NDVI} ASSUMPTION: bare soil and residue read 0.15 to 0.25 in this landscape, so only greenness above this counts as crop",
        "window_end": "min(harvest_date from status.json, as_of), never past the last clear pass; no extrapolation. A field not harvested by then is noted: its integral may still grow",
        "reference_yield_corn": "200 bu/ac = 12.55 t/ha ASSUMPTION: central Iowa recent county averages (USDA NASS), 56 lb/bu",
        "reference_yield_soybean": "60 bu/ac = 4.03 t/ha ASSUMPTION: central Iowa recent county averages (USDA NASS), 60 lb/bu",
        "index_clip": f"{INDEX_CLIP[0]} to {INDEX_CLIP[1]} ASSUMPTION: the estimate stays within this range of the reference because the NDVI integral saturates on dense canopies",
        "acres_per_ha": f"{ACRES_PER_HA}",
        "peak_ndvi_min": f"{peak_min} from region.json thresholds: a field whose peak NDVI stays under it (in status.json or in its own clear passes) has no crop-level peak and is not scored; a series that never reaches {GREEN_UP_NDVI} is not scored either",
        "peak_plateau_drop": f"{plateau_drop} from region.json thresholds: consecutive passes within this of the peak form the plateau",
        "confidence": f"high = {HIGH_MIN_PASSES}+ clear passes in the window and no gap over {HIGH_MAX_GAP_DAYS} days; low = under {LOW_MAX_PASSES} passes or a gap over {LOW_MIN_GAP_DAYS} days; gaps over {GAP_NOTE_DAYS} days are noted. A gap is any unobserved stretch inside the window, the stretch from an interpolated green-up or to a harvest cut included",
        "bands": "low / typical / high = bottom, middle and top third of the index within the crop across the landscape",
        "rank_in_zone": "1 = highest index among fields of the same crop in the same zone; ties share a rank",
    }

    def result(fields, landscape, zones):
        landscape = {"as_of": config.get("as_of"), **landscape, "notes": notes}
        return _clean({"key": KEY, "fields": fields, "landscape": landscape, "zones": zones,
                       "assumptions": assumptions, "method": METHOD})

    feats = ((data.get("fields") or {}).get("features") if isinstance(data.get("fields"), dict) else None) or []
    if not feats:
        notes.append("fields.geojson missing or empty: nothing to score")
        return result({}, {"crops": {}}, {})

    ts = data.get("timeseries") if isinstance(data.get("timeseries"), dict) else None
    ts_dates = list((ts or {}).get("dates") or [])
    ts_fields = (ts or {}).get("fields") or {}
    if not ts_dates or not ts_fields:
        notes.append("timeseries.json missing or empty: no NDVI curves, every field unscored")
    today = _day(config.get("as_of"))
    if today is None:
        last = [d for d in (_day(x) for x in ts_dates) if d is not None]
        today = max(last) if last else None
        notes.append("as_of missing from config: " + (f"window ends at the last pass {today.isoformat()}" if today else "no date to end the window"))

    status = data.get("status") if isinstance(data.get("status"), dict) else None
    recs = {r.get("field_id"): r for r in (status or {}).get("fields") or [] if isinstance(r, dict)}
    if status is None:
        notes.append("status.json missing: peaks judged from the raw passes and no harvest date cuts the window")

    zones = {}
    zdoc = data.get("zones") if isinstance(data.get("zones"), dict) else None
    for zf in (zdoc or {}).get("features") or []:
        zid = (zf.get("properties") or {}).get("zone_id")
        if zid:
            zones[str(zid)] = {}

    # pass 1: the curve of every field that can have one
    fields, scored, skipped = {}, {}, {}
    for feat in feats:
        p = feat.get("properties") or {}
        fid = p.get("field_id")
        if not fid:
            continue
        crop = str(p.get("crop") or "other").lower()
        zone = p.get("zone_id")
        if zone is not None:
            zones.setdefault(str(zone), {})
        if crop not in REFERENCE_YIELD:
            fields[fid] = _skip(crop, f"crop {crop}: no reference yield, not scored", zone)
            skipped["other"] = skipped.get("other", 0) + 1
            continue
        passes = clear_passes(ts_dates, ts_fields.get(fid)) if ts_fields else []
        if not passes:
            fields[fid] = _skip(crop, "no clear passes in the timeseries", zone)
            skipped["no_passes"] = skipped.get("no_passes", 0) + 1
            continue
        if len(passes) < 2:
            fields[fid] = _skip(crop, "only one clear pass: no curve to integrate", zone)
            skipped["no_passes"] = skipped.get("no_passes", 0) + 1
            continue
        rec = recs.get(fid) or {}
        peak_seen = max(v for _, v in passes)                 # the greenest clear pass in the series itself
        if green_state(passes) == "never":
            fields[fid] = _skip(crop, f"never green: no clear pass reaches NDVI {GREEN_UP_NDVI:.2f} (highest {peak_seen:.2f}), not scored", zone)
            skipped["never_green"] = skipped.get("never_green", 0) + 1
            continue
        # the peak gate needs both status.json (when it has a peak) and the passes themselves to clear the line,
        # so a stale status peak cannot score a series that never grew a canopy
        peak_ref = _num(rec.get("peak_ndvi"))
        if peak_ref is not None and peak_ref < peak_min:
            fields[fid] = _skip(crop, f"no crop-level peak (NDVI {peak_ref:.2f} under {peak_min:.2f}), not scored", zone)
            skipped["no_peak"] = skipped.get("no_peak", 0) + 1
            continue
        if peak_seen < peak_min:
            said = "" if peak_ref is None else f"; status.json says {peak_ref:.2f}"
            fields[fid] = _skip(crop, f"no crop-level peak in the clear passes (highest NDVI {peak_seen:.2f} under {peak_min:.2f}{said}), not scored", zone)
            skipped["no_peak"] = skipped.get("no_peak", 0) + 1
            continue
        if today is None:
            fields[fid] = _skip(crop, "no as_of date to end the window", zone)
            skipped["no_window"] = skipped.get("no_window", 0) + 1
            continue
        end = today
        harvest = _day(rec.get("harvest_date"))
        if harvest is not None and harvest < end:
            end = harvest
        c = curve(passes, end.toordinal(), plateau_drop)
        if c is None:
            fields[fid] = _skip(crop, f"window ends ({end.isoformat()}) before the curve starts, nothing to integrate", zone)
            skipped["no_window"] = skipped.get("no_window", 0) + 1
            continue
        field_notes = []
        if not c["green_up_known"]:
            # green_state() already ruled out "never", so the window starts at a first pass that is already green
            d0, v0 = passes[0]
            field_notes.append(f"green-up unknown: the first clear pass ({d0.isoformat()}, NDVI {v0:.2f}) was already green, the integral starts there")
        if c["max_gap_days"] > GAP_NOTE_DAYS:
            field_notes.append(f"gap of {c['max_gap_days']} days without a clear pass inside the window")
        if harvest is None or harvest > end:
            field_notes.append(f"not harvested by {end.isoformat()}: the window ends at {_iso(c['end'])} and the integral may still grow")
        scored[fid] = {"crop": crop, "zone": zone, "area_ha": _num(p.get("area_ha")), "curve": c, "notes": field_notes}

    # pass 2: crop medians -> index, bands, zone ranks
    reference = {}
    for crop in REFERENCE_YIELD:
        vals = [s["curve"]["integral"] for s in scored.values() if s["crop"] == crop]
        if vals:
            reference[crop] = float(np.median(np.array(vals, dtype=float)))
    index_of = {}
    for fid, s_ in scored.items():
        med = reference.get(s_["crop"])
        if not med or med <= 0:
            s_["notes"].append("no reference: the crop median integral is zero")
            index_of[fid] = None
        else:
            index_of[fid] = round(s_["curve"]["integral"] / med, 2)
    cuts = {crop: band_cuts([index_of[f] for f, s_ in scored.items() if s_["crop"] == crop]) for crop in reference}
    rank_of, peers_of = {}, {}
    groups = {}
    for fid, s_ in scored.items():
        groups.setdefault((str(s_["zone"]), s_["crop"]), {})[fid] = index_of[fid]
    for members in groups.values():
        r = ranks(members)
        rank_of.update(r)
        peers_of.update({f: len(r) for f in r})

    # per-field output, landscape and zone totals
    crops_out, zones_tot = {}, {}
    for fid, s_ in scored.items():
        c, crop, idx = s_["curve"], s_["crop"], index_of[fid]
        est = estimate(crop, idx, s_["area_ha"])
        conf = confidence(c["passes"], c["max_gap_days"])
        fields[fid] = {
            "crop": crop, "zone_id": s_["zone"], "area_ha": _r(s_["area_ha"], 2),
            "green_up": _iso(c["green_up"]), "green_up_known": bool(c["green_up_known"]),
            "window": {"start": _iso(c["start"]), "end": _iso(c["end"])},
            "passes": c["passes"], "max_gap_days": c["max_gap_days"],
            "integral": _r(c["integral"], 2), "peak_ndvi": _r(c["peak_ndvi"], 3), "peak_date": _iso(c["peak_date"]),
            "plateau_days": c["plateau_days"],
            "index": idx, "band": band(idx, cuts.get(crop)), "rank_in_zone": rank_of.get(fid), "zone_peers": peers_of.get(fid),
            "estimate": None if est is None else {"bu_per_acre": _r(est["bu_per_acre"], 1), "t_per_ha": _r(est["t_per_ha"], 2)},
            "production": None if est is None else {"bu": _r(est["production_bu"], 0), "t": _r(est["production_t"], 1)},
            "confidence": conf, "notes": s_["notes"],
        }
        if idx is None:
            continue
        agg = crops_out.setdefault(crop, {"fields": 0, "ha": 0.0, "indices": [], "production_bu": 0.0, "production_t": 0.0,
                                          "bands": {"low": 0, "typical": 0, "high": 0},
                                          "confidence": {"high": 0, "medium": 0, "low": 0}})
        agg["fields"] += 1
        agg["indices"].append(idx)
        agg["bands"][fields[fid]["band"]] += 1
        agg["confidence"][conf] += 1
        if s_["area_ha"] is not None:
            agg["ha"] += s_["area_ha"]
        if est and est["production_bu"] is not None:
            agg["production_bu"] += est["production_bu"]
            agg["production_t"] += est["production_t"]
        zt = zones_tot.setdefault(str(s_["zone"]), {}).setdefault(crop, {"fields": 0, "ha": 0.0, "indices": [],
                                                                             "production_bu": 0.0, "production_t": 0.0})
        zt["fields"] += 1
        zt["indices"].append(idx)
        if s_["area_ha"] is not None:
            zt["ha"] += s_["area_ha"]
        if est and est["production_bu"] is not None:
            zt["production_bu"] += est["production_bu"]
            zt["production_t"] += est["production_t"]

    # calibration to the county model (yield_train.py) and soil/terrain pass-through (field_env.py), when present:
    # a field's bushels are the county estimate times its relative index, the county's 90% band scaled the same way
    ym = data.get("yield_model") if isinstance(data.get("yield_model"), dict) else None
    county = {}
    for crop_name, c in ((ym or {}).get("crops") or {}).items():
        if not isinstance(c, dict) or "error" in c or _num(c.get("estimate_bu_per_acre")) is None:
            continue
        iv = c.get("interval_bu_per_acre")
        iv = [float(iv[0]), float(iv[1])] if isinstance(iv, list) and len(iv) == 2 and all(_num(v) is not None for v in iv) else None
        county[str(crop_name).lower()] = {
            "county": c.get("county"), "county_estimate_bu_per_acre": _r(float(c["estimate_bu_per_acre"]), 1),
            "interval_bu_per_acre": [_r(iv[0], 1), _r(iv[1], 1)] if iv else None,
            "rmse_bu_per_acre": _num(c.get("rmse_bu_per_acre")), "years": c.get("years"),
            "source": ((ym or {}).get("sources") or [None])[0]}
    env = data.get("field_env") if isinstance(data.get("field_env"), dict) else None
    env_fields = env.get("fields") if env and isinstance(env.get("fields"), dict) else {}
    calibrated_sum = {}
    for fid, f in fields.items():
        if f.get("index") is None:
            continue
        cal = county.get(f["crop"])
        if cal:
            f["bu_per_acre_calibrated"] = _r(cal["county_estimate_bu_per_acre"] * f["index"], 1)
            if cal["interval_bu_per_acre"]:
                f["interval_bu_per_acre"] = [_r(cal["interval_bu_per_acre"][0] * f["index"], 1), _r(cal["interval_bu_per_acre"][1] * f["index"], 1)]
            f["calibration"] = "county model"
            s_ = calibrated_sum.setdefault(f["crop"], [0.0, 0])
            s_[0] += f["bu_per_acre_calibrated"]
            s_[1] += 1
        else:
            f["calibration"] = "reference only"
        e = env_fields.get(fid)
        if isinstance(e, dict):
            f["soil"] = {k: e.get(k) for k in ("texture", "soc_g_kg", "clay_pct", "sand_pct", "ph")}
            f["terrain"] = {k: e.get(k) for k in ("elevation_m", "slope_pct", "aspect")}
            f["env_note"] = e.get("note")
        else:
            f["soil"] = None
            f["terrain"] = None

    crops = {}
    for crop, a in crops_out.items():
        arr = np.array(a["indices"], dtype=float)
        acres = a["ha"] * ACRES_PER_HA
        crops[crop] = {
            "fields": a["fields"], "ha": _r(a["ha"], 1),
            "median_index": _r(np.median(arr), 2), "p10_index": _r(np.percentile(arr, 10), 2), "p90_index": _r(np.percentile(arr, 90), 2),
            "production_bu": _r(a["production_bu"], 0), "production_t": _r(a["production_t"], 1),
            "mean_bu_per_acre": _r(a["production_bu"] / acres, 1) if acres > 0 else None,
            "mean_t_per_ha": _r(a["production_t"] / a["ha"], 2) if a["ha"] > 0 else None,
            "bands": a["bands"], "confidence": a["confidence"],
        }
    for zid, per_crop in zones_tot.items():
        for crop, zt in per_crop.items():
            zones.setdefault(zid, {})[crop] = {
                "fields": zt["fields"], "ha": _r(zt["ha"], 1),
                "mean_index": _r(float(np.mean(zt["indices"])), 2),
                "production_bu": _r(zt["production_bu"], 0), "production_t": _r(zt["production_t"], 1),
            }
    if skipped:
        notes.append("unscored fields: " + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())))
    for crop in REFERENCE_YIELD:
        if crop not in reference and any((f.get("properties") or {}).get("crop") == crop for f in feats):
            notes.append(f"no {crop} field could be scored, so no {crop} reference")
    for crop_name, (tot, n) in calibrated_sum.items():
        if crop_name in crops and n:
            crops[crop_name]["calibrated_mean_bu_per_acre"] = _r(tot / n, 1)
    landscape = {"crops": crops, "calibration": county, "scored": len([f for f in fields.values() if f.get("index") is not None]),
                 "skipped": skipped, "reference_integral": {k: _r(v, 2) for k, v in reference.items()},
                 "band_cuts": {k: None if v is None else [_r(v[0], 3), _r(v[1], 3)] for k, v in cuts.items()}}
    return result(fields, landscape, zones)
