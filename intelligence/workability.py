"""Workability: the chance of getting the combine in, from the forecast's rain probabilities.

weather_model.py answers "which forecast days are workable" with one yes/no per day from the forecast totals. This
module asks the same question with the forecast's own uncertainty: each day rains with its forecast probability, and
5000 drawn fortnights are run through the same rain rule (a shower blocks the day, a soaking the next day too, a
downpour two days). The share of draws in which something happens is its probability.

  landscape  expected workable days in the next 7 and 14, the chance of a 1, 2 or 3 day window starting within 5 days,
             the chance each day is workable, the 5th/95th percentile day counts, notes
  fields     every standing field (stage ready or maturing): machine days needed at the region's combine rate, the
             chance of finishing before the next rain and within 7 days, and one short sentence for the farmer
  zones      the same per zone, averaged over its standing fields

Where the count starts (fields[fid]["counted_from"]): today for a ready field; the combine-ready date for a maturing
field that is not ready yet (a dry spell before the crop is ready is no use to it); the first day after the carry-over
when rain from before the forecast keeps the combine out today (that is wet ground, not a coming rain, and the
sentence says so). A chance is only given when the forecast is long enough to see the whole job from that start:
a short forecast gives None and the sentence says how many days it covers, never a false 0%.

Module contract: analyze(data, config, prior=None) -> {"key", "fields", "landscape", "zones", "assumptions", "method"}.
Pure and clock-free: today is config["as_of"]. Never raises on bad input: the missing piece becomes None plus a note.
One weather station serves the whole box, so every field shares the same drawn fortnights; only the job size and the
start day differ. prior is accepted for the module contract and not used yet.
"""
import math
from datetime import date, timedelta

import numpy as np

import weather_model as wm

KEY = "workability"
SEED = 7                      # fixed seed: the same forecast gives the same plan every run
DRAWS = 5000                  # Monte Carlo draws per run
HORIZON_DAYS = 14             # forecast days used (weather.json carries 14)
WINDOW_WITHIN_DAYS = 5        # landscape: "a window starting within 5 days"
WINDOW_LENGTHS = (1, 2, 3)    # landscape: window lengths reported
FINISH_WITHIN_DAYS = 7        # fields: "finish within 7 days"
SHOWER_FALLBACK_MM = 6.0      # ASSUMPTION: a day that rains although the forecast total is under the shower threshold
                              # is a shower the forecast under-called; 6 mm sits just over weather_model.SHOWER_MM,
                              # so it blocks that day only
DEFAULT_MACHINE_RATE = 40.0   # ha/day, config/region.json machine_rate_ha_per_day (an ASSUMPTION there: ~100 acres/day)
STANDING_STAGES = ("ready", "maturing")
PROB_DIGITS = 3
DAY_DIGITS = 2

METHOD = ("We take the 14-day forecast's rain chances and play the fortnight out 5000 times. Each day rains with its "
          "forecast chance; a rainy day stops the combine, a soaking stops it the next day too and a downpour for two "
          "days, the same rule the harvest window uses. Counting the draws gives the chance each day is workable, how "
          "many workable days to expect in the next week and fortnight, and the chance of a one, two or three day "
          "window soon. For every standing field we work out the machine days it needs at the region's combine rate "
          "and the share of draws in which the first dry run is long enough: that is its chance of finishing before "
          "the next rain. The run is counted from today, or from the day the crop is combine-ready, or from the day "
          "the ground has dried after rain that fell before the forecast. When the forecast is too short to see the "
          "whole job we say so instead of giving a number.")


# ----------------------------------------------------------------------------------------------------- helpers
def _num(x):
    """A finite float, or None for missing, non-numeric, NaN or inf."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _r(x, nd=PROB_DIGITS):
    """Round to a plain float, None when it is not a finite number."""
    v = _num(x)
    return None if v is None else round(v, nd)


def _day(x):
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _by_date(block, key):
    """date -> value for one array of a weather block. Missing or malformed arrays give an empty map."""
    out = {}
    if not isinstance(block, dict):
        return out
    dates, vals = block.get("dates"), block.get(key)
    if not isinstance(dates, list) or not isinstance(vals, list):
        return out
    for i, x in enumerate(dates):
        dd = _day(x)
        if dd is not None and i < len(vals):
            out[dd] = vals[i]
    return out


def _today(config, data):
    """config["as_of"], else status.json's as_of, else None."""
    for src in (config if isinstance(config, dict) else {}, (data or {}).get("status") or {}):
        dd = _day(src.get("as_of")) if isinstance(src, dict) else None
        if dd is not None:
            return dd
    return None


def _empty_landscape(notes):
    out = {"horizon_start": None, "horizon_end": None, "horizon_days": 0, "p_workable_by_day": None,
           "p_rain_by_day": None, "expected_workable_days_7": None, "expected_workable_days_14": None,
           "best_case_workable_days": None, "worst_case_workable_days": None, "rain_days_likely": None,
           "wet_carry_over_days": None, "lead_in_mm": None}
    for k in WINDOW_LENGTHS:
        out[f"p_window_{k}_days_within_{WINDOW_WITHIN_DAYS}"] = None
    out["notes"] = list(notes)
    return out


# ---------------------------------------------------------------------------------------------- forecast axis
def forecast_axis(fc, today, horizon=HORIZON_DAYS):
    """The day axis from today and, per day, the chance of rain and the amount when it rains.

    Returns (days, p_rain, mm_if_rain, notes); days is None when the forecast is missing, empty or ends before today.
    Day 0 is today even when the forecast starts later; a day the forecast does not cover is treated as dry and named
    in the notes. precip_prob is 0..100; when a day has none, rain is certain if the forecast total reaches the shower
    threshold and impossible otherwise. The amount on a rainy day is the forecast total, or SHOWER_FALLBACK_MM when
    that total is under the threshold."""
    notes = []
    if not isinstance(fc, dict):
        return None, None, None, ["no forecast in weather.json: workability not computed"]
    dates = sorted({dd for dd in (_day(x) for x in (fc.get("dates") if isinstance(fc.get("dates"), list) else [])) if dd})
    if not dates:
        return None, None, None, ["no forecast dates in weather.json: workability not computed"]
    if dates[-1] < today:
        return None, None, None, [f"forecast ends {dates[-1].isoformat()}, before {today.isoformat()}: refresh "
                                  "weather.json; workability not computed"]
    last = min(dates[-1], today + timedelta(days=horizon - 1))
    days = [today + timedelta(days=i) for i in range((last - today).days + 1)]
    mm_s, pr_s = _by_date(fc, "precip_mm"), _by_date(fc, "precip_prob")
    covered = set(dates)
    p = np.zeros(len(days))
    amt = np.zeros(len(days))
    missing, fallback = [], 0
    for i, dd in enumerate(days):
        if dd not in covered:
            missing.append(dd)
            continue                                   # p = 0: treated as dry
        mm = _num(mm_s.get(dd))
        mm = 0.0 if mm is None else max(mm, 0.0)
        prob = _num(pr_s.get(dd))
        if prob is None:
            fallback += 1
            p[i] = 1.0 if mm >= wm.SHOWER_MM else 0.0
        else:
            p[i] = min(max(prob / 100.0, 0.0), 1.0)
        amt[i] = mm if mm >= wm.SHOWER_MM else SHOWER_FALLBACK_MM
    if len(days) < horizon:
        notes.append(f"forecast covers {len(days)} of {horizon} days from {today.isoformat()}: expectations are over "
                     f"{len(days)} days")
    if missing:
        notes.append("no forecast for " + ", ".join(dd.isoformat() for dd in missing) + ": treated as dry")
    if not mm_s:
        notes.append("forecast has no precip_mm: days without a probability are treated as dry")
    if fallback:
        notes.append(f"{fallback} day(s) without precip_prob: rain taken as certain when the forecast total is "
                     f">= {wm.SHOWER_MM:g} mm, else none")
    return days, p, amt, notes


def lead_in_mm(obs, fc, today):
    """Rain on the two days before today, as (two days ago, yesterday) in mm, 0 when unknown. The observed record
    is the measurement; a forecast value fills a gap. Yesterday's soaking still blocks today, the same carry-over
    weather_model applies."""
    obs_pr, fc_pr = _by_date(obs, "precip_mm"), _by_date(fc, "precip_mm")
    out = []
    for back in (2, 1):
        dd = today - timedelta(days=back)
        v = _num(obs_pr.get(dd))
        if v is None:
            v = _num(fc_pr.get(dd))
        out.append(max(v or 0.0, 0.0))
    return tuple(out)


def carry_over_days(lead):
    """How many days from today the lead-in rain alone keeps the combine out, by weather_model's rule: yesterday
    >= RAIN_BLOCK_MM or two days ago >= SOAK_MM blocks today; yesterday >= SOAK_MM blocks tomorrow too. 0, 1 or 2."""
    two, yest = lead
    if yest >= wm.SOAK_MM:
        return 2
    if yest >= wm.RAIN_BLOCK_MM or two >= wm.SOAK_MM:
        return 1
    return 0


def wet_words(lead, carry):
    """The start of the farmer's sentence when carry-over rain blocks the first day(s): names the rain that did it."""
    two, yest = lead
    span = "today and tomorrow" if carry >= 2 else "today"
    cause = f"{yest:g} mm yesterday" if yest >= wm.RAIN_BLOCK_MM else f"{two:g} mm two days ago"
    return f"Too wet {span} after {cause}"


# ------------------------------------------------------------------------------------------------- Monte Carlo
def simulate(p_rain, mm_if_rain, lead=(0.0, 0.0), draws=DRAWS, seed=SEED):
    """draws x days boolean array: True where a combine can run on that day in that draw.

    Each day rains with its probability; the amount is mm_if_rain for that day. weather_model's rule then applies:
    >= SHOWER_MM blocks the day, >= RAIN_BLOCK_MM the next day too, >= SOAK_MM the two following days. `lead` is the
    rain two days ago and yesterday, the same in every draw."""
    rng = np.random.default_rng(seed)
    p = np.asarray(p_rain, dtype=float)
    n = p.shape[0]
    rain = rng.random((draws, n)) < p[None, :]
    amount = np.where(rain, np.asarray(mm_if_rain, dtype=float)[None, :], 0.0)
    lead_cols = np.tile(np.asarray(lead, dtype=float), (draws, 1))
    a = np.concatenate([lead_cols, amount], axis=1)          # columns: today-2, today-1, day 0 .. day n-1
    blocked = (a[:, 2:] >= wm.SHOWER_MM) | (a[:, 1:-1] >= wm.RAIN_BLOCK_MM) | (a[:, :-2] >= wm.SOAK_MM)
    return ~blocked


def run_lengths(workable):
    """For every draw and day, how many consecutive workable days start there (0 when that day is blocked)."""
    draws, n = workable.shape
    run = np.zeros((draws, n), dtype=np.int32)
    nxt = np.zeros(draws, dtype=np.int32)
    for i in range(n - 1, -1, -1):
        nxt = np.where(workable[:, i], nxt + 1, 0)
        run[:, i] = nxt
    return run


def days_needed(area_ha, rate=DEFAULT_MACHINE_RATE):
    """Machine days to combine the field, in half-day steps and at least half a day: ceil(2 * area / rate) / 2."""
    area, rate = _num(area_ha), _num(rate)
    if area is None or area <= 0 or rate is None or rate <= 0:
        return None
    return max(0.5, math.ceil(2.0 * area / rate) / 2.0)


def finish_chances(run, start, need, within=FINISH_WITHIN_DAYS):
    """(p_finish_before_rain, p_finish_within) for a job of `need` machine days counted from forecast day `start`.

    before rain: the share of draws whose run of workable days starting on `start` lasts >= need.
    within:      the share of draws with a run >= need starting on one of the `within` days from `start`.
    Run lengths are cut at the end of the forecast, so a figure is only given when every start day it looks at can
    show the whole job (ceil(need) days) inside the horizon; otherwise None, never a false 0."""
    n = run.shape[1]
    whole = math.ceil(need)
    if start < 0 or start >= n:
        return None, None
    p_before = float(np.mean(run[:, start] >= need)) if start + whole <= n else None
    last_start = start + within - 1
    p_within = (float(np.mean((run[:, start:start + within] >= need).any(axis=1)))
                if last_start + whole <= n else None)
    return p_before, p_within


def words(p_before, p_within, need, horizon=HORIZON_DAYS, ready_about=None, counted_from=None, wet=None):
    """One short sentence for the farmer's plan.

    ready_about:  ISO date a maturing crop is combine-ready, when that is after today: the chances count from then.
    counted_from: ISO date the count starts, named when it is not today; None when it is past the forecast.
    wet:          the opening from wet_words() when carry-over rain blocks the first day(s) and is the binding reason.
    None chances mean the forecast is too short to see the job; the sentence then says what it covers."""
    unit = "machine-day" if need == 1 else "machine-days"
    job = f"the {need:g} {unit}"
    day_word = "day" if horizon == 1 else "days"
    if ready_about and counted_from is None:
        return f"Combine-ready about {ready_about}, after the {horizon}-day forecast ends: no figure yet."
    if ready_about:
        lead = f"Combine-ready about {ready_about}; from then "
    elif wet:
        lead = f"{wet}; " + (f"from {counted_from} " if counted_from else "")
    else:
        lead = ""
    if p_before is None:
        body = f"Forecast covers only {horizon} {day_word}, too short to judge {job}."
        return lead + (body[0].lower() + body[1:] if lead else body)
    body = f"{p_before:.0%} chance to finish {job} before the next rain"
    if p_within is None:
        return f"{lead}{body}; the forecast covers only {horizon} {day_word}."
    return f"{lead}{body}, {p_within:.0%} within {FINISH_WITHIN_DAYS} days."


# ------------------------------------------------------------------------------------------------------ analyze
def carry_note(lead, carry, today):
    """Landscape note naming the rain from before the forecast that keeps the combine out at the start."""
    two, yest = lead
    when, mm = (today - timedelta(days=1), yest) if yest >= wm.RAIN_BLOCK_MM else (today - timedelta(days=2), two)
    span = "today and tomorrow" if carry >= 2 else "today"
    return (f"{mm:g} mm on {when.isoformat()} keeps the combine out {span}: carry-over from before the forecast, "
            "not forecast rain; field chances count from the first day after it")


def _landscape(days, p_rain, workable, run, lead, carry, notes):
    n = len(days)
    counts = workable.sum(axis=1)
    p_day = workable.mean(axis=0)
    within = min(WINDOW_WITHIN_DAYS, n)
    out = {
        "horizon_start": days[0].isoformat(),
        "horizon_end": days[-1].isoformat(),
        "horizon_days": n,
        "p_workable_by_day": [_r(x) for x in p_day],
        "p_rain_by_day": [_r(x) for x in p_rain],
        "expected_workable_days_7": _r(workable[:, :7].sum(axis=1).mean(), DAY_DIGITS),
        "expected_workable_days_14": _r(counts.mean(), DAY_DIGITS),
        "best_case_workable_days": int(np.quantile(counts, 0.95, method="nearest")),
        "worst_case_workable_days": int(np.quantile(counts, 0.05, method="nearest")),
        "rain_days_likely": [dd.isoformat() for dd, pr in zip(days, p_rain) if pr >= 0.5],
        "wet_carry_over_days": int(carry),
        "lead_in_mm": {(days[0] - timedelta(days=2)).isoformat(): _r(lead[0], 1),
                       (days[0] - timedelta(days=1)).isoformat(): _r(lead[1], 1)},
    }
    for k in WINDOW_LENGTHS:
        out[f"p_window_{k}_days_within_{WINDOW_WITHIN_DAYS}"] = _r((run[:, :within] >= k).any(axis=1).mean())
    out["notes"] = list(notes)
    return out


def _standing(data, notes):
    """(field_id, zone_id, stage, combine_ready_about) for every status row in a standing stage."""
    st = (data or {}).get("status")
    rows = st.get("fields") if isinstance(st, dict) else None
    if not isinstance(rows, list):
        notes.append("no status.json: no per-field workability")
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("stage") not in STANDING_STAGES or not row.get("field_id"):
            continue
        ready = row.get("predicted_harvest_start")
        out.append((str(row["field_id"]), row.get("zone_id"), row["stage"], str(ready)[:10] if ready else None))
    return out


def _field_props(data):
    """field_id -> properties from fields.geojson (empty when the file is missing or odd)."""
    fc = (data or {}).get("fields")
    feats = fc.get("features") if isinstance(fc, dict) else None
    out = {}
    for f in feats if isinstance(feats, list) else []:
        props = f.get("properties") if isinstance(f, dict) else None
        if isinstance(props, dict) and props.get("field_id"):
            out[str(props["field_id"])] = props
    return out


def _fields(data, config, run, days, lead, carry, notes):
    """One entry per standing field. The count starts on the later of: the first day after the carry-over rain, and
    (maturing fields) the combine-ready date. forecast_covers_days and counted_from let a reader see what the
    chances rest on; a None chance means the forecast is too short to see the job from that start."""
    rate = _num((config or {}).get("machine_rate_ha_per_day")) or DEFAULT_MACHINE_RATE
    props = _field_props(data)
    n = run.shape[1]
    today = days[0]
    wet = wet_words(lead, carry) if carry else None
    out, no_area = {}, []
    for fid, zone, stage, ready_about in _standing(data, notes):
        pr = props.get(fid) or {}
        need = days_needed(pr.get("area_ha"), rate)
        if need is None:
            out[fid] = None
            no_area.append(fid)
            continue
        ready_day = _day(ready_about) if stage == "maturing" and ready_about else None
        ready_k = max(0, (ready_day - today).days) if ready_day else 0     # a ready field is ready today
        start = max(carry, ready_k)
        counted_from = (today + timedelta(days=start)).isoformat() if start < n else None
        p_before, p_within = finish_chances(run, start, need)
        out[fid] = {
            "stage": stage,
            "crop": pr.get("crop"),
            "zone_id": zone if zone is not None else pr.get("zone_id"),
            "area_ha": _r(pr.get("area_ha"), 2),
            "days_needed": need,
            "combine_ready_about": ready_about,
            "counted_from": counted_from,
            "forecast_covers_days": n,
            "p_finish_before_rain": _r(p_before),
            "p_finish_within_7d": _r(p_within),
            "words": words(p_before, p_within, need, n,
                           ready_about=ready_day.isoformat() if ready_k > carry else None,
                           counted_from=counted_from, wet=wet),
        }
    if no_area:
        shown = ", ".join(no_area[:5]) + (f" and {len(no_area) - 5} more" if len(no_area) > 5 else "")
        notes.append(f"{len(no_area)} standing field(s) with no area in fields.geojson, skipped: {shown}")
    return out


def _zones(data, fields):
    ids = []
    zf = (data or {}).get("zones")
    feats = zf.get("features") if isinstance(zf, dict) else None
    for f in feats if isinstance(feats, list) else []:
        props = f.get("properties") if isinstance(f, dict) else None
        if isinstance(props, dict) and props.get("zone_id") is not None:
            ids.append(str(props["zone_id"]))
    for v in fields.values():
        if v and v.get("zone_id") is not None and str(v["zone_id"]) not in ids:
            ids.append(str(v["zone_id"]))
    out = {}
    for zid in ids:
        rows = [v for v in fields.values() if v and str(v.get("zone_id")) == zid]
        # means over the fields that have a figure; a field the forecast cannot judge is left out, not counted as 0
        before = [v["p_finish_before_rain"] for v in rows if v["p_finish_before_rain"] is not None]
        within = [v["p_finish_within_7d"] for v in rows if v["p_finish_within_7d"] is not None]
        out[zid] = {
            "standing_fields": len(rows),
            "machine_days_needed": _r(sum(v["days_needed"] for v in rows), 1) if rows else 0.0,
            "mean_p_finish_before_rain": _r(float(np.mean(before))) if before else None,
            "mean_p_finish_within_7d": _r(float(np.mean(within))) if within else None,
            "share_likely_before_rain": _r(sum(1 for p in before if p >= 0.5) / len(before)) if before else None,
        }
    return out


def _assumptions(config):
    rate = _num((config or {}).get("machine_rate_ha_per_day")) or DEFAULT_MACHINE_RATE
    return {
        "draws": f"{DRAWS} Monte Carlo draws, numpy default_rng({SEED}); the fixed seed makes the morning plan repeatable",
        "rain_probability": "precip_prob / 100 per forecast day (Open-Meteo); without it, rain is certain when the "
                            f"forecast total is >= {wm.SHOWER_MM:g} mm and absent otherwise",
        "shower_fallback_mm": f"{SHOWER_FALLBACK_MM:g} mm when a day rains but the forecast total is under "
                              f"{wm.SHOWER_MM:g} mm: ASSUMPTION, the forecast under-called a shower",
        "rain_rule": f"weather_model: >= {wm.SHOWER_MM:g} mm blocks the day, >= {wm.RAIN_BLOCK_MM:g} mm the next day "
                     f"too, >= {wm.SOAK_MM:g} mm two days; observed rain on the two days before today carries in",
        "machine_rate_ha_per_day": f"{rate:g} ha/day for one combine, config/region.json (ASSUMPTION there)",
        "days_needed": "ceil(2 * area_ha / rate) / 2: half-day steps, at least 0.5",
        "standing_fields": "status.json stage ready or maturing; harvested, growing and unknown fields get no entry",
        "counted_from": "field chances count from today, or from status.json predicted_harvest_start for a maturing "
                        "field not ready yet, or from the first day after carry-over rain from before the forecast, "
                        "whichever is later",
        "short_forecast": f"a chance is given only when the forecast shows the whole job from that start (within "
                          f"{FINISH_WITHIN_DAYS} days: from every start day); otherwise None and the sentence says "
                          "how many days the forecast covers",
        "one_station": "one forecast for the whole box, so every field shares the same drawn fortnights",
        "best_worst_case": "95th and 5th percentile of workable days over the horizon across the draws",
    }


def analyze(data: dict, config: dict, prior: dict | None = None) -> dict:
    """The module contract: see the module docstring. Never raises on bad or missing input."""
    data = data if isinstance(data, dict) else {}
    config = config if isinstance(config, dict) else {}
    notes = []
    weather = data.get("weather") if isinstance(data.get("weather"), dict) else {}
    fc, obs = weather.get("forecast"), weather.get("observed")
    today = _today(config, data)
    if today is None:
        first = _day((fc.get("dates") or [None])[0]) if isinstance(fc, dict) and isinstance(fc.get("dates"), list) else None
        if first is None:
            return {"key": KEY, "fields": {}, "zones": {},
                    "landscape": _empty_landscape(["no as_of in config or status.json and no forecast: workability not computed"]),
                    "assumptions": _assumptions(config), "method": METHOD}
        today = first
        notes.append(f"no as_of in config or status.json: today taken as the first forecast day {today.isoformat()}")
    try:
        days, p_rain, mm_if_rain, fc_notes = forecast_axis(fc, today)
        notes.extend(fc_notes)
        if days is None:
            return {"key": KEY, "fields": {}, "zones": {}, "landscape": _empty_landscape(notes),
                    "assumptions": _assumptions(config), "method": METHOD}
        lead = lead_in_mm(obs, fc, today)
        carry = carry_over_days(lead)
        if carry:
            notes.append(carry_note(lead, carry, today))
        workable = simulate(p_rain, mm_if_rain, lead)
        run = run_lengths(workable)
        fields = _fields(data, config, run, days, lead, carry, notes)
        zones = _zones(data, fields)
        landscape = _landscape(days, p_rain, workable, run, lead, carry, notes)
    except Exception as e:   # the contract: never raise, say what went wrong
        notes.append(f"workability failed on this input: {type(e).__name__}: {e}")
        return {"key": KEY, "fields": {}, "zones": {}, "landscape": _empty_landscape(notes),
                "assumptions": _assumptions(config), "method": METHOD}
    return {"key": KEY, "fields": fields, "landscape": landscape, "zones": zones,
            "assumptions": _assumptions(config), "method": METHOD}
