"""Root-zone water balance per field: FAO-56 reference evapotranspiration (Hargreaves-Samani) and a daily soil
water bucket driven by the one weather station and each field's own satellite greenness.

  latitude + day of year            -> extraterrestrial radiation Ra            (FAO-56 eq. 21-25)
  Ra + daily Tmax / Tmin            -> reference ET0, mm/day                   (FAO-56 eq. 52, Hargreaves)
  NDVI between clear passes         -> crop coefficient Kc; residue after harvest
  rain in, Kc * ET0 out             -> depletion of a 150 mm bucket, full at season start; excess drains
  depletion past half the bucket    -> a stress day while the crop stands; actual ET shrinks (FAO-56 eq. 84)

Outputs per field: season ET0 / ETc / rain, depletion and available fraction this morning, stress days, a
wet / adequate / dry label, the last rain of 5 mm or more and whether there has been enough ET since to dry the
surface (a rough cue from the one station, not a trafficability check). The bucket is an estimate on top of the
files, not a measurement: every number here derives from weather.json, timeseries.json and status.json, and when
the rain record is missing or mostly blank no field is balanced at all.

    analyze(data, config, prior=None) -> {"key": "water", "fields", "landscape", "zones", "assumptions", "method"}
"""
import math
from datetime import date, timedelta

import numpy as np

KEY = "water"

# --- bucket -------------------------------------------------------------------------------------------------------
TAW_MM = 150.0            # ASSUMPTION: total available water of 1 m of Iowa silt loam (about 0.15 mm per mm; FAO-56 table 19 gives 0.13-0.19 for silt loam)
RAW_FRACTION = 0.5        # readily available water = p * TAW; FAO-56 table 22 depletion fraction p: maize 0.55, soybean 0.50
START_DEPLETION_MM = 0.0  # ASSUMPTION: spring recharge leaves the profile full at season_start (Iowa June)
WET_FRACTION = 0.2        # ASSUMPTION: depletion under 0.2 * TAW reads "wet"; past RAW reads "dry"; between is "adequate"
KS_REDUCES_ET = True      # once depletion passes RAW the crop cannot keep up: ETc * Ks, Ks = (TAW - D) / (TAW - RAW) (FAO-56 eq. 84)

# --- crop coefficient ---------------------------------------------------------------------------------------------
KC_SLOPE = 1.25           # ASSUMPTION: Kc = clip(1.25 * NDVI - 0.10, 0.15, 1.20); a simplified linear NDVI-Kc relation in the
KC_INTERCEPT = -0.10      #   range of published ones (Kamble et al. 2013, Remote Sensing 5: Kc = 1.457 NDVI - 0.1725; Hunsaker 2003)
KC_MIN = 0.15             #   bare soil floor; FAO-56 Kc ini for row crops 0.15-0.30
KC_MAX = 1.20             #   FAO-56 table 12 Kc mid for maize 1.20, soybean 1.15
KC_RESIDUE = 0.20         # ASSUMPTION: residue after the combine; FAO-56 Kc end for maize harvested dry is 0.35, residue cover lowers it

# --- rain memory --------------------------------------------------------------------------------------------------
RAIN_EVENT_MM = 5.0       # a rain that counts for last_rain_date and the surface cue (same line as weather_model.SHOWER_MM)
RAIN_KNOWN_MIN_FRACTION = 0.5  # ASSUMPTION: the pipeline writes None for NASA POWER fill values; a few blank days are read as 0 mm and
                               #   counted, but when fewer than half the recorded days carry a rain figure (or the column is missing)
                               #   the bucket cannot be filled honestly, so no field is balanced and rain is reported as unknown

# --- Hargreaves-Samani (FAO-56 eq. 52) and Ra (FAO-56 eq. 21-25) --------------------------------------------------
HARGREAVES_COEF = 0.0023
HARGREAVES_OFFSET_C = 17.8
MJ_TO_MM = 0.408          # 1 MJ m-2 d-1 of radiation evaporates 0.408 mm of water (FAO-56 eq. 20)
SOLAR_CONSTANT = 0.0820   # MJ m-2 min-1
FORECAST_DAYS = 14
MISSING_DAYS_NOTE_MIN = 1


# ================================================================================================= FAO-56 math

def extraterrestrial_radiation(lat_deg, doy):
    """Ra in MJ m-2 d-1 for a latitude (decimal degrees, south negative) and day of year (scalar or array).
    FAO-56: eq. 22 phi, eq. 23 dr, eq. 24 delta, eq. 25 ws, eq. 21 Ra."""
    j = np.asarray(doy, dtype=float)
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * np.cos(2.0 * math.pi * j / 365.0)
    delta = 0.409 * np.sin(2.0 * math.pi * j / 365.0 - 1.39)
    x = np.clip(-math.tan(phi) * np.tan(delta), -1.0, 1.0)      # polar day / night clamp
    ws = np.arccos(x)
    ra = (24.0 * 60.0 / math.pi) * SOLAR_CONSTANT * dr * (
        ws * math.sin(phi) * np.sin(delta) + math.cos(phi) * np.cos(delta) * np.sin(ws))
    ra = np.maximum(ra, 0.0)
    return float(ra) if ra.ndim == 0 else ra


def et0_hargreaves(ra, tmax, tmin):
    """Reference ET in mm/day, FAO-56 eq. 52: 0.0023 * 0.408 * Ra * (Tmean + 17.8) * sqrt(Tmax - Tmin).
    Works on scalars or arrays; a zero or inverted range gives 0, never NaN."""
    ra, tmax, tmin = (np.asarray(v, dtype=float) for v in (ra, tmax, tmin))
    tmean = (tmax + tmin) / 2.0
    span = np.sqrt(np.maximum(tmax - tmin, 0.0))
    out = HARGREAVES_COEF * MJ_TO_MM * ra * np.maximum(tmean + HARGREAVES_OFFSET_C, 0.0) * span
    out = np.maximum(out, 0.0)
    return float(out) if out.ndim == 0 else out


def kc_from_ndvi(ndvi):
    """Crop coefficient from NDVI: clip(KC_SLOPE * NDVI + KC_INTERCEPT, KC_MIN, KC_MAX)."""
    out = np.clip(KC_SLOPE * np.asarray(ndvi, dtype=float) + KC_INTERCEPT, KC_MIN, KC_MAX)
    return float(out) if out.ndim == 0 else out


def run_bucket(et0, precip, kc, standing, taw=TAW_MM, raw_fraction=RAW_FRACTION, start_depletion=START_DEPLETION_MM):
    """Daily root-zone depletion for many fields at once (FAO-56 eq. 85 without irrigation, runoff or capillary rise).
    et0, precip: (n_days,); kc, standing: (n_fields, n_days). Each day: Ks from the depletion at the start of the
    day (eq. 84, 1 until depletion passes RAW), ETc = Ks * Kc * ET0 but never more than the water left, then
    D = D - P + ETc; a negative D is water the bucket cannot hold and drains; D never exceeds TAW.
    Returns end-of-day depletion, etc, drained (n_fields, n_days) and stress (bool: D > RAW while standing)."""
    et0 = np.asarray(et0, dtype=float)
    precip = np.asarray(precip, dtype=float)
    kc = np.atleast_2d(np.asarray(kc, dtype=float))
    standing = np.atleast_2d(np.asarray(standing, dtype=bool))
    n_f, n_d = kc.shape
    raw = raw_fraction * taw
    dep = np.full(n_f, float(start_depletion))
    depletion = np.zeros((n_f, n_d))
    etc = np.zeros((n_f, n_d))
    drained = np.zeros((n_f, n_d))
    for t in range(n_d):
        if KS_REDUCES_ET and taw > raw:
            ks = np.clip((taw - dep) / (taw - raw), 0.0, 1.0)
        else:
            ks = np.ones(n_f)
        want = ks * kc[:, t] * et0[t]
        left = np.maximum(taw - dep + precip[t], 0.0)            # water the crop can still take today
        e = np.minimum(want, left)
        d_new = dep - precip[t] + e
        drain = np.maximum(-d_new, 0.0)
        dep = np.clip(d_new, 0.0, taw)
        depletion[:, t] = dep
        etc[:, t] = e
        drained[:, t] = drain
    stress = (depletion > raw) & standing
    return {"depletion": depletion, "etc": etc, "drained": drained, "stress": stress}


# ================================================================================================= helpers

def _iso_date(x):
    """A date from an ISO string (datetime strings cut to the day); None when it is not one."""
    if isinstance(x, date):
        return x
    if not isinstance(x, str) or len(x) < 10:
        return None
    try:
        return date.fromisoformat(x[:10])
    except ValueError:
        return None


def _num(x):
    """A finite float or None."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, nd=1):
    v = _num(x)
    return None if v is None else round(v, nd)


def _features(fc):
    feats = (fc or {}).get("features") if isinstance(fc, dict) else None
    return [f for f in feats if isinstance(f, dict)] if isinstance(feats, list) else []


def _latitude(data, config, notes):
    """Latitude of the box centre: config bbox, else the mean zone centroid, else the mean field vertex, else None."""
    bbox = (config or {}).get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(_num(v) is not None for v in bbox):
        return (float(bbox[1]) + float(bbox[3])) / 2.0
    lats = []
    for f in _features((data or {}).get("zones")):
        c = (f.get("properties") or {}).get("centroid")
        if isinstance(c, (list, tuple)) and len(c) >= 2 and _num(c[1]) is not None:
            lats.append(float(c[1]))
    if lats:
        notes.append("latitude taken from the zone centroids (no bbox in config)")
        return float(np.mean(lats))
    for f in _features((data or {}).get("fields")):
        try:
            ring = f["geometry"]["coordinates"][0]
            lats.extend(float(p[1]) for p in ring if _num(p[1]) is not None)
        except (KeyError, TypeError, IndexError):
            continue
    if lats:
        notes.append("latitude taken from the field polygons (no bbox in config)")
        return float(np.mean(lats))
    return None


def _series(block, key, n):
    """One array of a weather block as a list of length n, padded with None or cut, aligned with block["dates"]."""
    vals = block.get(key)
    if not isinstance(vals, list):
        return [None] * n
    return (list(vals) + [None] * n)[:n]


def _daily_weather(obs, season_start, today, notes):
    """Observed weather on the daily grid season_start .. today - 1: precip, tmax, tmin (None -> NaN).
    Returns (days, precip, tmax, tmin, rain_known) or None when the record does not cover the season at all.
    rain_known is False when the precip_mm column is missing or fewer than RAIN_KNOWN_MIN_FRACTION of the recorded
    days carry a rain figure; otherwise blank rain days are read as 0 mm and counted in a note."""
    n = (today - season_start).days
    if n <= 0:
        notes.append(f"as_of {today.isoformat()} is not after season_start {season_start.isoformat()}: no season to balance")
        return None
    dates = obs.get("dates") if isinstance(obs, dict) else None
    if not isinstance(dates, list) or not dates:
        notes.append("no observed weather (weather.json observed.dates missing): no water balance")
        return None
    m = len(dates)
    has_rain_column = isinstance(obs.get("precip_mm"), list)
    pr, hi, lo = _series(obs, "precip_mm", m), _series(obs, "tmax_c", m), _series(obs, "tmin_c", m)
    precip = np.full(n, np.nan)
    tmax = np.full(n, np.nan)
    tmin = np.full(n, np.nan)
    seen = np.zeros(n, dtype=bool)
    for i, x in enumerate(dates):
        dt = _iso_date(x)
        if dt is None:
            continue
        k = (dt - season_start).days
        if 0 <= k < n:
            seen[k] = True
            p = _num(pr[i])
            if p is not None:
                precip[k] = max(p, 0.0)
            h, l = _num(hi[i]), _num(lo[i])
            tmax[k] = h if h is not None else np.nan
            tmin[k] = l if l is not None else np.nan
    if not seen.any():
        notes.append(f"observed weather does not cover {season_start.isoformat()} to {(today - timedelta(days=1)).isoformat()}: no water balance")
        return None
    missing = int((~seen).sum())
    if missing >= MISSING_DAYS_NOTE_MIN:
        notes.append(f"{missing} of {n} season days have no weather record: rain read as 0, ET0 interpolated")
    recorded = int(seen.sum())
    rain_days = int(np.isfinite(precip).sum())
    blank = recorded - rain_days
    if not has_rain_column:
        notes.append("observed weather has no precip_mm column: rain unknown, no water balance")
        rain_known = False
    elif rain_days < RAIN_KNOWN_MIN_FRACTION * recorded:
        notes.append(f"{blank} of {recorded} recorded days have no rain figure: rain unknown, no water balance")
        rain_known = False
    else:
        if blank:
            notes.append(f"{blank} of {recorded} recorded days have no rain figure: read as 0 mm, so season rain is understated")
        rain_known = True
    precip = np.where(np.isfinite(precip), precip, 0.0)
    days = [season_start + timedelta(days=i) for i in range(n)]
    return days, precip, tmax, tmin, rain_known


def _et0_daily(lat, days, tmax, tmin, notes):
    """ET0 on the grid; days without both temperatures are filled by linear interpolation between known days
    (edges held). None when no day has temperatures."""
    doy = np.array([d.timetuple().tm_yday for d in days], dtype=float)
    ra = extraterrestrial_radiation(lat, doy)
    ok = np.isfinite(tmax) & np.isfinite(tmin)
    if not ok.any():
        notes.append("observed weather has no temperatures: ET0 cannot be computed")
        return None
    et0 = np.zeros(len(days))
    et0[ok] = et0_hargreaves(ra[ok], tmax[ok], tmin[ok])
    if not ok.all():
        idx = np.arange(len(days))
        et0[~ok] = np.interp(idx[~ok], idx[ok], et0[ok])
        gaps = int((~ok).sum())
        notes.append(f"{gaps} season day{'s' if gaps != 1 else ''} without temperatures: ET0 interpolated")
    return et0


def _forecast_et0(fc, lat, today, notes):
    """Sum of ET0 and rain over the next FORECAST_DAYS forecast days from today, when the forecast carries
    temperatures; None otherwise."""
    if not isinstance(fc, dict) or not isinstance(fc.get("dates"), list) or not fc["dates"]:
        notes.append("no forecast block: no forecast ET0")
        return None
    m = len(fc["dates"])
    hi, lo, pr = _series(fc, "tmax_c", m), _series(fc, "tmin_c", m), _series(fc, "precip_mm", m)
    rows = []
    for i, x in enumerate(fc["dates"]):
        dt = _iso_date(x)
        if dt is None or dt < today or (dt - today).days >= FORECAST_DAYS:
            continue
        rows.append((dt, _num(hi[i]), _num(lo[i]), _num(pr[i])))
    rows.sort()
    if not rows:
        notes.append("forecast has no days from as_of onward: no forecast ET0 (refresh weather.json)")
        return None
    with_t = [r for r in rows if r[1] is not None and r[2] is not None]
    with_p = [r for r in rows if r[3] is not None]
    if with_p:
        rain = float(sum(max(r[3], 0.0) for r in with_p))
        if len(with_p) < len(rows):
            notes.append(f"forecast rain covers {len(with_p)} of {len(rows)} days (no figure on the rest)")
    else:
        rain = None
        notes.append("forecast has no rain figures (precip_mm missing): forecast rain unknown")
    base = {"start": rows[0][0].isoformat(), "end": rows[-1][0].isoformat(), "days": len(rows)}
    if not with_t:
        notes.append("forecast has no temperatures: forecast ET0 unknown")
        return {**base, "et0_mm": None, "precip_mm": _r(rain), "net_mm": None, "precip_days": len(with_p)}
    doy = np.array([r[0].timetuple().tm_yday for r in with_t], dtype=float)
    et0 = et0_hargreaves(extraterrestrial_radiation(lat, doy), [r[1] for r in with_t], [r[2] for r in with_t])
    total = float(np.sum(et0))
    if len(with_t) < len(rows):
        notes.append(f"forecast ET0 covers {len(with_t)} of {len(rows)} days (temperatures missing on the rest)")
    return {**base, "et0_mm": round(total, 1), "precip_mm": _r(rain),
            "net_mm": round(rain - total, 1) if rain is not None else None,
            "et0_days": len(with_t), "precip_days": len(with_p)}


def _clear_passes(ts_dates, series, season_start):
    """[(day offset from season_start, ndvi)] for the clear passes of one field, sorted, one per day."""
    nd = series.get("ndvi") if isinstance(series, dict) else None
    if not isinstance(nd, list):
        return []
    by_day = {}
    for x, v in zip(ts_dates, nd):
        dt = _iso_date(x)
        val = _num(v)
        if dt is None or val is None:
            continue
        by_day[(dt - season_start).days] = val
    return sorted(by_day.items())


def _rain_memory(days, precip, et0, today):
    """Last rain of RAIN_EVENT_MM or more, days since it, and whether ET0 since then exceeds that rain."""
    hits = np.flatnonzero(precip >= RAIN_EVENT_MM)
    if hits.size == 0:
        after = float(np.sum(et0)) if et0 is not None else 0.0
        return None, None, bool(after > 0.0)
    k = int(hits[-1])
    last = days[k]
    after = float(np.sum(et0[k + 1:])) if et0 is not None else 0.0
    return last, (today - last).days, bool(after > float(precip[k]))


def _status_word(depletion, taw=TAW_MM, raw_fraction=RAW_FRACTION):
    if depletion < WET_FRACTION * taw:
        return "wet"
    if depletion > raw_fraction * taw:
        return "dry"
    return "adequate"


def _assumptions():
    return {
        "taw_mm": f"{TAW_MM:.0f} mm total available water in 1 m of silt loam (ASSUMPTION for Story County; FAO-56 table 19 silt loam 0.13-0.19 mm/mm)",
        "raw_fraction": f"{RAW_FRACTION} of TAW is readily available before the crop is short (FAO-56 table 22: maize 0.55, soybean 0.50)",
        "start_depletion_mm": f"{START_DEPLETION_MM:.0f} mm: the profile is full at season_start after spring recharge (ASSUMPTION)",
        "kc_from_ndvi": f"Kc = clip({KC_SLOPE} * NDVI {KC_INTERCEPT:+.2f}, {KC_MIN}, {KC_MAX}), NDVI interpolated between clear passes (ASSUMPTION; shape after Kamble et al. 2013)",
        "kc_residue": f"{KC_RESIDUE} after the harvest date in status.json (ASSUMPTION; FAO-56 Kc end for dry-harvested maize is 0.35)",
        "ks": "actual ET = Ks * Kc * ET0 once depletion passes RAW, Ks = (TAW - D) / (TAW - RAW) (FAO-56 eq. 84)",
        "et0": "Hargreaves-Samani, FAO-56 eq. 52, with Ra from the bbox centre latitude and day of year (FAO-56 eq. 21-25); one station for the whole box",
        "water_status": f"wet when depletion < {WET_FRACTION} TAW, dry when depletion > RAW, adequate between (ASSUMPTION)",
        "stress_day": "end-of-day depletion above RAW on a day the crop stands (before the harvest date, or all season when none)",
        "rain_event_mm": f"a rain of {RAIN_EVENT_MM:.0f} mm or more sets last_rain_date; surface_dry when ET0 since then exceeds it (ASSUMPTION: a rough cue from one station, not a trafficability check)",
        "rain_record": f"blank rain days are read as 0 mm and counted in a note; when the precip_mm column is missing or fewer than {RAIN_KNOWN_MIN_FRACTION:.0%} of recorded days carry a figure, no field is balanced (ASSUMPTION)",
        "runoff": "no runoff and no irrigation; rain above the bucket drains (ASSUMPTION: flat, tile-drained fields)",
    }


METHOD = ("Every field gets a simple soil water bucket: 150 mm of water a crop can reach in the top metre, assumed full when the "
          "season starts. Rain fills it and the crop empties it. How much the weather asks for each day comes from the daily high "
          "and low and the sun's position (FAO-56 Hargreaves), and the crop's share follows its own satellite greenness, so a lush "
          "field draws more than a thin one and a harvested field draws almost nothing. When more than half the bucket is gone the "
          "crop is short of water and we count a stress day. Each morning the plan estimates how much water is left under each "
          "field, how many stress days the season has brought and when it last rained, and gives a rough sign that the surface has "
          "had time to dry since the last real rain (one station for the whole area; a cue, not a trafficability check).")


# ================================================================================================= analyze

def analyze(data, config, prior=None):
    """The water module. Never raises: a field it cannot balance is None, a missing section is None, and
    landscape["notes"] says why."""
    notes = []
    out = {"key": KEY, "fields": {}, "landscape": {"notes": notes}, "zones": {}, "assumptions": _assumptions(), "method": METHOD}
    try:
        return _analyze(data if isinstance(data, dict) else {}, config if isinstance(config, dict) else {}, out, notes)
    except Exception as e:  # a model quirk must never cost the morning run its other modules
        notes.append(f"water balance failed: {type(e).__name__}: {e}")
        out["landscape"].setdefault("narrative", "Water balance unavailable this morning.")
        return out


def _analyze(data, config, out, notes):
    fields_fc = data.get("fields")
    ts = data.get("timeseries") if isinstance(data.get("timeseries"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    weather = data.get("weather") if isinstance(data.get("weather"), dict) else {}
    obs = weather.get("observed") if isinstance(weather.get("observed"), dict) else {}
    fc = weather.get("forecast") if isinstance(weather.get("forecast"), dict) else {}
    ts_fields = ts.get("fields") if isinstance(ts.get("fields"), dict) else {}
    ts_dates = ts.get("dates") if isinstance(ts.get("dates"), list) else []

    # --- field list and zones (fields.geojson first, else whatever the timeseries knows) ---
    feats = _features(fields_fc)
    field_ids, zone_of = [], {}
    for f in feats:
        p = f.get("properties") or {}
        fid = p.get("field_id")
        if isinstance(fid, str) and fid not in zone_of:
            field_ids.append(fid)
            zone_of[fid] = p.get("zone_id")
    if not field_ids:
        if ts_fields:
            notes.append("fields.geojson missing or empty: field list taken from timeseries.json (no zones)")
        field_ids = [k for k in ts_fields if isinstance(k, str)]
        for r in status.get("fields") or []:
            if isinstance(r, dict) and r.get("field_id") in ts_fields:
                zone_of.setdefault(r["field_id"], r.get("zone_id"))
    out["fields"] = {fid: None for fid in field_ids}
    zone_ids = []
    for f in _features(data.get("zones")):
        z = (f.get("properties") or {}).get("zone_id")
        if isinstance(z, str) and z not in zone_ids:
            zone_ids.append(z)
    for z in zone_of.values():
        if isinstance(z, str) and z not in zone_ids:
            zone_ids.append(z)
    out["zones"] = {z: None for z in zone_ids}
    if not field_ids:
        notes.append("no fields in fields.geojson or timeseries.json: nothing to balance")

    # --- dates ---
    today = _iso_date(config.get("as_of")) or _iso_date(status.get("as_of"))
    if today is None:
        last = max((d for d in (_iso_date(x) for x in (obs.get("dates") or [])) if d), default=None)
        if last is None:
            notes.append("no as_of in config or status.json and no observed weather: no water balance")
            out["landscape"]["narrative"] = "Water balance unavailable: no date to run it for."
            return out
        today = last + timedelta(days=1)
        notes.append(f"no as_of in config or status.json: using the day after the last observed weather ({today.isoformat()})")
    season_start = _iso_date(config.get("season_start"))
    if season_start is None:
        first = min((d for d in (_iso_date(x) for x in (obs.get("dates") or [])) if d), default=None)
        season_start = first or today
        notes.append(f"no season_start in config: using {season_start.isoformat()}")
    out["landscape"].update({"as_of": today.isoformat(), "season_start": season_start.isoformat()})

    # --- weather on the daily grid ---
    lat = _latitude(data, config, notes)
    if lat is None:
        notes.append("no latitude (no bbox, zones or field polygons): ET0 cannot be computed")
        out["landscape"]["narrative"] = "Water balance unavailable: no latitude for the radiation term."
        return out
    grid = _daily_weather(obs, season_start, today, notes)
    if grid is None:
        out["landscape"]["narrative"] = "Water balance unavailable: no observed weather for the season."
        out["landscape"]["forecast"] = _forecast_et0(fc, lat, today, notes)
        return out
    days, precip, tmax, tmin, rain_known = grid
    n = len(days)
    et0 = _et0_daily(lat, days, tmax, tmin, notes)
    if et0 is None:
        out["landscape"]["narrative"] = "Water balance unavailable: no temperatures to compute ET0."
        out["landscape"]["forecast"] = _forecast_et0(fc, lat, today, notes)
        return out
    season_et0 = float(np.sum(et0))
    if not rain_known:
        # ET0 is real, the forecast may be real, but a bucket with no rain in it would call every field dry: no balance.
        L = out["landscape"]
        L["season"] = {"start": season_start.isoformat(), "end": days[-1].isoformat(), "days": n,
                       "precip_mm": None, "et0_mm": round(season_et0, 1), "etc_median_mm": None, "etc_mean_mm": None,
                       "drained_median_mm": None, "et0_last7_mm": round(float(np.sum(et0[-7:])), 1), "precip_last7_mm": None}
        L["forecast"] = _forecast_et0(fc, lat, today, notes)
        L["water_status_counts"] = {"wet": 0, "adequate": 0, "dry": 0}
        L["fields_analysed"] = 0
        L["fields_skipped"] = len(field_ids)
        L["last_rain_date"] = None
        L["days_since_rain"] = None
        L["surface_dry"] = None
        L["latitude"] = round(lat, 3)
        L["median_depletion_mm"] = None
        parts = [f"Since {season_start.isoformat()} the station asked for {season_et0:.0f} mm of reference ET, but its rain record "
                 f"is missing, so no field's water bucket can be balanced this morning"]
        clause = _forecast_clause(L["forecast"])
        if clause:
            parts.append(clause)
        L["narrative"] = "; ".join(parts) + "."
        return out
    last_rain, since_rain, surface_dry = _rain_memory(days, precip, et0, today)
    season_precip = float(np.sum(precip))

    # --- per-field Kc and standing matrices ---
    recs = {}
    for r in status.get("fields") or []:
        if isinstance(r, dict) and isinstance(r.get("field_id"), str):
            recs[r["field_id"]] = r
    idx = np.arange(n, dtype=float)
    kept, kc_rows, stand_rows, meta = [], [], [], []
    skipped_no_series, skipped_no_clear = 0, 0
    for fid in field_ids:
        series = ts_fields.get(fid)
        if series is None:
            skipped_no_series += 1
            continue
        passes = _clear_passes(ts_dates, series, season_start)
        if not passes:
            skipped_no_clear += 1
            continue
        xp = np.array([p[0] for p in passes], dtype=float)
        fp = np.array([p[1] for p in passes], dtype=float)
        ndvi_daily = np.interp(idx, xp, fp)                     # held flat before the first and after the last clear pass
        kc = kc_from_ndvi(ndvi_daily)
        rec = recs.get(fid) or {}
        harvest = _iso_date(rec.get("harvest_date"))
        standing = np.ones(n, dtype=bool)
        if harvest is not None:
            h = (harvest - season_start).days
            if h < n:
                h0 = max(h, 0)
                kc[h0:] = KC_RESIDUE
                standing[h0:] = False
        peak = _iso_date(rec.get("peak_date"))
        kept.append(fid)
        kc_rows.append(kc)
        stand_rows.append(standing)
        meta.append({"harvest": harvest, "peak": peak})
    if skipped_no_series:
        notes.append(f"{skipped_no_series} field{'s' if skipped_no_series != 1 else ''} not in timeseries.json: no water balance")
    if skipped_no_clear:
        notes.append(f"{skipped_no_clear} field{'s' if skipped_no_clear != 1 else ''} with no clear pass: no water balance")

    if kept:
        kc_m = np.vstack(kc_rows)
        st_m = np.vstack(stand_rows)
        res = run_bucket(et0, precip, kc_m, st_m)
        dep_today = res["depletion"][:, -1]
        etc_season = res["etc"].sum(axis=1)
        stress_total = res["stress"].sum(axis=1)
        for i, fid in enumerate(kept):
            m = meta[i]
            since_peak = None
            if m["peak"] is not None:
                p0 = (m["peak"] - season_start).days
                since_peak = int(res["stress"][i, max(p0, 0):].sum()) if p0 < n else 0
            dep = float(dep_today[i])
            out["fields"][fid] = {
                "season_et0_mm": round(season_et0, 1),
                "season_etc_mm": round(float(etc_season[i]), 1),
                "season_precip_mm": round(season_precip, 1),
                "depletion_today_mm": round(dep, 1),
                "available_fraction_today": round(max(0.0, 1.0 - dep / TAW_MM), 3),
                "stress_days": int(stress_total[i]),
                "stress_days_since_peak": since_peak,
                "water_status": _status_word(dep),
                "last_rain_date": last_rain.isoformat() if last_rain else None,
                "days_since_rain": since_rain,
                "surface_dry": surface_dry,
                "kc_today": round(float(kc_m[i, -1]), 3),
                "standing": bool(st_m[i, -1]),
            }

    # --- landscape ---
    done = [out["fields"][f] for f in kept]
    counts = {"wet": 0, "adequate": 0, "dry": 0}
    for v in done:
        counts[v["water_status"]] += 1
    etc_vals = [v["season_etc_mm"] for v in done]
    L = out["landscape"]
    L["season"] = {"start": season_start.isoformat(), "end": days[-1].isoformat(), "days": n,
                   "precip_mm": round(season_precip, 1), "et0_mm": round(season_et0, 1),
                   "etc_median_mm": _r(np.median(etc_vals)) if etc_vals else None,
                   "etc_mean_mm": _r(np.mean(etc_vals)) if etc_vals else None,
                   "drained_median_mm": _r(np.median(res["drained"].sum(axis=1))) if kept else None,
                   "et0_last7_mm": round(float(np.sum(et0[-7:])), 1),
                   "precip_last7_mm": round(float(np.sum(precip[-7:])), 1)}
    L["forecast"] = _forecast_et0(fc, lat, today, notes)
    L["water_status_counts"] = counts
    L["fields_analysed"] = len(kept)
    L["fields_skipped"] = len(field_ids) - len(kept)
    L["last_rain_date"] = last_rain.isoformat() if last_rain else None
    L["days_since_rain"] = since_rain
    L["surface_dry"] = surface_dry
    L["latitude"] = round(lat, 3)
    L["median_depletion_mm"] = _r(np.median([v["depletion_today_mm"] for v in done])) if done else None
    L["narrative"] = _narrative(season_start, season_precip, season_et0, counts, len(kept), last_rain, since_rain, surface_dry, L["forecast"])

    # --- zones ---
    by_zone = {}
    for fid in kept:
        z = zone_of.get(fid)
        if isinstance(z, str):
            by_zone.setdefault(z, []).append(out["fields"][fid])
    for z, vals in by_zone.items():
        deps = [v["depletion_today_mm"] for v in vals]
        out["zones"][z] = {"n_fields": len(vals),
                           "mean_depletion_mm": _r(np.mean(deps)),
                           "mean_available_fraction": _r(np.mean([v["available_fraction_today"] for v in vals]), 3),
                           "mean_stress_days": _r(np.mean([v["stress_days"] for v in vals])),
                           "dry_fields": sum(1 for v in vals if v["water_status"] == "dry"),
                           "wet_fields": sum(1 for v in vals if v["water_status"] == "wet")}
    return out


def _narrative(season_start, precip, et0, counts, n_done, last_rain, since_rain, surface_dry, forecast):
    if n_done == 0:
        return f"Since {season_start.isoformat()} the station saw {precip:.0f} mm of rain against {et0:.0f} mm of reference ET, but no field could be balanced."
    parts = [f"Since {season_start.isoformat()} the station saw {precip:.0f} mm of rain against {et0:.0f} mm of reference ET"]
    parts.append(f"{counts['dry']} of {n_done} fields are dry (root zone past half empty), {counts['adequate']} adequate and {counts['wet']} wet")
    if last_rain is not None:
        rain = f"the last rain of {RAIN_EVENT_MM:.0f} mm or more fell on {last_rain.isoformat()}, {since_rain} day{'s' if since_rain != 1 else ''} ago"
        rain += ", with enough ET since to have dried the surface" if surface_dry else ", with not enough ET since to have dried the surface"
        parts.append(rain)
    else:
        parts.append(f"no rain of {RAIN_EVENT_MM:.0f} mm or more all season")
    clause = _forecast_clause(forecast)
    if clause:
        parts.append(clause)
    return "; ".join(parts) + "."


def _forecast_clause(forecast):
    """'the next N days ask for X mm of ET0 against Y mm of forecast rain', without the rain half when the forecast
    carries no rain figures; None when there is no forecast ET0."""
    if not forecast or forecast.get("et0_mm") is None:
        return None
    s = f"the next {forecast['days']} days ask for {forecast['et0_mm']:.0f} mm of ET0"
    if forecast.get("precip_mm") is not None:
        s += f" against {forecast['precip_mm']:.0f} mm of forecast rain"
    return s
