"""The season at landscape scale, for the officer's morning brief and the pitch. Module key: "landscape".

  harvest progress per crop   cumulative share of the crop's area with a satellite harvest date, by date; a logistic
                              fitted by least squares on logit(share) against season day; projected 50 / 75 / 90%
                              dates; the cutting rate of the last ten days
  combine demand              every ready or maturing field's area spread evenly over its predicted harvest window,
                              summed per day over the next two weeks and turned into machine-days; capacity is the
                              machine count of the simulated depots in config/chcs.json when they are given
  zone summary                fields, area, harvested and ready shares, latest greenness, and from plans.json the
                              flag counts and the most common open check
  USDA comparison             the Crop Progress row for the same week of the latest year USDA has published (week
                              ending nearest today's month and day; last season's pace until this year's report is
                              out), next to our own percent harvested
  headline numbers            fields, area by crop, standing and harvested area, flag counts, three plain sentences

    analyze(data, config, prior=None) -> {"key", "fields", "landscape", "zones", "assumptions", "method"}

Pure: it reads the dicts it is given and returns JSON-ready plain values (no numpy scalars, no NaN). Nothing here
raises on a missing or malformed input: the affected section is None or empty and landscape["notes"] says why.
Every fact comes from status.json, fields.geojson, timeseries.json, plans.json, route.json and the USDA cache;
nothing is measured here.
"""
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np

import weather_model as wm

KEY = "landscape"
STANDING = ("growing", "maturing", "ready")
DEMAND_STAGES = ("ready", "maturing")        # fields whose predicted harvest window still lies ahead of them
FIT_SHARE_MIN, FIT_SHARE_MAX = 0.02, 0.98    # task brief: only shares in this band enter the logit fit (the tails are one or two fields)
FIT_MIN_POINTS = 3                           # task brief: fewer distinct harvest dates than this gives no projection
SLOPE_EPS = 1e-9                             # a slope this small is flat: no projection
PROJECT_LEVELS = (50, 75, 90)
RECENT_RATE_DAYS = 10                        # task brief: the cutting rate is the area harvested in the last ten days divided by ten
DEFAULT_HORIZON_DAYS = 14                    # region.json forecast_horizon_days overrides
DEFAULT_MACHINE_RATE = 40.0                  # ha per combine per day; region.json machine_rate_ha_per_day overrides (an ASSUMPTION there)
DEFAULT_SEASON_LENGTH_DAYS = 150             # ASSUMPTION: when region.json has no season_start, day 0 is this long before as_of
CAPACITY_SOURCE = "config/chcs.json (simulated depots)"
USDA_CROP = {"CORN": "corn", "SOYBEANS": "soybean", "SOYBEAN": "soybean"}
CHECK_RANK = {"act_now": 2, "watch": 1, "clear": 0}
CHECK_ORDER = ["fire", "flood", "harvest_before_rain", "burn_scar", "stress", "visible_change", "harvest_ready",
               "establishment"]              # the spec's route priority: breaks ties for a zone's top open check


# ----------------------------------------------------------------------------- small helpers
def _num(x, nd=2):
    """A plain rounded float, or None for nothing, NaN, inf or anything that is not a number."""
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


def _date(x):
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _iso(x):
    return x.isoformat() if isinstance(x, date) else None


def _pct(num, den, nd=2):
    return _num(100.0 * num / den, nd) if den and den > 0 else 0.0


def _month_day_distance(a: date, b: date) -> int:
    """Days between two calendar dates ignoring the year (29 Feb reads as 28 Feb), the short way round the year."""
    try:
        a2 = a.replace(year=b.year)
    except ValueError:
        a2 = a.replace(year=b.year, day=28)
    diff = abs((a2 - b).days)
    return min(diff, 365 - diff)


# ----------------------------------------------------------------------------- readers (each tolerates a bad file)
def _field_meta(data, notes):
    """field_id -> {zone_id, crop, area_ha} from fields.geojson, in file order. Missing or negative areas read 0."""
    doc = data.get("fields")
    feats = doc.get("features") if isinstance(doc, dict) else None
    if not isinstance(feats, list):
        notes.append("fields.geojson missing or malformed: no field rows")
        return {}
    out = {}
    for f in feats:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict) or not p.get("field_id"):
            continue
        area = _num(p.get("area_ha"), 4)
        crop = p.get("crop") if isinstance(p.get("crop"), str) and p.get("crop").strip() else "other"
        out[str(p["field_id"])] = {"zone_id": str(p["zone_id"]) if p.get("zone_id") is not None else None,
                                   "crop": crop.strip().lower(), "area_ha": area if area and area > 0 else 0.0}
    return out


def _status_recs(data, notes):
    doc = data.get("status")
    recs = doc.get("fields") if isinstance(doc, dict) else None
    if not isinstance(recs, list):
        notes.append("status.json missing or malformed: every field reads as stage unknown and nothing is harvested")
        return {}
    return {str(r["field_id"]): r for r in recs if isinstance(r, dict) and r.get("field_id")}


def _latest_ndvi(data, notes):
    """field_id -> (date, ndvi) of the field's latest clear pass, and the latest clear pass over the landscape."""
    doc = data.get("timeseries")
    if not isinstance(doc, dict) or not isinstance(doc.get("dates"), list) or not isinstance(doc.get("fields"), dict):
        notes.append("timeseries.json missing or malformed: no latest greenness")
        return {}, None
    dates = [_date(x) for x in doc["dates"]]
    out, latest_any = {}, None
    for fid, series in doc["fields"].items():
        ndvi = series.get("ndvi") if isinstance(series, dict) else None
        if not isinstance(ndvi, list):
            continue
        for i in range(min(len(ndvi), len(dates)) - 1, -1, -1):
            v, dt = _num(ndvi[i], 3), dates[i]
            if v is not None and dt is not None:
                out[str(fid)] = (dt, v)
                latest_any = dt if latest_any is None or dt > latest_any else latest_any
                break
    return out, latest_any


def _plan_rows(data, notes):
    """field_id -> {flag, open: [(kind, status)]} from plans.json; None when there is no usable plans file."""
    doc = data.get("plans")
    rows = doc.get("plans") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        notes.append("plans.json not given or malformed: no flags or open checks" if doc is not None
                     else "plans.json not given: no flags or open checks")
        return None
    out = {}
    for p in rows:
        if not isinstance(p, dict) or not p.get("farm_id"):
            continue
        checks = p.get("checks") if isinstance(p.get("checks"), list) else []
        open_ = [(c["kind"], c["status"]) for c in checks
                 if isinstance(c, dict) and c.get("kind") and c.get("status") in ("watch", "act_now")]
        flag = p.get("flag") if p.get("flag") in ("fine", "watch", "act_now") else None
        out[str(p["farm_id"])] = {"flag": flag, "open": open_}
    return out


def _route_counts(data):
    doc = data.get("route")
    stops = doc.get("stops") if isinstance(doc, dict) else None
    if not isinstance(stops, list):
        return None
    visits = sum(1 for st in stops if isinstance(st, dict) and st.get("action") == "visit")
    return {"stops": len(stops), "visits": visits, "calls": len(stops) - visits}


def _harvest_date(rec, today):
    """The satellite's harvest date when status.json has one on or before today, else None."""
    hd = _date((rec or {}).get("harvest_date"))
    return hd if hd is not None and hd <= today else None


def _precip_map(weather):
    """date -> mm from the observed record and the forecast (forecast wins on a shared day); {} when none."""
    out = {}
    if not isinstance(weather, dict):
        return out
    for block in ("observed", "forecast"):
        blk = weather.get(block)
        if not isinstance(blk, dict) or not isinstance(blk.get("dates"), list):
            continue
        vals = blk.get("precip_mm") if isinstance(blk.get("precip_mm"), list) else []
        for i, x in enumerate(blk["dates"]):
            dt = _date(x)
            if dt is not None and i < len(vals):
                out[dt] = _num(vals[i], 2) or 0.0
    return out


# ----------------------------------------------------------------------------- 1. harvest progress per crop
def _logistic_fit(days, shares, season_start):
    """Least squares of logit(share) on season day (day 0 = season_start) over the points in the fit band, and
    the day each projection level is reached by inverting the line. No projection with fewer than
    FIT_MIN_POINTS points or a flat or falling line; r2 is None when the points have no spread."""
    out = {"n": 0, "slope": None, "intercept": None, "r2": None, "projected": {lvl: None for lvl in PROJECT_LEVELS}}
    pts = [((dt - season_start).days, float(sh)) for dt, sh in zip(days, shares) if FIT_SHARE_MIN <= sh <= FIT_SHARE_MAX]
    out["n"] = len(pts)
    if len(pts) < FIT_MIN_POINTS:
        return out
    x = np.array([p[0] for p in pts], dtype=float)
    p = np.array([p[1] for p in pts], dtype=float)
    y = np.log(p / (1.0 - p))
    try:
        slope, intercept = (float(v) for v in np.polyfit(x, y, 1))
    except (np.linalg.LinAlgError, ValueError):
        return out
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    ss_res = float(np.sum((y - (slope * x + intercept)) ** 2))
    out["slope"], out["intercept"] = _num(slope, 4), _num(intercept, 3)
    out["r2"] = _num(1.0 - ss_res / ss_tot, 4) if ss_tot > 0 else None
    if not math.isfinite(slope) or slope <= SLOPE_EPS:
        return out
    for lvl in PROJECT_LEVELS:
        q = lvl / 100.0
        day = (math.log(q / (1.0 - q)) - intercept) / slope
        out["projected"][lvl] = _iso(season_start + timedelta(days=round(day))) if math.isfinite(day) and abs(day) < 100000 else None
    return out


def harvest_progress(meta, recs, today, season_start):
    """Per crop present in fields.geojson: the cumulative curve of harvested area share, today's percent, the
    ten-day cutting rate, the logistic fit and its projections, and the dates the observed curve crossed each level."""
    out = {}
    for crop in sorted({m["crop"] for m in meta.values()}):
        fids = [f for f, m in meta.items() if m["crop"] == crop]
        total = sum(meta[f]["area_ha"] for f in fids)
        cut = sorted((hd, meta[f]["area_ha"]) for f in fids for hd in [_harvest_date(recs.get(f), today)] if hd is not None)
        by_day = defaultdict(float)
        for hd, ha in cut:
            by_day[hd] += ha
        days = sorted(by_day)
        cum = np.cumsum([by_day[dt] for dt in days]) if days else np.zeros(0)
        shares = cum / total if total > 0 else np.zeros(len(days))
        harvested = float(cum[-1]) if len(cum) else 0.0
        recent = sum(ha for hd, ha in cut if today - timedelta(days=RECENT_RATE_DAYS) < hd <= today)
        fit = _logistic_fit(days, shares, season_start)
        row = {"total_ha": _num(total, 1), "harvested_ha": _num(harvested, 1), "n_fields": len(fids), "n_harvested": len(cut),
               "pct_harvested_today": _pct(harvested, total), "recent_rate_ha_per_day": _num(recent / RECENT_RATE_DAYS, 2),
               "fit_points": fit["n"], "fit_slope_per_day": fit["slope"], "fit_intercept": fit["intercept"], "fit_r2": fit["r2"]}
        ahead = False
        for lvl in PROJECT_LEVELS:
            projected = fit["projected"][lvl]
            crossed = next((_iso(dt) for dt, sh in zip(days, shares) if sh * 100.0 + 1e-9 >= lvl), None)
            row[f"projected_{lvl}_pct"], row[f"crossed_{lvl}_pct"] = projected, crossed
            # a level the fit puts on or before today that the observed curve has not reached: the early steep
            # part of the season is pulling the line, so the dates are optimistic
            ahead = ahead or (projected is not None and crossed is None and _date(projected) <= today)
        row["fit_caveat"] = "projections run ahead of the observed curve: read them as early" if ahead else None
        row["curve"] = [[_iso(dt), _num(sh * 100.0, 2)] for dt, sh in zip(days, shares)]
        out[crop] = row
    return out


# ----------------------------------------------------------------------------- 2. combine demand
def _capacity(chcs):
    """Machines over the simulated depots: config/chcs.json as a {"chcs": [...]} dict or a bare list; None when absent."""
    lst = chcs.get("chcs") if isinstance(chcs, dict) else chcs
    if not isinstance(lst, list):
        return None
    counts = [c["machines"] for c in lst if isinstance(c, dict) and isinstance(c.get("machines"), (int, float))
              and not isinstance(c.get("machines"), bool)]
    return int(sum(counts)) if counts else None


def combine_demand(meta, recs, today, horizon, rate, chcs, weather, notes):
    """Hectares to combine per day for the next `horizon` days: each ready or maturing field's area spread evenly
    over its predicted window (from today when the window is already open), divided by the machine rate for
    machine-days, against the depots' machine count when given. Returns (block, field_id -> ha per day)."""
    days = [today + timedelta(days=i) for i in range(horizon)]
    ha = np.zeros(horizon)
    per_field, beyond, in_window = {}, 0.0, 0
    for fid, m in meta.items():
        r = recs.get(fid) or {}
        if r.get("stage") not in DEMAND_STAGES or m["area_ha"] <= 0:
            continue
        a, b = _date(r.get("predicted_harvest_start")), _date(r.get("predicted_harvest_end"))
        if a is None or b is None:
            continue
        start = max(a, today)
        end = max(b, start)
        n = (end - start).days + 1
        daily = m["area_ha"] / n
        per_field[fid] = _num(daily, 2)
        inside = 0
        for k in range(n):
            i = (start - today).days + k
            if 0 <= i < horizon:
                ha[i] += daily
                inside += 1
        in_window += 1 if inside else 0
        beyond += daily * (n - inside)
    md = ha / rate
    precip = _precip_map(weather)
    work = wm.workable_from_precip(precip, days[0], days[-1]) if precip and days else {}
    rows = [{"date": _iso(dt), "ha": _num(ha[i], 1), "machine_days": _num(md[i], 2),
             "workable": (bool(work[dt]) if dt in precip and dt in work else None)} for i, dt in enumerate(days)]
    cap = _capacity(chcs)
    peak = int(np.argmax(ha)) if horizon and float(ha.max()) > 0 else None
    return ({"horizon_days": horizon, "from": _iso(days[0]) if days else None, "to": _iso(days[-1]) if days else None,
             "machine_rate_ha_per_day": _num(rate, 1),
             "capacity_machines": cap, "capacity_source": CAPACITY_SOURCE if cap is not None else None,
             "days": rows,
             "peak_day": _iso(days[peak]) if peak is not None else None,
             "peak_ha": _num(ha[peak], 1) if peak is not None else 0.0,
             "peak_machine_days": _num(md[peak], 2) if peak is not None else 0.0,
             "days_over_capacity": int(np.sum(md > cap)) if cap is not None else None,
             "fields_in_window": in_window, "ha_in_horizon": _num(float(ha.sum()), 1),
             "ha_beyond_horizon": _num(beyond, 1), "total_machine_days": _num(float(md.sum()), 2),
             "workable_days": (sum(1 for r in rows if r["workable"]) if any(r["workable"] is not None for r in rows) else None)},
            per_field)


# ----------------------------------------------------------------------------- 3. zone summary
def zone_summary(meta, recs, latest, plans, data, today, notes):
    """One row per zone: zones.geojson first (file order), then any zone only the fields know about."""
    names = {}
    zdoc = data.get("zones")
    feats = zdoc.get("features") if isinstance(zdoc, dict) else None
    if isinstance(feats, list):
        for f in feats:
            p = f.get("properties") if isinstance(f, dict) else None
            if isinstance(p, dict) and p.get("zone_id") is not None:
                names[str(p["zone_id"])] = p.get("name") if isinstance(p.get("name"), str) else None
    else:
        notes.append("zones.geojson missing or malformed: zones come from the fields alone")
    extra = sorted({m["zone_id"] for m in meta.values() if m["zone_id"] and m["zone_id"] not in names})
    out = {}
    for z in list(names) + extra:
        fids = [f for f, m in meta.items() if m["zone_id"] == z]
        ha = sum(meta[f]["area_ha"] for f in fids)
        by_crop, harvested, ready, standing = Counter(), 0.0, 0.0, 0.0
        for f in fids:
            a = meta[f]["area_ha"]
            by_crop[meta[f]["crop"]] += a
            r = recs.get(f) or {}
            if _harvest_date(r, today) is not None:
                harvested += a
            elif r.get("stage") == "ready":
                ready += a
            if r.get("stage") in STANDING:
                standing += a
        nd = [latest[f][1] for f in fids if f in latest]
        row = {"name": names.get(z), "fields": len(fids), "ha": _num(ha, 1),
               "ha_by_crop": {c: _num(v, 1) for c, v in sorted(by_crop.items())},
               "harvested_ha": _num(harvested, 1), "pct_harvested_area": _pct(harvested, ha, 1),
               "pct_ready_area": _pct(ready, ha, 1), "pct_standing_area": _pct(standing, ha, 1),
               "mean_latest_ndvi": _num(float(np.mean(nd)), 3) if nd else None,
               "act_now": None, "watch": None, "fine": None, "top_kind": None}
        if plans is not None:
            flags, kinds, worst = Counter(), Counter(), {}
            for f in fids:
                p = plans.get(f)
                if not p:
                    continue
                if p["flag"]:
                    flags[p["flag"]] += 1
                for kind, st in p["open"]:
                    kinds[kind] += 1
                    worst[kind] = max(worst.get(kind, 0), CHECK_RANK.get(st, 0))
            row.update(act_now=flags["act_now"], watch=flags["watch"], fine=flags["fine"])
            if kinds:
                order = {k: i for i, k in enumerate(CHECK_ORDER)}
                row["top_kind"] = max(kinds, key=lambda k: (kinds[k], worst[k], -order.get(k, len(order))))
        out[z] = row
    return out


# ----------------------------------------------------------------------------- 4. USDA comparison
def usda_comparison(usda, progress, today, notes):
    """Per commodity, the Crop Progress row whose week ending is nearest today's month and day (any year) next to
    our percent harvested for that crop. None when the cache is absent or has no usable row."""
    if not isinstance(usda, dict):
        if usda is not None:
            notes.append("usda_crop_progress.json malformed: no USDA comparison")
        return None
    groups = {}
    series = usda.get("series")
    if isinstance(series, dict):
        for name, blk in series.items():
            rows = blk.get("rows") if isinstance(blk, dict) else None
            if isinstance(rows, list):
                groups[str(name).upper()] = rows
    elif isinstance(usda.get("rows"), list):            # flat shape: each row names its commodity
        for r in usda["rows"]:
            if isinstance(r, dict) and r.get("commodity"):
                groups.setdefault(str(r["commodity"]).upper(), []).append(r)
    crops = {}
    for name, rows in groups.items():
        best = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            we, pct = _date(r.get("week_ending")), _num(r.get("pct_harvested"), 1)
            if we is None or pct is None:
                continue
            dist = _month_day_distance(we, today)
            if best is None or dist < best[0] or (dist == best[0] and we > best[1]):     # a tie goes to the later year
                best = (dist, we, pct, r.get("year"))
        if best is None:
            continue
        crop = USDA_CROP.get(name, name.lower())
        ours = (progress.get(crop) or {}).get("pct_harvested_today")
        crops[crop] = {"commodity": name, "week_ending": _iso(best[1]),
                       "year": int(best[3]) if isinstance(best[3], (int, float)) and not isinstance(best[3], bool) else best[1].year,
                       "days_from_as_of": int(best[0]), "usda_pct_harvested": best[2], "ours_pct_harvested": ours,
                       "diff_pp": _num(ours - best[2], 1) if ours is not None else None}
    if not crops:
        notes.append("usda_crop_progress.json has no usable rows: no USDA comparison")
        return None
    return {"source": usda.get("source") if isinstance(usda.get("source"), str) else None,
            "state": usda.get("state") if isinstance(usda.get("state"), str) else None,
            "district": usda.get("district") if isinstance(usda.get("district"), str) else None,
            "as_of_month_day": today.strftime("%m-%d"), "crops": crops,
            "note": ("USDA district rows are the same week of the latest published year (last season's pace when this year "
                     "is not out yet), matched on month and day; diff_pp is ours minus that USDA pace")}


# ----------------------------------------------------------------------------- 5. headline and narrative
def _narrative(L, demand, zones, today):
    """Three plain sentences for the officer and the pitch. No em dashes; dates as ISO."""
    prog = L.get("harvest_progress") or {}
    named = [c for c in ("corn", "soybean") if c in prog and (prog[c].get("total_ha") or 0) > 0]
    by_crop = ", ".join(f"{c} {prog[c]['pct_harvested_today']:.0f}%" for c in named)
    one = (f"Across {L['fields']} fields ({L['ha_total']:.0f} ha), {L['pct_harvested_area']:.0f}% of the area is harvested"
           + (f" ({by_crop})" if by_crop else "") + f" as of {_iso(today)}.")
    if demand and demand.get("peak_day"):
        cap = demand.get("capacity_machines")
        cap_txt = (f" against {cap} simulated machines ({demand['days_over_capacity']} days over capacity)" if cap is not None else "")
        two = (f"The next {demand['horizon_days']} days ask for up to {demand['peak_machine_days']:.1f} machine-days of combining "
               f"on {demand['peak_day']}{cap_txt}.")
    else:
        two = f"No ready or maturing field has a harvest window inside the next {(demand or {}).get('horizon_days', DEFAULT_HORIZON_DAYS)} days."
    flags = L.get("flags")
    if flags:
        busiest = max(zones, key=lambda z: ((zones[z].get("act_now") or 0), (zones[z].get("watch") or 0))) if zones else None
        tail = ""
        if busiest and ((zones[busiest].get("act_now") or 0) + (zones[busiest].get("watch") or 0)) > 0:
            kind = zones[busiest].get("top_kind")
            tail = f"; the busiest zone is {busiest}" + (f" ({kind.replace('_', ' ')})" if kind else "")
        three = f"Today {flags['act_now']} farms need action, {flags['watch']} are on watch and {flags['fine']} are fine{tail}."
    else:
        three = "No plans file was given, so there are no flags to count today."
    return " ".join((one, two, three))


def _skeleton(notes, reason):
    notes.append(reason)
    return {"key": KEY, "fields": {}, "zones": {},
            "landscape": {"as_of": None, "fields": 0, "ha_total": 0.0, "ha_by_crop": {}, "harvest_progress": {},
                          "combine_demand": None, "usda_comparison": None, "flags": None,
                          "narrative": "No usable input: nothing to report. " + reason + ". Check the data folder.",
                          "notes": notes},
            "assumptions": ASSUMPTIONS_STATIC, "method": METHOD}


ASSUMPTIONS_STATIC = {
    "fit_band": "0.02 to 0.98 of crop area: the first and last fields do not shape the logistic (task brief)",
    "fit_min_points": "3 distinct harvest dates before a projection is made; flat or falling fits project nothing (task brief)",
    "recent_rate_days": "10: area harvested in the last ten days divided by ten (task brief)",
    "harvested": "a field is harvested when status.json carries a harvest_date on or before as_of (the satellite's call, not a report)",
    "demand_spread": "ASSUMPTION: a field's area is spread evenly over its predicted window, from today when the window is already open",
    "capacity": "sum of machines over the simulated depots in config/chcs.json (label it simulated); None when the file is not given",
    "workable_day": "weather_model rule: 5 mm or more blocks the day, 12 mm or more also the next day, 25 mm or more two more days",
    "usda_match": "the Crop Progress row for the same week of the year (week ending nearest as_of by month and day) from the "
                  "latest year USDA has published for the district: last season's pace when this year's report is not out yet, "
                  "so diff_pp is a gap to that pace, not to a current USDA figure",
    "top_kind": "most common open (watch or act_now) check in the zone's plans; ties go to the more severe, then the route order",
}
METHOD = ("We add up the hectares the satellite has already seen harvested, crop by crop, and fit an S-curve through "
          "that running share so the officer can read off when half, three quarters and nine tenths of the crop will "
          "be in, then set the figure against the pace USDA published for the same week of the latest year it has out "
          "for the district (last season's pace until this year's report lands). We spread every ready or "
          "maturing field's area over the days it is expected to be cut and add it up per day for the next two weeks, "
          "which tells us how many combine-days the landscape needs and whether the simulated depots could cover it, "
          "and we roll the same facts up per zone with the morning flags so the busiest corner of the county is "
          "obvious at a glance.")


# ----------------------------------------------------------------------------- entry point
def analyze(data: dict, config: dict, prior: dict | None = None) -> dict:
    """Landscape-scale summary of the season. See the module docstring for the sections; every section degrades to
    None or empty with a note rather than raising."""
    notes = []
    data = data if isinstance(data, dict) else {}
    config = config if isinstance(config, dict) else {}
    status_doc = data.get("status") if isinstance(data.get("status"), dict) else {}

    today = _date(config.get("as_of"))
    if today is None:
        today = _date(status_doc.get("as_of"))
        notes.append("config as_of missing: using status.json as_of" if today else "config as_of missing")
    meta = _field_meta(data, notes)
    recs = _status_recs(data, notes)
    latest, latest_pass = _latest_ndvi(data, notes)
    if today is None:
        today = latest_pass
        if today is not None:
            notes.append("no as_of anywhere: the latest clear pass stands in for today")
    if today is None:
        return _skeleton(notes, "no as_of in config, status.json or timeseries.json")

    season_start = _date(config.get("season_start"))
    if season_start is None:
        season_start = today - timedelta(days=DEFAULT_SEASON_LENGTH_DAYS)
        notes.append(f"config season_start missing: day 0 of the fit is {DEFAULT_SEASON_LENGTH_DAYS} days before as_of")
    horizon = config.get("forecast_horizon_days")
    horizon = int(horizon) if isinstance(horizon, (int, float)) and not isinstance(horizon, bool) and horizon >= 1 else DEFAULT_HORIZON_DAYS
    rate = _num(config.get("machine_rate_ha_per_day"), 3)
    if not rate or rate <= 0:
        notes.append(f"config machine_rate_ha_per_day missing or not positive: using {DEFAULT_MACHINE_RATE}")
        rate = DEFAULT_MACHINE_RATE
    plans = _plan_rows(data, notes)

    # 1. harvest progress
    try:
        progress = harvest_progress(meta, recs, today, season_start)
    except Exception as e:
        progress = {}
        notes.append(f"harvest progress failed: {type(e).__name__}: {e}")

    # 2. combine demand
    try:
        demand, per_field_demand = combine_demand(meta, recs, today, horizon, rate, data.get("chcs"), data.get("weather"), notes)
    except Exception as e:
        demand, per_field_demand = None, {}
        notes.append(f"combine demand failed: {type(e).__name__}: {e}")

    # 3. zones
    try:
        zones = zone_summary(meta, recs, latest, plans, data, today, notes)
    except Exception as e:
        zones = {}
        notes.append(f"zone summary failed: {type(e).__name__}: {e}")

    # 4. USDA
    try:
        usda = usda_comparison(data.get("usda"), progress, today, notes)
    except Exception as e:
        usda = None
        notes.append(f"USDA comparison failed: {type(e).__name__}: {e}")

    # 5. per-field rows and headline numbers
    fields_out, by_crop, by_stage = {}, Counter(), Counter()
    harvested_ha = unknown_ha = 0.0
    for fid, m in meta.items():
        r = recs.get(fid) or {}
        stage = r.get("stage") if r.get("stage") in STANDING + ("harvested", "unknown") else "unknown"
        hd = _harvest_date(r, today)
        by_crop[m["crop"]] += m["area_ha"]
        if hd is not None:
            harvested_ha += m["area_ha"]
        elif stage in STANDING:
            by_stage[stage] += m["area_ha"]
        else:
            unknown_ha += m["area_ha"]
        lp = latest.get(fid)
        fields_out[fid] = {"zone_id": m["zone_id"], "crop": m["crop"], "area_ha": _num(m["area_ha"], 2), "stage": stage,
                           "harvest_date": _iso(hd), "latest_pass": _iso(lp[0]) if lp else None,
                           "latest_ndvi": lp[1] if lp else None,
                           "flag": (plans.get(fid) or {}).get("flag") if plans is not None else None,
                           "demand_ha_per_day": per_field_demand.get(fid)}
    ha_total = sum(by_crop.values())
    standing_ha = sum(by_stage.values())
    flags = None
    if plans is not None:
        c = Counter(p["flag"] for f, p in plans.items() if f in meta and p["flag"])
        flags = {"act_now": c["act_now"], "watch": c["watch"], "fine": c["fine"]}
        orphans = [f for f in plans if f not in meta]
        if orphans:
            notes.append(f"{len(orphans)} plans are for farms not in fields.geojson and are not counted")
    route = _route_counts(data)
    if route is None:
        notes.append("route.json not given: no stop counts")

    landscape = {
        "as_of": _iso(today), "season_start": _iso(season_start), "latest_pass": _iso(latest_pass),
        "fields": len(meta), "ha_total": _num(ha_total, 1),
        "ha_by_crop": {c: _num(v, 1) for c, v in sorted(by_crop.items())},
        "harvested_ha": _num(harvested_ha, 1), "standing_ha": _num(standing_ha, 1), "unknown_ha": _num(unknown_ha, 1),
        "standing_by_stage": {st: _num(by_stage[st], 1) for st in STANDING},
        "pct_harvested_area": _pct(harvested_ha, ha_total, 1),
        "pct_harvested_cropped_area": _pct(harvested_ha, ha_total - unknown_ha, 1),
        "flags": flags,
        "route_stops": route["stops"] if route else None,
        "route_visits": route["visits"] if route else None,
        "route_calls": route["calls"] if route else None,
        "harvest_progress": progress, "combine_demand": demand, "usda_comparison": usda,
    }
    try:
        landscape["narrative"] = _narrative(landscape, demand, zones, today)
    except Exception as e:
        landscape["narrative"] = (f"Across {len(meta)} fields the season summary could not be written. The numbers above still "
                                  f"stand. Reason: {type(e).__name__}.")
        notes.append(f"narrative failed: {type(e).__name__}: {e}")
    landscape["notes"] = notes

    assumptions = dict(ASSUMPTIONS_STATIC)
    assumptions["machine_rate_ha_per_day"] = f"{rate} ha per combine per day (config/region.json machine_rate_ha_per_day, an ASSUMPTION there)"
    assumptions["horizon_days"] = f"{horizon} days (config/region.json forecast_horizon_days)"
    assumptions["season_day_zero"] = f"{_iso(season_start)} (config/region.json season_start): day 0 of the logistic fit"
    return {"key": KEY, "fields": fields_out, "landscape": landscape, "zones": zones, "assumptions": assumptions, "method": METHOD}
