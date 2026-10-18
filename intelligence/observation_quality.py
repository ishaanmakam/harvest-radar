"""How much the satellite actually saw of each field this season, and how fresh that look is. Module key "coverage".

A pass is one scheduled satellite date in timeseries.json. A pass is clear for a field when the field has an NDVI
reading on that date: the reading is null when the pass was cloudy over the field (its cloud-free share, clear_frac,
is low on those passes too), so a missing reading is a cloudy look, not a missing file. From the clear passes:

  clear_passes, total_passes, clear_fraction_season   how often the field was seen
  last_clear_date, days_since_last_clear, staleness   how fresh the latest look is: fresh / aging / stale
  longest_gap_days                                     the longest stretch without a clear look: between clear passes,
                                                       from the first scheduled pass, or from the last clear pass to as_of
  mean_clear_frac                                      the field's average cloud-free share over every pass
  data_confidence                                      high / medium / low from pass count, longest gap and staleness
  p_next_pass_clear, expected_days_to_next_clear       the zone's season clear rate, and the nominal revisit divided by it

The landscape block repeats this by month, by zone and by date, with the share of fields fresh / aging / stale and the
latest clear pass over the box. Nothing here reads weather, fires or vision: it is bookkeeping on timeseries.json, so
the other modules (and the officer) can tell a quiet field from a field nobody has looked at.

    analyze(data, config, prior=None) -> {"key": "coverage", "fields": {...}, "landscape": {...}, "zones": {...},
                                          "assumptions": {...}, "method": "..."}
"""
from collections import Counter, OrderedDict
from datetime import date

import numpy as np

KEY = "coverage"

REVISIT_DAYS = 5.0            # Sentinel-2A/B combined nominal revisit at the equator (ESA Sentinel-2 mission guide);
                              # mid-latitudes such as Iowa get overlapping swaths and shorter waits, so 5 is conservative.
                              # ASSUMPTION: the wait for the next clear look scales as revisit / clear rate.
FRESH_MAX_DAYS = 6            # ASSUMPTION: a look within about one revisit is fresh
AGING_MAX_DAYS = 12           # ASSUMPTION: within about two revisits it is aging; older is stale
HIGH_MIN_CLEAR_PASSES = 15    # ASSUMPTION: about half the season's passes seen clear
HIGH_MAX_GAP_DAYS = 15        # ASSUMPTION: no stretch longer than three revisits without a look
LOW_MAX_CLEAR_PASSES = 8      # ASSUMPTION: fewer clear passes than this is low confidence
LOW_GAP_DAYS = 30             # ASSUMPTION: a month or more without a look is low confidence
MIN_CLEAR_RATE = 0.05         # ASSUMPTION: floor on a clear rate when estimating the wait (caps the wait at 100 days, never divides by zero)
BOX_CLEAR_SHARE = 0.5         # ASSUMPTION: a pass is clear over the box when at least half the fields with data were clear
DEFAULT_CLEAR_FRACTION_MIN = 0.6   # thresholds.clear_fraction_min in config/region.json; used only for a series with clear_frac but no ndvi

STALENESS = ("fresh", "aging", "stale")
CONFIDENCE = ("high", "medium", "low")

ASSUMPTIONS = {
    "clear_pass": "a pass is clear for a field when its NDVI reading is present; a null reading is a cloudy look at the field "
                  "(its cloud-free share is low on those passes), not a missing file",
    "revisit_days": f"{REVISIT_DAYS:.0f} days, the Sentinel-2A/B combined nominal revisit at the equator (ESA); mid-latitudes get "
                    "overlapping swaths and shorter waits, so this is conservative; ASSUMPTION that the expected wait for a clear "
                    "look is revisit / the zone's season clear rate",
    "staleness": f"fresh <= {FRESH_MAX_DAYS} days since the last clear look, aging <= {AGING_MAX_DAYS}, else stale (ASSUMPTION: "
                 "about one and two revisits); a field never seen clear is stale",
    "data_confidence": f"high: >= {HIGH_MIN_CLEAR_PASSES} clear passes and longest gap <= {HIGH_MAX_GAP_DAYS} days; low: < "
                       f"{LOW_MAX_CLEAR_PASSES} clear passes, a gap > {LOW_GAP_DAYS} days, or stale; otherwise medium (ASSUMPTION)",
    "longest_gap": "the longest stretch of days without a clear look at the field: between clear passes, from the first "
                   "scheduled pass to the first clear one, or from the last clear pass to as_of",
    "box_clear_pass": f"a pass is clear over the box when at least {BOX_CLEAR_SHARE:.0%} of the fields with data were clear "
                      "on it (ASSUMPTION)",
    "zone_clear_rate": "clear field-passes divided by all field-passes of the fields in the zone this season (pooled, not "
                       "a mean of per-field rates); a field without a zone uses the whole-box rate",
}

METHOD = ("For every field we count the satellite passes this season and how many of them gave a clear look, when the "
          "last clear look was, and the longest stretch with no look at all. From that each field gets a freshness "
          "(fresh, aging or stale) and a data confidence (high, medium or low), plus the zone's season clear rate as the "
          "chance the next pass is clear and the nominal five-day revisit divided by that rate as the expected wait. The "
          "landscape view rolls the same counts up by month, zone and date, so a quiet plan can be told apart from a "
          "field nobody has seen for weeks.")


# ----------------------------------------------------------------------------------------------------------- helpers

def _num(x, nd=3):
    """A plain rounded float, or None for NaN / inf / non-numbers."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, nd)


def _is_reading(v):
    """A usable index value: a finite number, not a bool, not None."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)


def _parse_date(x):
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _today(config, dates, notes):
    """as_of from config/region.json; the last scheduled pass when it is missing or unreadable."""
    t = _parse_date((config or {}).get("as_of"))
    if t is None:
        notes.append("config has no readable as_of: freshness is measured from the last scheduled pass")
        t = dates[-1][0] if dates else None
    return t


def _scheduled(ts, notes):
    """[(date, index)] of the scheduled passes, sorted by date; unreadable dates are dropped with a note."""
    raw = ts.get("dates") if isinstance(ts, dict) else None
    if not isinstance(raw, list):
        return []
    out, bad = [], 0
    for i, x in enumerate(raw):
        dt = _parse_date(x)
        if dt is None:
            bad += 1
        else:
            out.append((dt, i))
    if bad:
        notes.append(f"{bad} timeseries date(s) unreadable and skipped")
    out.sort()
    return out


def _roster(data, ts_fields):
    """Ordered field ids and field -> zone. fields.geojson first (the farm roster), then ids only status or the
    timeseries know about."""
    ids, zone_of = [], {}

    def add(fid, zone):
        if not fid or not isinstance(fid, str):
            return
        if fid not in zone_of:
            ids.append(fid)
            zone_of[fid] = zone if isinstance(zone, str) else None
        elif zone_of[fid] is None and isinstance(zone, str):
            zone_of[fid] = zone

    fg = data.get("fields")
    for feat in (fg.get("features") if isinstance(fg, dict) else None) or []:
        p = feat.get("properties") if isinstance(feat, dict) else None
        if isinstance(p, dict):
            add(p.get("field_id"), p.get("zone_id"))
    st = data.get("status")
    for rec in (st.get("fields") if isinstance(st, dict) else None) or []:
        if isinstance(rec, dict):
            add(rec.get("field_id"), rec.get("zone_id"))
    for fid in ts_fields:
        add(fid, None)
    return ids, zone_of


def _zone_ids(data, zone_of):
    """Every zone we should report: zones.geojson first, then any zone a field names."""
    out = []
    zg = data.get("zones")
    for feat in (zg.get("features") if isinstance(zg, dict) else None) or []:
        p = feat.get("properties") if isinstance(feat, dict) else None
        z = p.get("zone_id") if isinstance(p, dict) else None
        if isinstance(z, str) and z not in out:
            out.append(z)
    for z in zone_of.values():
        if isinstance(z, str) and z not in out:
            out.append(z)
    return out


def _matrices(ts_fields, ids, scheduled, n_raw, cf_min, notes):
    """clear[i, j]  field i was seen clear on scheduled pass j
       cf[i, j]     the field's cloud-free share on that pass (NaN where absent)
       has[i]       the field has a usable series at all"""
    n, m = len(ids), len(scheduled)
    clear = np.zeros((n, m), dtype=bool)
    cf = np.full((n, m), np.nan)
    has = np.zeros(n, dtype=bool)
    misaligned, missing, no_ndvi = 0, 0, 0
    for i, fid in enumerate(ids):
        sr = ts_fields.get(fid)
        if not isinstance(sr, dict):
            missing += 1
            continue
        ndvi = sr.get("ndvi") if isinstance(sr.get("ndvi"), list) else None
        frac = sr.get("clear_frac") if isinstance(sr.get("clear_frac"), list) else None
        if ndvi is None and frac is None:
            missing += 1
            continue
        has[i] = True
        for j, (_, idx) in enumerate(scheduled):
            if frac is not None and idx < len(frac) and _is_reading(frac[idx]):
                cf[i, j] = float(frac[idx])
            if ndvi is not None:
                clear[i, j] = idx < len(ndvi) and _is_reading(ndvi[idx])
            else:
                clear[i, j] = np.isfinite(cf[i, j]) and cf[i, j] >= cf_min
        if ndvi is None:
            no_ndvi += 1
        elif len(ndvi) != n_raw:
            misaligned += 1
    if missing:
        notes.append(f"{missing} field(s) absent from the timeseries (or with no usable series): coverage None for them")
    if misaligned:
        notes.append(f"{misaligned} field(s) have an ndvi array shorter or longer than the dates: passes without a value count as not clear")
    if no_ndvi:
        notes.append(f"{no_ndvi} field(s) have clear_frac but no ndvi: clear = clear_frac >= {cf_min}")
    return clear, cf, has


def _longest_gap(clear_ords, first_ord, today_ord):
    """Longest stretch of days without a clear look, from the first scheduled pass through as_of."""
    edges = np.concatenate(([first_ord], clear_ords, [max(today_ord, clear_ords[-1] if len(clear_ords) else first_ord)]))
    return int(np.diff(edges).max()) if len(edges) > 1 else 0


def _staleness(days_since):
    if days_since is None:
        return "stale"
    if days_since <= FRESH_MAX_DAYS:
        return "fresh"
    if days_since <= AGING_MAX_DAYS:
        return "aging"
    return "stale"


def _confidence(clear_passes, longest_gap, staleness):
    if clear_passes < LOW_MAX_CLEAR_PASSES or longest_gap > LOW_GAP_DAYS or staleness == "stale":
        return "low"
    if clear_passes >= HIGH_MIN_CLEAR_PASSES and longest_gap <= HIGH_MAX_GAP_DAYS:
        return "high"
    return "medium"


def _expected_wait(rate):
    if rate is None:
        return None
    return _num(REVISIT_DAYS / max(float(rate), MIN_CLEAR_RATE), 1)


def _field(row, cf_row, dates, today, rate):
    """One field's coverage from its boolean clear row and cloud-free shares."""
    total = int(len(row))
    idx = np.flatnonzero(row)
    n_clear = int(len(idx))
    ords = np.array([dates[j].toordinal() for j in idx], dtype=int)
    first_ord = dates[0].toordinal()
    today_ord = today.toordinal()
    last = dates[idx[-1]] if n_clear else None
    days_since = max(0, today_ord - last.toordinal()) if last else None
    gap = _longest_gap(ords, first_ord, today_ord) if total else None
    stale = _staleness(days_since)
    conf = _confidence(n_clear, gap if gap is not None else 10 ** 6, stale)
    known = cf_row[np.isfinite(cf_row)]
    if n_clear:
        ago = "today" if days_since == 0 else ("1 day ago" if days_since == 1 else f"{days_since} days ago")
        note = f"Seen clear on {n_clear} of {total} passes; last clear look {last.isoformat()} ({ago}); longest gap {gap} days."
    else:
        # No last clear date exists, so days_since_last_clear stays None; the note carries the number to say out loud.
        note = f"Never seen clear this season (0 of {total} passes): {gap} days without a clear look."
    return {
        "clear_passes": n_clear,
        "total_passes": total,
        "clear_fraction_season": _num(n_clear / total, 3) if total else None,
        "last_clear_date": last.isoformat() if last else None,
        "days_since_last_clear": days_since,
        "longest_gap_days": gap,
        "mean_clear_frac": _num(known.mean(), 3) if len(known) else None,
        "staleness": stale,
        "data_confidence": conf,
        "p_next_pass_clear": _num(rate, 3),
        "expected_days_to_next_clear": _expected_wait(rate),
        "note": note,
    }


def _share(counter, labels, n):
    return {k: (_num(counter.get(k, 0) / n, 3) if n else None) for k in labels}


def _empty_landscape(zone_ids, n_ids, today):
    """The full landscape key set for a season with no scheduled passes, so consumers find every key either way."""
    return {
        "passes_total": 0,
        "first_pass_date": None,
        "last_pass_date": None,
        "as_of": today.isoformat() if today else None,
        "fields": 0,
        "fields_without_series": int(n_ids),
        "clear_rate_season": None,
        "latest_clear_date": None,
        "days_since_latest_clear": None,
        "latest_box_clear_date": None,
        "box_clear_passes": 0,
        "by_month": {},
        "clear_rate_by_zone": {z: None for z in zone_ids},
        "clear_share_by_date": {},
        "staleness_counts": {k: 0 for k in STALENESS},
        "staleness_share": {k: None for k in STALENESS},
        "confidence_counts": {k: 0 for k in CONFIDENCE},
        "confidence_share": {k: None for k in CONFIDENCE},
    }


# ----------------------------------------------------------------------------------------------------------- analyze

def analyze(data, config, prior=None):
    """Coverage and freshness per field, per zone and for the landscape. Never raises: whatever cannot be computed is
    None with a note in landscape["notes"]. prior is accepted for the module contract and not needed here."""
    notes = []
    out = {"key": KEY, "fields": {}, "landscape": {"notes": notes}, "zones": {}, "assumptions": dict(ASSUMPTIONS), "method": METHOD}
    try:
        _fill(out, data if isinstance(data, dict) else {}, config if isinstance(config, dict) else {}, notes)
    except Exception as e:                       # a broken input must never cost the morning run
        notes.append(f"coverage could not be computed: {type(e).__name__}: {e}")
    return out


def _fill(out, data, config, notes):
    ts = data.get("timeseries") if isinstance(data.get("timeseries"), dict) else {}
    ts_fields = ts.get("fields") if isinstance(ts.get("fields"), dict) else {}
    ids, zone_of = _roster(data, ts_fields)
    zone_ids = _zone_ids(data, zone_of)
    scheduled = _scheduled(ts, notes)
    landscape = out["landscape"]

    def empty_zones():
        return {z: {"fields": 0, "clear_rate": None, "fields_fresh": 0, "fields_aging": 0, "fields_stale": 0,
                    "p_next_pass_clear": None, "expected_days_to_next_clear": None, "last_clear_date": None} for z in zone_ids}

    if not scheduled:
        notes.append("no timeseries dates: coverage is unknown for every field")
        today = _today(config, scheduled, notes)
        out["fields"] = {fid: None for fid in ids}
        out["zones"] = empty_zones()
        landscape.update(_empty_landscape(zone_ids, len(ids), today))
        return

    today = _today(config, scheduled, notes)
    th = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else {}
    cf_min = th.get("clear_fraction_min")
    cf_min = float(cf_min) if _is_reading(cf_min) else DEFAULT_CLEAR_FRACTION_MIN
    dates = [dt for dt, _ in scheduled]
    n_raw = len(ts.get("dates") or [])
    clear, cf, has = _matrices(ts_fields, ids, scheduled, n_raw, cf_min, notes)
    zones_arr = np.array([zone_of.get(fid) or "" for fid in ids], dtype=object)

    # season clear rates: pooled field-passes, per zone and for the box
    box_rate = _num(clear[has].mean(), 4) if has.any() else None
    zone_rate = {}
    for z in zone_ids:
        mask = has & (zones_arr == z)
        zone_rate[z] = _num(clear[mask].mean(), 4) if mask.any() else None

    # per field
    fields = {}
    stale_by_zone = {z: Counter() for z in zone_ids}
    last_by_zone = {z: None for z in zone_ids}
    stale_all, conf_all = Counter(), Counter()
    for i, fid in enumerate(ids):
        if not has[i]:
            fields[fid] = None
            continue
        z = zone_of.get(fid)
        rate = zone_rate.get(z) if z in zone_rate and zone_rate[z] is not None else box_rate
        try:
            rec = _field(clear[i], cf[i], dates, today, rate)
        except Exception as e:                   # one odd series must not take the others down
            notes.append(f"{fid}: coverage not computed ({type(e).__name__})")
            fields[fid] = None
            continue
        fields[fid] = rec
        stale_all[rec["staleness"]] += 1
        conf_all[rec["data_confidence"]] += 1
        if z in stale_by_zone:
            stale_by_zone[z][rec["staleness"]] += 1
            if rec["last_clear_date"] and (last_by_zone[z] is None or rec["last_clear_date"] > last_by_zone[z]):
                last_by_zone[z] = rec["last_clear_date"]
    out["fields"] = fields
    n_done = sum(1 for v in fields.values() if v is not None)
    unzoned = sum(1 for fid, v in fields.items() if v is not None and not zone_of.get(fid))
    if unzoned:
        notes.append(f"{unzoned} field(s) have no zone: their next-pass odds use the whole-box clear rate")

    # zones
    zones = {}
    for z in zone_ids:
        mask = has & (zones_arr == z)
        zones[z] = {
            "fields": int(mask.sum()),
            "clear_rate": zone_rate[z],
            "fields_fresh": int(stale_by_zone[z]["fresh"]),
            "fields_aging": int(stale_by_zone[z]["aging"]),
            "fields_stale": int(stale_by_zone[z]["stale"]),
            "p_next_pass_clear": zone_rate[z],
            "expected_days_to_next_clear": _expected_wait(zone_rate[z]),
            "last_clear_date": last_by_zone[z],
        }
    out["zones"] = zones

    # landscape: by date, by month, latest clear pass over the box, shares
    share = np.full(len(dates), np.nan)
    if has.any():
        share = clear[has].mean(axis=0)
    by_date = OrderedDict((dt.isoformat(), _num(share[j], 3)) for j, dt in enumerate(dates))
    any_clear = [dt for j, dt in enumerate(dates) if np.isfinite(share[j]) and share[j] > 0]
    box_clear = [dt for j, dt in enumerate(dates) if np.isfinite(share[j]) and share[j] >= BOX_CLEAR_SHARE]
    by_month = OrderedDict()
    for j, dt in enumerate(dates):
        k = dt.strftime("%Y-%m")
        m = by_month.setdefault(k, {"passes": 0, "box_clear_passes": 0, "_cols": []})
        m["passes"] += 1
        m["box_clear_passes"] += int(np.isfinite(share[j]) and share[j] >= BOX_CLEAR_SHARE)
        m["_cols"].append(j)
    for k, m in by_month.items():
        cols = m.pop("_cols")
        m["clear_rate"] = _num(clear[has][:, cols].mean(), 3) if has.any() else None
    latest_clear = any_clear[-1] if any_clear else None
    landscape.update({
        "passes_total": len(dates),
        "first_pass_date": dates[0].isoformat(),
        "last_pass_date": dates[-1].isoformat(),
        "as_of": today.isoformat() if today else None,
        "fields": n_done,
        "fields_without_series": len(ids) - n_done,
        "clear_rate_season": box_rate,
        "latest_clear_date": latest_clear.isoformat() if latest_clear else None,
        "days_since_latest_clear": max(0, (today - latest_clear).days) if (latest_clear and today) else None,
        "latest_box_clear_date": box_clear[-1].isoformat() if box_clear else None,
        "box_clear_passes": len(box_clear),
        "by_month": by_month,
        "clear_rate_by_zone": {z: zone_rate[z] for z in zone_ids},
        "clear_share_by_date": by_date,
        "staleness_counts": {k: int(stale_all.get(k, 0)) for k in STALENESS},
        "staleness_share": _share(stale_all, STALENESS, n_done),
        "confidence_counts": {k: int(conf_all.get(k, 0)) for k in CONFIDENCE},
        "confidence_share": _share(conf_all, CONFIDENCE, n_done),
    })
    if latest_clear is None:
        notes.append("no field was seen clear on any pass this season")
