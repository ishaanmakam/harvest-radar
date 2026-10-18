"""Which fields behave unlike their neighbours.

Every field is scored on every clear satellite pass against its cohort, the fields of the same crop in the same
zone (same crop across the landscape when the zone holds fewer than five comparable fields):

    z = (NDVI - cohort median) / (1.4826 * MAD + 0.02)

a robust z score: how far the field sits from the middle of its cohort, in units of the cohort's own spread, with a
small floor so a uniform cohort does not turn sensor noise into outliers. Four readings come out of the z track:

    chronic_low        z <= -1.5 on the field's last two clear passes
    sudden_drop        z fell by >= 1.5 between the last two clear passes while the cohort median held (fell < 0.05);
                       both z are taken over the cohort members clear on both passes, the field's own NDVI must have
                       fallen by at least 0.05, and it must end at or below the cohort middle, so a cohort that lost
                       its brown members to harvest, or a green outlier coming back to the pack, is not a drop
    early_senescence   the field began browning (first post-peak pass from which NDVI stays at or below 90% of its
                       peak) more than 7 days before the median onset of its cohort
    outlier_high       latest z >= 2.0: much greener than its neighbours

The first that applies names the field (anomaly_type); the note says it in plain words. Harvested fields are compared
on their pre-harvest passes only, and only those passes feed the cohort median, so a cohort half-way through harvest
does not make every standing field look odd. A harvested field keeps its record as history: "harvested" is true, the
note is in the past tense and carries no instruction, and the landscape list puts standing fields first. Nothing
here reads the calendar beyond config as_of, and nothing reads the weather.

    analyze(data, config, prior=None) -> {"key": "anomaly", "fields": {field_id: {...} | None}, "landscape": {...},
                                          "zones": {zone_id: {...}}, "assumptions": {...}, "method": "..."}

Inputs used: timeseries.json (ndvi per pass), fields.geojson (zone, crop), status.json (harvest_date), zones.geojson
(zone list), config thresholds.peak_ndvi_min. A field that cannot be compared gets None and a line in
landscape["notes"]; bad input never raises.
"""
import math
from collections import defaultdict
from datetime import date

import numpy as np

KEY = "anomaly"
TYPES = ("chronic_low", "sudden_drop", "early_senescence", "outlier_high")   # priority order
NONE = "none"

MIN_COHORT_FIELDS = 5          # task spec: fewer comparable same-crop fields in the zone -> same crop landscape-wide
MIN_PASS_VALUES = MIN_COHORT_FIELDS  # ASSUMPTION: a pass needs as many cohort values as the cohort rule asks for before a
                               # median and a MAD mean anything; with 3 or 4 the MAD collapses and every field looks odd
MAD_SCALE = 1.4826             # MAD -> standard deviation for a normal distribution (textbook constant)
Z_FLOOR = 0.02                 # task spec: NDVI noise floor added to the scale
LOW_Z = -1.5                   # task spec: chronic low threshold on the last two clear passes
DROP_Z = 1.5                   # task spec: fall in z between the last two clear passes that counts as sudden
COHORT_STEADY_FALL = 0.05      # task spec: the cohort median fell less than this = the cohort held steady
MIN_OWN_FALL_NDVI = 0.05       # ASSUMPTION: a sudden drop needs the field's own NDVI to fall by at least this between the
                               # two passes; a smaller move is within what sensor and sky do to NDVI from one pass to the next
SUDDEN_DROP_MAX_GAP_DAYS = 20  # ASSUMPTION: two clear passes further apart than this (4 Sentinel-2 revisits) are not "sudden"
SENESCENCE_FRACTION = 0.9      # task spec: onset = first post-peak pass at or below this share of the field's peak, and
                               # ASSUMPTION: from which it stays there, so a dip that climbs back to green does not date it
EARLY_DAYS = 7                 # task spec: onset more than this many whole days before the cohort median onset = early
HIGH_Z = 2.0                   # task spec: outlier_high threshold on the latest z
ZONE_WIDE_SHARE = 0.30         # task spec: this share of the cohort flagged = zone_wide
DEFAULT_PEAK_NDVI_MIN = 0.55   # config thresholds.peak_ndvi_min; a field that never reached it has no crop to senesce
TOP_N = 10                     # task spec: most anomalous fields listed on the landscape

ASSUMPTIONS = {
    "cohort": f"same crop and same zone; fewer than {MIN_COHORT_FIELDS} comparable fields falls back to the same crop "
              f"across the landscape (task spec)",
    "robust_z": f"(NDVI - cohort median) / ({MAD_SCALE} * MAD + {Z_FLOOR}) on each clear pass; the floor keeps a tight "
                f"cohort from turning sensor noise into outliers (task spec)",
    "min_pass_values": f"ASSUMPTION: a pass needs at least {MIN_PASS_VALUES} cohort values before a z is computed (the "
                       f"same five the cohort rule asks for); a field's latest z is from its last pass that has one",
    "persistent_low": f"z <= {LOW_Z} on the field's last two clear passes (task spec)",
    "sudden_drop": f"z fell by >= {DROP_Z} between the last two clear passes while the cohort median fell < "
                   f"{COHORT_STEADY_FALL} (task spec), with both z taken over the cohort members clear on both passes "
                   f"so a change of membership is not a fall; ASSUMPTION: the field's own NDVI must have fallen by >= "
                   f"{MIN_OWN_FALL_NDVI} and its latest z must be <= 0, and passes more than {SUDDEN_DROP_MAX_GAP_DAYS} "
                   f"days apart are not judged",
    "early_senescence": f"onset = first post-peak clear pass with NDVI <= {SENESCENCE_FRACTION} * peak; early when more "
                        f"than {EARLY_DAYS} whole days before the cohort's median onset (task spec); ASSUMPTION: the "
                        f"peak is the field's own highest clear pass, only fields that reached thresholds.peak_ndvi_min "
                        f"have an onset, and the NDVI must stay at or below the line from the onset on, so a dip that "
                        f"climbs back to green does not date it",
    "outlier_high": f"latest z >= {HIGH_Z} (task spec)",
    "event_scope": f"zone_wide when >= {ZONE_WIDE_SHARE:.0%} of the cohort is flagged, else isolated (task spec); the "
                   f"share is over the same members the median and MAD came from, so a crop-wide fallback cohort "
                   f"counts every field of the crop",
    "harvested": "passes on or after status.json harvest_date are dropped, for the field and for the cohort median "
                 "it feeds (task spec); a field whose harvest_date is on or before config as_of is marked harvested, "
                 "its note is in the past tense with no instruction, and standing fields come first in the top list",
    "rank_in_zone": "ASSUMPTION: by the latest clear NDVI of every same-crop field in the zone, harvested fields "
                    "included (1 = greenest; ties share a rank)",
}

METHOD = (
    "Every field is scored against its cohort, the fields of the same crop in the same zone (or across the landscape "
    "when the zone has fewer than five), on every clear satellite pass: how far its greenness sits from the cohort "
    "median, in units of the cohort's own spread. A field well below its neighbours on its last two passes is "
    "chronic_low; one whose own greenness fell sharply while its neighbours held is a sudden_drop; one that began "
    "browning more than a week before the rest is early_senescence; one much greener than the rest is outlier_high. "
    "Harvested fields are compared only before harvest, so a cohort's own harvest does not make the standing fields "
    "look odd, and a field already harvested keeps its record as history with no instruction attached. When a third "
    "or more of a cohort is flagged the event is zone wide, which points to weather or disease rather than one farm's "
    "problem."
)


# ----------------------------------------------------------------------------------------------------------- helpers

def _date(x):
    """ISO date (a datetime is cut to its day) or None."""
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _num(x):
    """Finite float or None (bools and strings are not measurements)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, digits=2):
    """Rounded plain float, or None."""
    v = _num(x)
    return None if v is None else round(v, digits)


def _iso(d):
    return d.isoformat() if d else None


def field_table(fields_doc, status_doc, ts_doc, notes):
    """field_id -> {zone, crop} in fields.geojson order. Without fields.geojson the zones come from status.json and
    the crop is unknown; with neither, every series in timeseries.json is a field in one unknown zone."""
    table = {}
    feats = fields_doc.get("features") if isinstance(fields_doc, dict) else None
    for f in feats or []:
        p = (f or {}).get("properties") if isinstance(f, dict) else None
        if isinstance(p, dict) and p.get("field_id"):
            crop = p.get("crop")
            table[str(p["field_id"])] = {"zone": p.get("zone_id"), "crop": str(crop).lower() if crop else "unknown"}
    if table:
        return table
    notes.append("fields.geojson missing or empty: zones from status.json, crop unknown for every field")
    recs = status_doc.get("fields") if isinstance(status_doc, dict) else None
    for r in recs or []:
        if isinstance(r, dict) and r.get("field_id"):
            table[str(r["field_id"])] = {"zone": r.get("zone_id"), "crop": "unknown"}
    if not table and isinstance(ts_doc, dict) and isinstance(ts_doc.get("fields"), dict):
        for fid in ts_doc["fields"]:
            table[str(fid)] = {"zone": None, "crop": "unknown"}
    return table


def parse_timeseries(ts_doc, notes):
    """(dates, field_id -> ndvi list aligned with dates). Unreadable dates and non-numeric values read as cloudy."""
    if not isinstance(ts_doc, dict) or not isinstance(ts_doc.get("dates"), list) or not ts_doc["dates"]:
        notes.append("timeseries.json missing or empty: no field can be compared")
        return [], {}
    dates = [_date(x) for x in ts_doc["dates"]]
    if any(d is None for d in dates):
        notes.append("timeseries.json has an unreadable pass date: that pass is skipped")
    series = {}
    for fid, rec in (ts_doc.get("fields") or {}).items():
        raw = rec.get("ndvi") if isinstance(rec, dict) else None
        raw = list(raw) if isinstance(raw, (list, tuple)) else []
        vals = ([_num(v) for v in raw] + [None] * len(dates))[:len(dates)]
        series[str(fid)] = [v if d is not None else None for v, d in zip(vals, dates)]
    return dates, series


def harvest_dates(status_doc):
    """field_id -> harvest date from status.json (fields without one are standing or unknown)."""
    out = {}
    recs = status_doc.get("fields") if isinstance(status_doc, dict) else None
    for r in recs or []:
        if isinstance(r, dict) and r.get("field_id") and r.get("harvest_date"):
            h = _date(r["harvest_date"])
            if h:
                out[str(r["field_id"])] = h
    return out


def usable_passes(vals, dates, harvest):
    """Indices of the clear passes a field is compared on: NDVI present, and before the harvest when it has one."""
    return [j for j, v in enumerate(vals) if v is not None and (harvest is None or dates[j] < harvest)]


def build_cohorts(table, usable):
    """(field_id -> cohort key, cohort key -> member ids, keys that fell back). A cohort is "crop/zone"; a zone with
    fewer than MIN_COHORT_FIELDS comparable fields of that crop sends them to "crop/all", whose members are every
    comparable field of the crop."""
    by_zone, by_crop = defaultdict(list), defaultdict(list)
    for fid, idx in usable.items():
        if len(idx) >= 2:
            crop, zone = table[fid]["crop"], table[fid]["zone"]
            by_zone[(crop, zone)].append(fid)
            by_crop[crop].append(fid)
    key_of, members, fallback = {}, {}, []
    for (crop, zone), fids in by_zone.items():
        if zone is not None and len(fids) >= MIN_COHORT_FIELDS:
            key = f"{crop}/{zone}"
            members[key] = fids
        else:
            key = f"{crop}/all"
            members[key] = by_crop[crop]
            fallback.append(f"{crop}/{zone}")
        for fid in fids:
            key_of[fid] = key
    return key_of, members, fallback


def cohort_matrix(members, series, usable, n_dates):
    """members x passes NDVI matrix, NaN where a member has no usable value on a pass."""
    arr = np.full((len(members), n_dates), np.nan)
    for i, fid in enumerate(members):
        for j in usable[fid]:
            arr[i, j] = series[fid][j]
    return arr


def robust_z(v):
    """(z per value, median) for one set of cohort values: distance from the median in MAD units, with the floor."""
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    return (v - med) / (MAD_SCALE * mad + Z_FLOOR), med


def cohort_z(arr):
    """(z per member and pass (NaN where none), cohort median per pass (None where fewer than MIN_PASS_VALUES))."""
    z = np.full(arr.shape, np.nan)
    medians = [None] * arr.shape[1]
    for j in range(arr.shape[1]):
        ok = ~np.isnan(arr[:, j])
        if int(ok.sum()) < MIN_PASS_VALUES:
            continue
        z[ok, j], medians[j] = robust_z(arr[ok, j])
    return z, medians


def paired_z(arr, j1, j2):
    """z on passes j1 and j2 over the members clear on both, so the two numbers describe the same cohort and a change
    between them is a change in the field, not in who was under clear sky. (z1, z2 per member, NaN for members outside
    the pair; median on j1; median on j2), or None when fewer than MIN_PASS_VALUES members are clear on both."""
    ok = ~np.isnan(arr[:, j1]) & ~np.isnan(arr[:, j2])
    if int(ok.sum()) < MIN_PASS_VALUES:
        return None
    z1, z2 = np.full(len(arr), np.nan), np.full(len(arr), np.nan)
    z1[ok], med1 = robust_z(arr[ok, j1])
    z2[ok], med2 = robust_z(arr[ok, j2])
    return z1, z2, med1, med2


def senescence_onset(vals, idx, peak_min):
    """(onset pass index or None, peak NDVI). The onset is the first usable pass after the field's highest one from
    which NDVI stays at or below SENESCENCE_FRACTION * peak: the start of the final decline, not a mid-season dip.
    A field whose peak never reached peak_min has no crop to senesce: (None, peak) with the caller deciding it
    cannot be judged."""
    if not idx:
        return None, None
    v = np.array([vals[j] for j in idx], dtype=float)
    k = int(np.argmax(v))
    peak = float(v[k])
    if peak < peak_min:
        return None, peak
    below = v <= SENESCENCE_FRACTION * peak
    for t in range(k + 1, len(idx)):
        if below[t:].all():
            return idx[t], peak
    return None, peak


def _zf(x):
    """z for a note: one decimal with its sign, and never '-0.0'."""
    v = round(float(x), 1)
    return f"{0.0 if v == 0 else v:+.1f}"


def zone_ranks(table, series):
    """field_id -> (rank, n): rank 1 = greenest latest clear NDVI among the same-crop fields of the zone (ties share
    a rank); n = how many of them have a clear pass. Fields without one get (None, n)."""
    latest = {}
    for fid in table:
        vals = series.get(fid) or []
        clear = [v for v in vals if v is not None]
        latest[fid] = clear[-1] if clear else None
    groups = defaultdict(list)
    for fid, meta in table.items():
        groups[(meta["zone"], meta["crop"])].append(fid)
    out = {}
    for fids in groups.values():
        have = [latest[f] for f in fids if latest[f] is not None]
        for fid in fids:
            v = latest[fid]
            rank = None if v is None else 1 + sum(1 for x in have if x > v)
            out[fid] = (rank, len(have))
    return out


def _where(key, crop, zone):
    return f"the other {crop} fields in {zone}" if not key.endswith("/all") else f"the other {crop} fields across the landscape"


def _note(kind, where, d1, z1, d2, z2, n1, n2, onset, cohort_onset, days_early, harvest):
    """One line in plain words for the farmer or the officer. A standing field is told what to do; a harvested field
    (harvest = its ISO harvest date) gets the same finding in the past tense with no instruction, since the crop is in."""
    if harvest:
        lead = f"Before its harvest on {harvest} it "
        if kind == "chronic_low":
            return lead + f"was well below {where} on its last two clear passes ({d1} and {d2})."
        if kind == "sudden_drop":
            return lead + f"fell sharply between {d1} and {d2} (NDVI {n1:.2f} to {n2:.2f}) while {where} held steady."
        if kind == "early_senescence":
            return lead + f"began browning on {onset}, {days_early} days before {where} ({cohort_onset})."
        if kind == "outlier_high":
            return lead + f"was much greener than {where} on {d2}."
        return lead + f"was in step with {where} (last compared on {d2})."
    if kind == "chronic_low":
        return (f"Well below {where} on its last two clear passes (z {_zf(z2)} on {d2}, {_zf(z1)} on {d1}): "
                f"check for a thin stand, weeds or water damage.")
    if kind == "sudden_drop":
        return (f"Fell sharply between {d1} and {d2} (NDVI {n1:.2f} to {n2:.2f}) while {where} held steady: "
                f"walk the field.")
    if kind == "early_senescence":
        return (f"Began browning on {onset}, {days_early} days before {where} ({cohort_onset}): early dry-down, "
                f"disease or drought stress.")
    if kind == "outlier_high":
        return f"Much greener than {where} on {d2} (z {_zf(z2)}): a later crop, still standing, or a different crop."
    return f"In step with {where} (z {_zf(z2)} on {d2})."


# ----------------------------------------------------------------------------------------------------------- analyze

def _empty_landscape(n_fields, notes):
    return {"n_fields": n_fields, "n_compared": 0, "n_flagged": 0,
            "counts": {NONE: 0, **{t: 0 for t in TYPES}, "no_verdict": n_fields},
            "by_zone": {}, "top": [], "cohorts": {}, "notes": notes}


def _zone_ids(zones_doc, table):
    ids = []
    feats = zones_doc.get("features") if isinstance(zones_doc, dict) else None
    for f in feats or []:
        p = (f or {}).get("properties") if isinstance(f, dict) else None
        if isinstance(p, dict) and p.get("zone_id") is not None:
            ids.append(p["zone_id"])
    for meta in table.values():
        if meta["zone"] is not None and meta["zone"] not in ids:
            ids.append(meta["zone"])
    return ids


def _run(data, config, notes):
    """The whole computation; analyze() wraps it so that nothing raises out of the module."""
    table = field_table(data.get("fields"), data.get("status"), data.get("timeseries"), notes)
    zone_ids = _zone_ids(data.get("zones"), table)
    fields_out = {fid: None for fid in table}
    if not table:
        notes.append("no field list at all (fields.geojson, status.json and timeseries.json all empty)")
        return fields_out, _empty_landscape(0, notes), {z: _zone_row(0, 0, {}) for z in zone_ids}

    dates, series = parse_timeseries(data.get("timeseries"), notes)
    thresholds = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else {}
    peak_min = _num(thresholds.get("peak_ndvi_min"))
    peak_min = DEFAULT_PEAK_NDVI_MIN if peak_min is None else peak_min
    harvest = harvest_dates(data.get("status"))
    today = _date(config.get("as_of"))
    if data.get("status") is None:
        notes.append("status.json missing: harvested fields are compared on all their passes")

    usable = {fid: usable_passes(series[fid], dates, harvest.get(fid)) for fid in table if fid in series}
    key_of, members, fallback = build_cohorts(table, usable)
    matrices = {key: cohort_matrix(fids, series, usable, len(dates)) for key, fids in members.items()}
    zstats = {key: cohort_z(arr) for key, arr in matrices.items()}
    row_of = {key: {fid: i for i, fid in enumerate(fids)} for key, fids in members.items()}
    paired = {}                                     # (cohort key, j1, j2) -> paired_z(...) for the drop test
    ranks = zone_ranks(table, series)

    # cohort median onset, from every member that has one
    onsets = {fid: senescence_onset(series[fid], usable[fid], peak_min) for fid in key_of}
    cohort_onset = {}
    for key, fids in members.items():
        ords = [dates[onsets[f][0]].toordinal() for f in fids if onsets[f][0] is not None]
        cohort_onset[key] = float(np.median(ords)) if len(ords) >= MIN_PASS_VALUES else None

    skipped = {"no_series": [], "few_passes": [], "no_peers": []}
    for fid, meta in table.items():
        if fid not in series:
            skipped["no_series"].append(fid)
            continue
        idx = usable[fid]
        if len(idx) < 2:
            skipped["few_passes"].append(fid)
            continue
        key = key_of[fid]
        row = row_of[key][fid]
        zrow = zstats[key][0][row]
        zs = [(j, float(zrow[j])) for j in idx if not np.isnan(zrow[j])]
        if not zs:
            skipped["no_peers"].append(fid)
            continue
        j2, z2 = zs[-1]
        j1, z1 = zs[-2] if len(zs) > 1 else (None, None)
        d2, d1 = dates[j2], (dates[j1] if j1 is not None else None)
        n2, n1 = series[fid][j2], (series[fid][j1] if j1 is not None else None)
        h = harvest.get(fid)
        harvested = h is not None and (today is None or h <= today)

        persistent_low = None if z1 is None else bool(z2 <= LOW_Z and z1 <= LOW_Z)
        # sudden drop: the field itself fell (own NDVI down by MIN_OWN_FALL_NDVI, ending at or below the cohort middle)
        # and its z, taken over the same members on both passes, fell by DROP_Z while that cohort's median held
        sudden_drop = None
        if j1 is not None and (d2 - d1).days <= SUDDEN_DROP_MAX_GAP_DAYS:
            if (key, j1, j2) not in paired:
                paired[(key, j1, j2)] = paired_z(matrices[key], j1, j2)
            pair = paired[(key, j1, j2)]
            if pair is not None:
                z1p, z2p, med1, med2 = float(pair[0][row]), float(pair[1][row]), pair[2], pair[3]
                sudden_drop = bool(z1p - z2p >= DROP_Z and z2p <= 0.0 and n1 - n2 >= MIN_OWN_FALL_NDVI
                                   and med1 - med2 < COHORT_STEADY_FALL)
        onset_j, peak = onsets[fid]
        med_onset = cohort_onset[key]
        early, days_early = None, None
        if peak is not None and peak >= peak_min and med_onset is not None:
            if onset_j is None:
                early = False                       # still holding its green: not early
            else:
                # whole days: a half-day median (even cohort) rounds towards "not early"
                days_early = int(math.floor(med_onset - dates[onset_j].toordinal()))
                early = bool(days_early > EARLY_DAYS)
        outlier_high = bool(z2 >= HIGH_Z)

        flags = {"chronic_low": persistent_low, "sudden_drop": sudden_drop, "early_senescence": early, "outlier_high": outlier_high}
        kind = next((t for t in TYPES if flags[t]), NONE)
        onset_iso = _iso(dates[onset_j]) if onset_j is not None else None
        cohort_onset_iso = _iso(date.fromordinal(int(round(med_onset)))) if med_onset is not None else None
        rank, n_rank = ranks.get(fid, (None, 0))
        where = _where(key, meta["crop"], meta["zone"])
        fields_out[fid] = {
            "cohort": key, "n_passes": len(idx), "n_compared": len(zs),
            "harvested": harvested, "harvest_date": _iso(h) if harvested else None,
            "latest_pass": _iso(d2), "latest_ndvi": _r(n2, 3), "latest_z": _r(z2),
            "prev_pass": _iso(d1), "prev_z": _r(z1), "ndvi_change": None if n1 is None else _r(n2 - n1, 3),
            "persistent_low": persistent_low, "sudden_drop": sudden_drop, "early_senescence": early, "outlier_high": outlier_high,
            "onset": onset_iso, "cohort_onset": cohort_onset_iso,
            "rank_in_zone": rank, "zone_crop_n": n_rank,
            "anomaly_type": kind, "neighbors_flagged": 0, "event_scope": None,
            "note": _note(kind, where, _iso(d1), z1 if z1 is not None else 0.0, _iso(d2), z2,
                          n1 if n1 is not None else n2, n2, onset_iso, cohort_onset_iso, days_early,
                          _iso(h) if harvested else None),
        }

    # second pass: who else is flagged around each field, and whether the cohort as a whole moved. The cohort share
    # is over the members the median and MAD came from, so a crop-wide fallback counts every field of the crop
    flagged_in_zone = defaultdict(int)
    for fid, rec in fields_out.items():
        if rec is not None and rec["anomaly_type"] != NONE:
            flagged_in_zone[table[fid]["zone"]] += 1
    cohorts = {}
    for key, fids in members.items():
        rows = [fields_out[f] for f in fids if fields_out.get(f) is not None]
        n, k = len(rows), sum(1 for r in rows if r["anomaly_type"] != NONE)
        share = (k / n) if n else 0.0
        cohorts[key] = {"n": n, "flagged": k, "share": round(share, 3),
                        "scope": "zone_wide" if n and share >= ZONE_WIDE_SHARE else "isolated",
                        "fallback": key.endswith("/all")}
    for fid, rec in fields_out.items():
        if rec is None:
            continue
        flagged = rec["anomaly_type"] != NONE
        rec["neighbors_flagged"] = flagged_in_zone[table[fid]["zone"]] - (1 if flagged else 0)
        rec["event_scope"] = cohorts[rec["cohort"]]["scope"] if flagged else None

    # notes for the fields without a verdict, one line per reason
    reasons = {"no_series": "no satellite series in timeseries.json",
               "few_passes": "fewer than 2 clear passes (before harvest)",
               "no_peers": f"too few comparable fields of the same crop (under {MIN_PASS_VALUES} on every pass)"}
    for k, fids in skipped.items():
        if fids:
            notes.append(f"no comparison for {len(fids)} field(s), {reasons[k]}: {', '.join(fids)}")
    if fallback:
        notes.append(f"{len(fallback)} crop/zone cohort(s) under {MIN_COHORT_FIELDS} fields compared landscape-wide: {', '.join(sorted(fallback))}")

    # landscape and zones
    verdicts = {fid: rec for fid, rec in fields_out.items() if rec is not None}
    counts = {NONE: 0, **{t: 0 for t in TYPES}}
    by_zone = {z: {NONE: 0, **{t: 0 for t in TYPES}} for z in zone_ids}
    zone_compared = defaultdict(int)
    for fid, rec in verdicts.items():
        counts[rec["anomaly_type"]] += 1
        zone = table[fid]["zone"]
        if zone in by_zone:
            by_zone[zone][rec["anomaly_type"]] += 1
            zone_compared[zone] += 1
    counts["no_verdict"] = len(table) - len(verdicts)
    # flagged before clear, standing before harvested, then the size of the z: what the officer can still act on first
    ranked = sorted(verdicts.items(), reverse=True,
                    key=lambda kv: (kv[1]["anomaly_type"] != NONE, not kv[1]["harvested"], abs(kv[1]["latest_z"] or 0.0)))
    top = [{"field_id": fid, "z": rec["latest_z"], "type": rec["anomaly_type"], "harvested": rec["harvested"]}
           for fid, rec in ranked[:TOP_N]]
    landscape = {"n_fields": len(table), "n_compared": len(verdicts),
                 "n_flagged": sum(counts[t] for t in TYPES),
                 "n_flagged_standing": sum(1 for rec in verdicts.values() if rec["anomaly_type"] != NONE and not rec["harvested"]),
                 "counts": counts, "by_zone": by_zone, "top": top, "cohorts": cohorts, "notes": notes}
    zones = {z: _zone_row(flagged_in_zone[z], zone_compared[z], by_zone[z]) for z in zone_ids}
    return fields_out, landscape, zones


def _zone_row(flagged, compared, by_type):
    return {"flagged": flagged, "compared": compared,
            "share": round(flagged / compared, 3) if compared else 0.0, "by_type": dict(by_type)}


def analyze(data, config, prior=None):
    """Module contract: see the module docstring. prior is accepted for the shared interface and not needed."""
    notes = []
    data = data if isinstance(data, dict) else {}
    config = config if isinstance(config, dict) else {}
    try:
        fields_out, landscape, zones = _run(data, config, notes)
    except Exception:  # pragma: no cover - the last line of defence: a broken input never raises out of here
        notes.append("anomaly stopped early on input it could not read; no field was compared")
        try:
            table = field_table(data.get("fields"), data.get("status"), data.get("timeseries"), [])
        except Exception:
            table = {}
        fields_out = {fid: None for fid in table}
        landscape = _empty_landscape(len(table), notes)
        zones = {}
    return {"key": KEY, "fields": fields_out, "landscape": landscape, "zones": zones,
            "assumptions": dict(ASSUMPTIONS), "method": METHOD}
