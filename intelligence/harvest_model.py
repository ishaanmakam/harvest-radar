"""Per-field harvest stage + harvest-date prediction from satellite time series.

Inputs:  fields.geojson, timeseries.json, fires.json (optional)
Output:  status.json  (contract: contracts/status.schema.json)

Logic (keep it explainable for judges):
  1. Peak greenness (NDVI) = heading/flowering. Dated at the time-centre of the NDVI plateau (steadier than
     argmax, which wanders with noise on a flat top).
  2. Harvest = first clear pass after the peak where NDVI is at or below harvest_ndvi_max (and dropped, or is
     under half the peak) AND the next passes stay at that level. For crops peaking above ~0.7 the drop clause
     never fires, so harvest_ndvi_max must sit between residue and dry standing crop (0.25 for corn/soybeans).
  3. Regional lag = median(harvest - peak) over fields ALREADY harvested this season.
     Unharvested fields: predicted harvest = their own peak + regional lag, +/- half the 10th-90th pct range
     (an ~80% window). Stage = ready / maturing / growing by how far out the window centre is.
     (Neighbouring fields teach the model this season's timing. No crop calendar needed.)
  4. Burn = NBR goes strongly negative after harvest (scar) and/or a FIRMS hotspot in the field.
  5. Sown = NDVI climbing again after harvest (wheat emerging).
"""
import argparse
import statistics
from pathlib import Path

from common import add_days, d, load_region, read_json, s, write_json


def median_smooth(vals):
    out = []
    for i in range(len(vals)):
        w = vals[max(0, i - 1): i + 2]
        out.append(statistics.median(w))
    return out


def plateau_centre(xs, sm, pk, drop):
    """Time-midpoint of the run of smoothed values within `drop` of the maximum around index pk.
    xs are the observation dates (or positions) aligned with sm. argmax on a flat NDVI plateau jumps around
    with noise, and index-midpoints skew towards densely sampled stretches, so take the centre in time."""
    lo = hi = pk
    while lo > 0 and sm[lo - 1] >= sm[pk] - drop:
        lo -= 1
    while hi < len(sm) - 1 and sm[hi + 1] >= sm[pk] - drop:
        hi += 1
    return xs[lo] + (xs[hi] - xs[lo]) / 2


def lag_window(lags, default_lag, default_half=7):
    """Regional peak->harvest lag (median of fields already harvested) and a half-width that covers ~80% of
    them (10th-90th percentile). Needs at least 10 harvested fields, otherwise the configured default."""
    if len(lags) < 10:
        return default_lag, default_half
    q = statistics.quantiles(lags, n=10)
    return statistics.median(lags), max(3, (q[8] - q[0]) / 2)


def censored_median(events, censored):
    """Median lag when some fields are still standing: a standing field only says its lag is at least
    (as_of - peak). Mid-season the harvested fields are the early ones, so their median alone runs short.
    Censored lower bounds are imputed at max(bound, median of the events) and pooled; the result is still a
    lower-ish estimate of the true median (right-censoring cannot be fully undone), but no longer the early-harvester
    median. None when there are no events."""
    if not events:
        return None
    base = statistics.median(events)
    imputed = [max(c, base) for c in censored]
    return statistics.median(list(events) + imputed)


def lag_windows(lags_by_crop, censored_by_crop, default_lag, default_half=7, min_n=10):
    """{crop: (lag, half_width)} plus '*' for the pooled estimate. A crop with fewer than min_n harvested fields
    uses the pooled values; the pooled estimate itself falls back to the configured default."""
    def window(events, censored):
        if len(events) < min_n:
            return None
        q = statistics.quantiles(events, n=10)
        return censored_median(events, censored), max(3, (q[8] - q[0]) / 2)

    all_events = [v for vs in lags_by_crop.values() for v in vs]
    all_cens = [v for vs in censored_by_crop.values() for v in vs]
    pooled = window(all_events, all_cens) or (default_lag, default_half)
    out = {"*": pooled}
    for crop, events in lags_by_crop.items():
        out[crop] = window(events, censored_by_crop.get(crop, [])) or pooled
    return out


def predicted_stage(start, end, as_of):
    """Stage for a field with a predicted harvest window, judged from the window centre (the best point estimate):
    ready = expected within 3 days, maturing = within 14, else growing. Returns (stage, days_out)."""
    centre = start + (end - start) / 2
    days_out = max(0, (centre - as_of).days)
    return ("ready" if days_out <= 3 else ("maturing" if days_out <= 14 else "growing")), days_out


BURN_NBR_SEVERE = -0.30        # a second darkening after harvest to at least this NBR reads as a burn without a hotspot
BURN_POST_DROP_MIN = 0.15      # ... when it is at least this much below the first post-harvest pass
SYSTEM_DEFAULTS = {            # per crop_system in config/region.json; region thresholds override these
    "corn_soy": {"burn_requires_hotspot": True},     # residue burning is rare in Iowa; dark soil after the combine is not a scar
    "rice_wheat": {},                                 # Punjab: any post-harvest scar is burn evidence (the original rule)
}


def thresholds_for(region):
    """The thresholds the season runs with: the crop system's defaults under the region's own values."""
    return {**SYSTEM_DEFAULTS.get(region.get("crop_system") or "", {}), **(region.get("thresholds") or {})}


def analyze_field(fid, zone_id, dates, ndvi, nbr, fires_by_field, th):
    obs = [(d(dt), v, b) for dt, v, b in zip(dates, ndvi, nbr) if v is not None]
    rec = {"field_id": fid, "zone_id": zone_id, "stage": "unknown", "peak_date": None, "peak_ndvi": None,
           "harvest_date": None, "predicted_harvest_start": None, "predicted_harvest_end": None,
           "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.3, "notes": ""}
    if len(obs) < 4:
        rec["notes"] = "too few clear observations"
        return rec, None

    sm = median_smooth([v for _, v, _ in obs])
    pk = max(range(len(sm)), key=lambda i: sm[i])
    peak_date = plateau_centre([o[0] for o in obs], sm, pk, th.get("peak_plateau_drop", 0.04))
    peak_ndvi = sm[pk]   # the smoothed value the crop-level gate below uses (a raw spike would overstate it)
    rec["peak_date"], rec["peak_ndvi"] = s(peak_date), round(peak_ndvi, 3)
    if sm[pk] < th["peak_ndvi_min"]:
        rec["notes"] = "never reached crop-level greenness (not a crop field?)"
        return rec, None

    # ---- harvest detection ----
    harvest_idx = None
    for j in range(pk + 1, len(obs)):
        v, prev = obs[j][1], obs[j - 1][1]
        if v <= th["harvest_ndvi_max"] and (prev - v >= th["harvest_drop_min"] or v <= 0.5 * peak_ndvi):
            later = [o[1] for o in obs[j + 1: j + 3]]
            # the collapse must persist at the harvested level: a noise dip on dry standing crop (corn and soybeans
            # senesce to 0.25-0.40 before the combine) rebounds above the threshold, a harvest does not
            if all(x <= th["harvest_ndvi_max"] for x in later):
                harvest_idx = j
                break
    if harvest_idx is None:
        if pk == len(obs) - 1:
            rec.update(stage="growing", notes="still greening up", confidence=0.4)
        return rec, None

    before, after = obs[harvest_idx - 1][0], obs[harvest_idx][0]
    harvest = add_days(before, (after - before).days / 2)
    confirmed = harvest_idx < len(obs) - 1
    rec.update(stage="harvested", harvest_date=s(harvest), confidence=0.9 if confirmed else 0.7,
               notes=f"harvested between {s(before)} and {s(after)}")

    # ---- burn evidence ----
    post = [o for o in obs[harvest_idx:] if o[2] is not None]
    scar = next((o for o in post if o[2] <= th["burn_nbr_max"]), None)
    hot = [f for f in fires_by_field.get(fid, []) if d(f["date"]) >= add_days(harvest, -3)]
    if scar and not hot and th.get("burn_requires_hotspot"):
        # corn-and-soy country: freshly harvested ground reads dark (wet soil, residue, a tillage pass) for a week or
        # two and then brightens, so a scar alone is not a burn. A burn darkens the field AGAIN after harvest, to a
        # severe value; anything else is read as residue or tillage and the plan stays quiet.
        first_nbr = post[0][2]
        severe = next((o for o in post[1:] if o[2] <= th.get("burn_nbr_severe", BURN_NBR_SEVERE)
                       and first_nbr - o[2] >= th.get("burn_post_drop_min", BURN_POST_DROP_MIN)), None)
        if severe:
            scar = severe
        else:
            rec["notes"] += (f"; dark residue after harvest (NBR {min(o[2] for o in post):.2f}), no hotspot: read as "
                             f"residue or tillage, not a burn")
            scar = None
    if scar or hot:
        rec["stage"] = "burned"
        rec["burn_evidence"] = "scar+hotspot" if (scar and hot) else ("scar" if scar else "hotspot")
        candidates = ([scar[0]] if scar else []) + [d(f["date"]) for f in hot]
        rec["burn_date"] = s(min(candidates))
    elif th.get("sown_ndvi_min") is not None:   # regions with a following crop inside the window (rice -> wheat)
        regrow = [o for o in obs[harvest_idx + 1:] if o[1] >= th["sown_ndvi_min"] and (o[0] - harvest).days >= 10]
        if regrow:
            rec["stage"] = "sown"
            rec["notes"] += f"; regreening after harvest (next crop, cover crop or volunteer) by {s(regrow[0][0])}"
    return rec, (harvest - peak_date).days


def run(data_dir: Path):
    region = load_region()
    th = thresholds_for(region)
    as_of = d(region["as_of"])
    window = region["sowing_window_days"]
    fields = read_json(data_dir, "fields.geojson")
    ts = read_json(data_dir, "timeseries.json")
    fires = read_json(data_dir, "fires.json", default={"detections": []})
    fires_by_field = {}
    for f in fires["detections"]:
        if f.get("field_id"):
            fires_by_field.setdefault(f["field_id"], []).append(f)

    recs, lags, censored, crop_of = [], {}, {}, {}
    for feat in fields["features"]:
        p = feat["properties"]
        series = ts["fields"].get(p["field_id"])
        if not series:
            continue
        crop = p.get("crop") or "unknown"
        rec, lag = analyze_field(p["field_id"], p["zone_id"], ts["dates"], series["ndvi"], series["nbr"], fires_by_field, th)
        recs.append(rec)
        crop_of[rec["field_id"]] = crop
        if lag is not None:
            lags.setdefault(crop, []).append(lag)
        elif rec["peak_date"] and not rec["harvest_date"] and rec["peak_ndvi"] and rec["peak_ndvi"] >= th["peak_ndvi_min"]:
            censored.setdefault(crop, []).append((as_of - d(rec["peak_date"])).days)   # still standing: lag >= this

    windows = lag_windows(lags, censored, th["default_peak_to_harvest_days"])
    lag, half_spread = windows["*"]
    n_harvested = sum(len(v) for v in lags.values())
    by_crop = ", ".join(f"{c} {w[0]:.0f} d" for c, w in windows.items() if c != "*" and len(lags.get(c, [])) >= 10)
    print(f"  regional peak->harvest lag: {lag:.1f} d (+/-{half_spread:.1f}) from {n_harvested} harvested fields"
          + (f" | by crop: {by_crop}" if by_crop else ""))

    for rec in recs:
        if rec["harvest_date"]:
            rec["sowing_deadline"] = s(add_days(d(rec["harvest_date"]), window))
            continue
        if rec["peak_date"] and rec["stage"] in ("unknown",) and rec["peak_ndvi"] and rec["peak_ndvi"] >= th["peak_ndvi_min"]:
            lag, half_spread = windows.get(crop_of.get(rec["field_id"], "unknown"), windows["*"])
            center = add_days(d(rec["peak_date"]), lag)
            start, end = add_days(center, -half_spread), add_days(center, half_spread)
            if end < as_of:  # overdue: probably harvested under clouds, or late
                start, end = as_of, add_days(as_of, 3)
                rec["notes"] = "predicted harvest already passed; likely imminent or hidden by clouds"
            start = max(start, as_of)
            rec["predicted_harvest_start"], rec["predicted_harvest_end"] = s(start), s(end)
            rec["sowing_deadline"] = s(add_days(start + (end - start) / 2, window))
            rec["stage"], days_out = predicted_stage(start, end, as_of)
            rec["confidence"] = round(max(0.35, 0.75 - 0.02 * days_out), 2)
            rec["notes"] = rec["notes"] or f"peak {rec['peak_date']}; regional lag {lag:.0f} d"

    out = {"as_of": s(as_of), "peak_to_harvest_days": round(windows["*"][0], 1),
           "peak_to_harvest_days_by_crop": {c: round(w[0], 1) for c, w in windows.items() if c != "*"},
           "fields": recs}
    write_json(data_dir, "status.json", out)
    counts = {}
    for r in recs:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    print(f"  stages: {counts}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    run(Path(ap.parse_args().data))
