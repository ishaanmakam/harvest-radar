"""Harvest now or wait, in dollars, for every standing corn and soybean field. Module key "economics".

For each standing field (status stage growing, maturing or ready) the module prices the two choices a farmer has
this week:
  harvest now    pay to dry the grain from today's moisture down to the storage standard, and give up the water
                 weight (shrink) the elevator takes off the wet bushels
  wait 7 days    drying and shrink from the moisture a week from now, plus what the crop loses standing in the
                 field (a share of the yield per week once it is combine-ready, more after a hard freeze or a
                 soaking rain on corn, or a wet-dry spell on beans), plus a weather-risk premium: the chance that no
                 workable combine window opens in the wait week times one more week of field loss
The wait week is tomorrow .. today + 7: the farmer who waits is not combining today, so a dry morning proves
nothing about the week ahead; today's forecast counts only through a heavy rain carrying into tomorrow.
Grain moisture follows the same dry-down rule as weather_model.py (corn 32% at physiological maturity, drying
max(0.10, 0.03 x Tmean) points a day; soybeans down to 13% a week after maturity), where maturity is the satellite's
harvest estimate less its dry-down fortnight. For a field status calls ready that estimate is the peak date plus the
crop's peak-to-harvest lag (the window centre harvest_model computed before it clamped the start to as_of), so a
field that has stood three weeks reads drier than one that turned ready this morning, and the satellite's word that
the crop is ready wins over the curve: a ready field is combine-ready today. Bushels come from the yield module when
it ran, else a reference yield times the field area. Every price and loss rate is an ASSUMPTION listed below so it
can be tuned; nothing here is a measurement, and every number derives from status.json, fields.geojson and
weather.json.

    analyze(data, config, prior) -> {"key": "economics", "fields": {field_id: {...} | None}, "landscape": {...},
                                     "zones": {zone_id: {...}}, "assumptions": {...}, "method": "..."}
"""
import math
from collections import Counter
from datetime import date, timedelta

import numpy as np

import weather_model as wm

KEY = "economics"
STANDING = ("growing", "maturing", "ready")
CROPS = ("corn", "soybean")

# prices and yields
PRICE_USD_PER_BU = {"corn": 4.20, "soybean": 10.40}        # ASSUMPTION: placeholder cash prices, Oct 2026
REFERENCE_YIELD_BU_PER_AC = {"corn": 200.0, "soybean": 60.0}   # ASSUMPTION: central Iowa trend yields (USDA NASS 2024 Iowa: corn 211, soybeans 60 bu/ac)
AC_PER_HA = 2.4711

# drying, shrink and moisture
DRYING_USD_PER_POINT_BU = 0.045           # ASSUMPTION: Iowa custom drying charge per point of moisture per bushel
STORAGE_MOISTURE_PCT = {"corn": 15.0, "soybean": 13.0}   # market standard; shrink = points over standard / (100 - standard)
CORN_FIELD_MOISTURE_FLOOR_PCT = STORAGE_MOISTURE_PCT["corn"]   # ASSUMPTION: standing corn does not field-dry below the storage standard
SOY_MOISTURE_AT_MATURITY_PCT = 60.0       # ASSUMPTION: soybean seed moisture at R7 (ISU extension), then a straight line down
SOY_DAYS_TO_DRY = 7                       # beans read 13% from 7 days after maturity (estimate - 14); rewetting ignored
DEFAULT_TMEAN_C = 15.0                    # ASSUMPTION: mean temperature when the weather file has none (weather_model uses the same)

# field loss while standing (ASSUMPTION, ISU extension rules of thumb: pre-harvest losses, lodging, ear drop, shatter)
FIELD_LOSS_PCT_PER_WEEK = {"corn": 0.5, "soybean": 1.0}   # share of yield lost per week standing after combine-ready
CORN_EVENT_LOSS_PCT = 1.5                 # once, when a hard freeze (<= -2 C) or a >= 25 mm day hits ready corn in the wait
SOY_WET_DRY_CYCLE_LOSS_PCT = 2.0          # per wet-dry cycle (a >= 5 mm day followed by a dry day) on ready beans
DRY_DAY_MAX_MM = 1.0                      # ASSUMPTION: under this much rain a day counts as the dry half of a wet-dry cycle

# the decision
WAIT_DAYS = 7
EITHER_BAND_FRAC = 0.03                   # "either" when the two choices are within 3% of the crop value
DEFAULT_MACHINE_RATE_HA_PER_DAY = 40.0    # config/region.json machine_rate_ha_per_day; used only when it is missing
RAIN_DAY_DEFAULT_MM = wm.SHOWER_MM        # ASSUMPTION: a forecast rain day with no amount brings at least a shower

CROP_WORD = {"corn": "Corn", "soybean": "Beans"}

ASSUMPTIONS = {
    "prices": f"corn {PRICE_USD_PER_BU['corn']:.2f} USD/bu, soybeans {PRICE_USD_PER_BU['soybean']:.2f} USD/bu (placeholders, Oct 2026)",
    "reference_yield": f"corn {REFERENCE_YIELD_BU_PER_AC['corn']:.0f}, soybeans {REFERENCE_YIELD_BU_PER_AC['soybean']:.0f} bu/ac "
                       f"when the yield module has no estimate (central Iowa trend, USDA NASS)",
    "drying_charge": f"{DRYING_USD_PER_POINT_BU} USD per point per bushel above the storage standard (Iowa custom drying)",
    "storage_standard": f"corn {STORAGE_MOISTURE_PCT['corn']:.0f}%, soybeans {STORAGE_MOISTURE_PCT['soybean']:.0f}%; "
                        f"shrink = points over standard / (100 - standard) of the wet bushels",
    "corn_drydown": (f"{wm.CROPS['corn']['moisture_at_maturity']:.0f}% at maturity (estimate - {wm.DRYDOWN_BEFORE_HARVEST_DAYS} d), "
                     f"max({wm.CROPS['corn']['drydown_min']}, {wm.CROPS['corn']['drydown_per_c']} x Tmean) points a day, "
                     f"combine-ready at {wm.CROPS['corn']['ready_moisture']:.0f}%, floor {CORN_FIELD_MOISTURE_FLOOR_PCT:.0f}% (weather_model.py)"),
    "soy_drydown": f"{SOY_MOISTURE_AT_MATURITY_PCT:.0f}% at maturity (estimate - {wm.DRYDOWN_BEFORE_HARVEST_DAYS} d) straight down to "
                   f"{STORAGE_MOISTURE_PCT['soybean']:.0f}% in {SOY_DAYS_TO_DRY} days, rewetting ignored (ISU)",
    "field_loss": (f"corn {FIELD_LOSS_PCT_PER_WEEK['corn']}%/week after combine-ready, +{CORN_EVENT_LOSS_PCT}% once a hard freeze "
                   f"(<= {wm.HARD_FREEZE_C:.0f} C) or a >= {wm.SOAK_MM:.0f} mm day falls in the wait week; soybeans "
                   f"{FIELD_LOSS_PCT_PER_WEEK['soybean']}%/week + {SOY_WET_DRY_CYCLE_LOSS_PCT}% per wet-dry cycle "
                   f"(>= {wm.SHOWER_MM:.0f} mm day then a < {DRY_DAY_MAX_MM:.0f} mm day) (ISU extension)"),
    "wait_week": (f"tomorrow to today + {WAIT_DAYS}: the days a waiting farmer would combine; today is the harvest-now day "
                  f"and is never a wait day (its heavy rain still carries into tomorrow)"),
    "weather_risk": (f"forecast precip_prob over the wait week as independent days; a rain day of >= {wm.SHOWER_MM:.0f} mm blocks "
                     f"the combine that day, >= {wm.RAIN_BLOCK_MM:.0f} mm the next too; premium = P(no window of days_needed "
                     f"in the {WAIT_DAYS} wait days) x one more week of field loss; the workability module's "
                     f"p_finish_within_7d wins when present"),
    "ready_stage": (f"a field status.json calls ready is combine-ready today (the satellite's reading wins over the dry-down "
                    f"curve); its harvest estimate is peak_date + the crop's peak-to-harvest lag from status.json, the "
                    f"window centre before status clamped the start to as_of, so an overdue field reads drier"),
    "days_needed": f"ceil(area_ha / machine_rate_ha_per_day), rate from config/region.json (default {DEFAULT_MACHINE_RATE_HA_PER_DAY:.0f})",
    "either_band": f"harvest now and wait within {EITHER_BAND_FRAC:.0%} of the crop value read as either",
    "default_tmean": f"{DEFAULT_TMEAN_C:.0f} C for days with no temperature in weather.json",
}

METHOD = ("For every standing corn and soybean field we price this week's two choices. Harvest now: pay to dry the grain "
          "from today's moisture to the storage standard and lose the water weight the elevator shrinks off. Wait a week: "
          "the grain dries in the field at the forecast temperatures, so drying and shrink fall, but the crop loses a "
          "small share of its yield standing, more after a hard freeze, a soaking rain or wet-dry spells, and there is "
          "a chance the rain leaves no day to combine, which costs another week of field loss. The wait is judged on "
          "the seven days after today, the days a waiting farmer would actually combine. Moisture starts at "
          "maturity, two weeks before the satellite's harvest estimate, and follows the same dry-down rule as the "
          "harvest-window model; a crop the satellite calls ready is combine-ready today, and its estimate is the "
          "peak date plus the regional lag, so a field that has stood for weeks reads drier than one that just "
          "turned. Bushels come from the yield estimate or a reference yield times the field area. The cheaper "
          "choice is the recommendation; within three percent of the crop value we say either.")


# ----------------------------------------------------------------------------------------------- small helpers
def _num(x, nd=2):
    """A JSON-safe rounded float: None for None, NaN or infinity, never a numpy scalar."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, nd)


def _iso(x):
    return x.isoformat() if isinstance(x, date) else None


def _d(x):
    """A date from an ISO string or datetime string; None when absent or unreadable."""
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _prior_field(prior, key, fid):
    """The per-field dict an earlier module left for this field, whether prior[key] is that module's whole result
    ({"fields": {...}}) or is already keyed by field id. None when absent or malformed."""
    try:
        block = (prior or {}).get(key)
        if not isinstance(block, dict):
            return None
        if isinstance(block.get("fields"), dict):
            block = block["fields"]
        rec = block.get(fid)
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _yield_bushels(y):
    """Production bushels from the yield module's per-field dict: production_bu, or the nested production.bu that
    yield_model.py writes. None when absent."""
    if not isinstance(y, dict):
        return None
    bu = y.get("production_bu")
    if bu is None and isinstance(y.get("production"), dict):
        bu = y["production"].get("bu")
    return _num(bu, 3)


def _today(config, data):
    """as_of from config/region.json, else from status.json; None when neither is readable."""
    status = (data or {}).get("status")
    status_as_of = status.get("as_of") if isinstance(status, dict) else None
    for src in ((config or {}).get("as_of"), status_as_of):
        t = _d(src)
        if t:
            return t
    return None


def _features(data):
    """(field_id, properties) for every readable feature of fields.geojson."""
    out = []
    try:
        for f in ((data or {}).get("fields") or {}).get("features") or []:
            props = (f or {}).get("properties") if isinstance(f, dict) else None
            if isinstance(props, dict) and props.get("field_id"):
                out.append((str(props["field_id"]), props))
    except Exception:
        pass
    return out


def _status_by_field(data):
    out = {}
    try:
        for r in ((data or {}).get("status") or {}).get("fields") or []:
            if isinstance(r, dict) and r.get("field_id"):
                out[str(r["field_id"])] = r
    except Exception:
        pass
    return out


def _zone_ids(data):
    out = []
    try:
        for z in ((data or {}).get("zones") or {}).get("features") or []:
            zid = ((z or {}).get("properties") or {}).get("zone_id")
            if zid:
                out.append(zid)
    except Exception:
        pass
    return out


def _lag_days(data, crop):
    """The crop's peak-to-harvest lag in days from status.json (peak_to_harvest_days_by_crop, else the pooled
    peak_to_harvest_days), the same numbers harvest_model used to place the window centre on peak_date + lag.
    None when unreadable."""
    status = (data or {}).get("status")
    if not isinstance(status, dict):
        return None
    by_crop = status.get("peak_to_harvest_days_by_crop")
    lag = by_crop.get(crop) if isinstance(by_crop, dict) else None
    if lag is None:
        lag = status.get("peak_to_harvest_days")
    lag = _num(lag, 3)
    return lag if lag is not None and lag > 0 else None


def wait_days(today):
    """The wait week: tomorrow .. today + WAIT_DAYS, the days a farmer who waits would combine. Today is the
    harvest-now day and is never one of them."""
    return [today + timedelta(days=i) for i in range(1, WAIT_DAYS + 1)]


# ----------------------------------------------------------------------------------------------- phenology
def harvest_estimate(rec, phen=None, lag_days=None):
    """The satellite's harvest estimate for a standing field, and where it came from:
      ready             peak_date + the crop's peak-to-harvest lag (lag_days, from status.json): the window centre
                        harvest_model computed before it clamped the start to as_of, so an overdue field keeps its
                        real date; the clamped start when the peak or the lag is missing
      maturing/growing  the centre of the predicted window
    then the phenology module's ensemble date when status carries none. Returns (date, source) or (None, None)."""
    start, end = _d(rec.get("predicted_harvest_start")), _d(rec.get("predicted_harvest_end"))
    peak = _d(rec.get("peak_date"))
    est = None
    if rec.get("stage") == "ready" and peak and lag_days is not None:
        return peak + timedelta(days=int(round(lag_days))), "peak_lag"
    if rec.get("stage") == "ready" and start:
        est = start
    elif start and end:
        est = start + timedelta(days=(end - start).days // 2)
    elif start or end:
        est = start or end
    if est:
        return est, "status"
    ens = _d((phen or {}).get("ensemble_harvest_date"))
    if ens:
        return ens, "phenology"
    return None, None


def temperature_by_day(weather):
    """date -> mean temperature from the observed record then the forecast (weather_model's reading), plus the
    stand-in for days without one: the mean of the last seven known days, else DEFAULT_TMEAN_C."""
    obs = (weather or {}).get("observed") or {}
    fc = (weather or {}).get("forecast") or {}
    try:
        temps = wm._tmeans(obs, fc)
    except Exception:
        temps = {}
    known = sorted(temps)
    tail = [temps[k] for k in known[-7:]]
    recent = sum(tail) / len(tail) if tail else DEFAULT_TMEAN_C
    return temps, recent


def corn_moisture_curve(maturity, temps, recent, n_days):
    """Kernel moisture k days after maturity for k = 0..n_days: 32% at maturity, then each day loses
    max(drydown_min, drydown_per_c x Tmean) points (weather_model.CROPS), never below the field floor."""
    c = wm.CROPS["corn"]
    t = np.array([temps.get(maturity + timedelta(days=k), recent) for k in range(max(0, int(n_days)))], dtype=float)
    daily = np.maximum(c["drydown_min"], c["drydown_per_c"] * t)
    curve = c["moisture_at_maturity"] - np.concatenate([[0.0], np.cumsum(daily)])
    return np.maximum(curve, CORN_FIELD_MOISTURE_FLOOR_PCT)


def moisture_model(crop, maturity, today, temps, recent):
    """Today's and next week's grain moisture and the first combine-ready day for one field.
    Corn: the dry-down curve above, ready at weather_model's ready_moisture (None when not reached within
    MAX_EXTRAPOLATE_DAYS of maturity). Soybeans: SOY_MOISTURE_AT_MATURITY_PCT at maturity, a straight line to the
    storage standard SOY_DAYS_TO_DRY days later, flat from then on (rewetting ignored)."""
    later = today + timedelta(days=WAIT_DAYS)
    if crop == "corn":
        c = wm.CROPS["corn"]
        n = max((later - maturity).days, wm.MAX_EXTRAPOLATE_DAYS, 1)
        curve = corn_moisture_curve(maturity, temps, recent, n)

        def at(day):
            k = (day - maturity).days
            return float(curve[0]) if k <= 0 else float(curve[min(k, n)])

        hits = np.nonzero(curve <= c["ready_moisture"])[0]
        ready = maturity + timedelta(days=int(hits[0])) if len(hits) else None
    else:
        top, std = SOY_MOISTURE_AT_MATURITY_PCT, STORAGE_MOISTURE_PCT["soybean"]

        def at(day):
            k = (day - maturity).days
            if k <= 0:
                return top
            if k >= SOY_DAYS_TO_DRY:
                return std
            return top - (top - std) * k / SOY_DAYS_TO_DRY

        ready = maturity + timedelta(days=SOY_DAYS_TO_DRY)
    return {"today": at(today), "in_7d": at(later), "ready": ready}


# ----------------------------------------------------------------------------------------------- weather in the wait
def forecast_by_day(weather, today):
    """date -> {"precip": mm, "prob": 0..100, "tmin": C} for forecast days on or after today (older days are stale).
    Missing arrays give None entries; an unreadable forecast gives {}."""
    fc = (weather or {}).get("forecast") or {}
    out = {}
    try:
        precip, prob, tmin = wm._series(fc, "precip_mm"), wm._series(fc, "precip_prob"), wm._series(fc, "tmin_c")
        for day in wm._dates(fc):
            if day >= today:
                out[day] = {"precip": precip.get(day), "prob": prob.get(day), "tmin": tmin.get(day)}
    except Exception:
        return {}
    return out


def p_no_workable_window(day_risks, days_needed, lead=None):
    """Probability that no run of days_needed workable days appears among day_risks, given per day (p_rain, mm if
    it rains), days independent. A rain day of >= SHOWER_MM blocks the combine that day, >= RAIN_BLOCK_MM also the
    next day; a rain day with no amount is read as a shower. lead is the same pair for the day before the window
    (today): its heavy rain can carry into the first window day, but it is never a window day itself.
    Exact: every rain / no-rain pattern is enumerated (2^n, n <= 8). None when there are no days to judge."""
    n = len(day_risks)
    if n == 0:
        return None
    days_needed = max(1, int(math.ceil(days_needed)))
    if days_needed > n:
        return 1.0
    days = ([tuple(lead)] if lead is not None else []) + [tuple(r) for r in day_risks]
    m = len(days)
    p = np.array([min(1.0, max(0.0, float(r[0] or 0.0))) for r in days])
    mm = [None if r[1] is None else float(r[1]) for r in days]
    blocks = np.array([(a is None) or (a >= wm.SHOWER_MM) for a in mm])
    carries = np.array([(a is not None) and (a >= wm.RAIN_BLOCK_MM) for a in mm])
    rains = ((np.arange(2 ** m)[:, None] >> np.arange(m)) & 1).astype(bool)   # pattern x day: does it rain
    blocked = rains & blocks
    blocked[:, 1:] |= rains[:, :-1] & carries[:-1]
    workable = ~blocked[:, m - n:]                                            # the lead day is not a window day
    prob = np.prod(np.where(rains, p, 1.0 - p), axis=1)
    run = np.zeros(2 ** m)
    best = np.zeros(2 ** m)
    for j in range(n):
        run = np.where(workable[:, j], run + 1, 0.0)
        best = np.maximum(best, run)
    return float(prob[best < days_needed].sum())


def _day_risk(f):
    """(p_rain, mm) for one forecast day, for p_no_workable_window: a day with an amount but no probability is
    certain by amount; a rain day with no amount brings a shower. None when the day has neither."""
    if not f:
        return None
    mm, prob = f.get("precip"), f.get("prob")
    if prob is None and mm is None:
        return None
    if prob is None:
        p = 1.0 if mm >= wm.SHOWER_MM else 0.0
    else:
        p = float(prob) / 100.0
    return p, (mm if mm is not None else RAIN_DAY_DEFAULT_MM)


def wait_week_risks(fc_days, today):
    """(p_rain, mm) per wait day (tomorrow .. today + WAIT_DAYS) that the forecast covers; days it does not cover
    are left out. Today is not a wait day: the farmer who waits is not combining today."""
    risks = (_day_risk(fc_days.get(day)) for day in wait_days(today))
    return [r for r in risks if r is not None]


def field_loss(crop, ready, today, fc_days):
    """Yield lost by standing through the wait week (tomorrow .. today + WAIT_DAYS), in percent, with the days that
    counted. The weekly rate runs only over the wait days on or after combine-ready. Corn adds CORN_EVENT_LOSS_PCT
    once when a hard freeze or a soaking rain lands on a ready day; beans add SOY_WET_DRY_CYCLE_LOSS_PCT per wet day
    (>= SHOWER_MM) on a ready day that the next day's forecast calls dry (< DRY_DAY_MAX_MM)."""
    standing = [day for day in wait_days(today) if ready is not None and day >= ready]
    pct = FIELD_LOSS_PCT_PER_WEEK[crop] * len(standing) / WAIT_DAYS
    events = []
    for day in standing:
        f = fc_days.get(day) or {}
        mm, tmin = f.get("precip"), f.get("tmin")
        if crop == "corn":
            if (tmin is not None and tmin <= wm.HARD_FREEZE_C) or (mm is not None and mm >= wm.SOAK_MM):
                events.append(day)
        else:
            nxt = (fc_days.get(day + timedelta(days=1)) or {}).get("precip")
            if mm is not None and mm >= wm.SHOWER_MM and nxt is not None and nxt < DRY_DAY_MAX_MM:
                events.append(day)
    if crop == "corn" and events:
        pct += CORN_EVENT_LOSS_PCT
    elif crop == "soybean":
        pct += SOY_WET_DRY_CYCLE_LOSS_PCT * len(events)
    return pct, len(standing), events


# ----------------------------------------------------------------------------------------------- dollars
def drying_and_shrink(crop, moisture, bushels, price):
    """(drying USD, shrink USD) to bring wet bushels at this moisture to the crop's storage standard."""
    std = STORAGE_MOISTURE_PCT[crop]
    pts = max(0.0, float(moisture) - std)
    drying = bushels * pts * DRYING_USD_PER_POINT_BU
    shrink_bu = bushels * pts / (100.0 - std)
    return drying, shrink_bu * price


def _money(x):
    return f"${x:,.0f}"


def _reason(crop, rec, m_now, m_7d, savings, band, maturity, ready, today, standing_days, events, p_no, loss_usd=0.0):
    word = CROP_WORD[crop]
    if rec == "wait" and (maturity > today or (ready is not None and ready > today + timedelta(days=WAIT_DAYS))):
        when = ready.isoformat() if ready else "next month at the earliest"
        if maturity > today:
            return f"{word} not mature until {maturity.isoformat()} and not combine-ready before {when}: wait."
        return f"{word} at about {m_now:.0f}% moisture, not combine-ready before {when}: let it dry in the field."
    if ready is None:
        return (f"{word} at about {m_now:.0f}% moisture will not field-dry this season at these temperatures: "
                f"plan to dry it; harvesting now or later costs about the same.")
    if rec == "wait":
        tail = " after field losses" if loss_usd and loss_usd > 0 else ""
        return (f"{word} at about {m_now:.0f}% moisture today, about {m_7d:.0f}% in a week: letting it dry in the field "
                f"saves about {_money(savings)}{tail}.")
    if rec == "harvest_now":
        why = []
        if crop == "corn" and events:
            why.append("a hard freeze or soaking rain is forecast")
        if crop == "soybean" and events:
            why.append(f"{len(events)} wet-dry spell{'s' if len(events) > 1 else ''} forecast")
        if p_no is not None and p_no >= 0.5:
            why.append(f"a {p_no:.0%} chance of no dry window")
        tail = " with " + " and ".join(why) if why else ""
        return (f"{word} at about {m_now:.0f}% moisture: another week standing{tail} costs about {_money(savings)} "
                f"more than drying it now, so combine now.")
    return (f"{word} at about {m_now:.0f}% moisture: harvesting now or in a week costs about the same "
            f"(within {_money(band)}), so combine when the machine is free.")


def evaluate_field(fid, props, rec, today, temps, recent, fc_days, machine_rate, prior, has_forecast, lags=None):
    """One standing field's numbers, or (None, reason) when it cannot be priced. lags = {crop: peak-to-harvest
    days} from status.json (see _lag_days)."""
    crop = str(props.get("crop") or "").lower()
    if crop not in CROPS:
        return None, "crop other"
    stage = rec.get("stage")
    if stage == "harvested":
        return None, "harvested"
    if stage not in STANDING:
        return None, "stage unknown"
    estimate, est_source = harvest_estimate(rec, _prior_field(prior, "phenology", fid), (lags or {}).get(crop))
    if estimate is None:
        return None, "no harvest estimate"
    try:
        area = float(props.get("area_ha"))
    except (TypeError, ValueError):
        return None, "no area"
    if not math.isfinite(area) or area <= 0:
        return None, "no area"

    maturity = estimate - timedelta(days=wm.DRYDOWN_BEFORE_HARVEST_DAYS)
    moist = moisture_model(crop, maturity, today, temps, recent)
    m_now, m_7d, ready = moist["today"], moist["in_7d"], moist["ready"]
    ready_source = "drydown"
    if stage == "ready" and (ready is None or ready > today):
        # the satellite says the crop is ready: it can be combined today whatever the curve reads, so every wait
        # day stands and the weather-risk premium is live
        ready, ready_source = today, "satellite"

    bushels = _yield_bushels(_prior_field(prior, "yield", fid))
    if bushels is not None and bushels > 0:
        bushels_source = "yield_module"
    else:
        bushels, bushels_source = area * AC_PER_HA * REFERENCE_YIELD_BU_PER_AC[crop], "reference_yield"
    price = PRICE_USD_PER_BU[crop]
    value = bushels * price

    drying_now, shrink_now = drying_and_shrink(crop, m_now, bushels, price)
    drying_7d, shrink_7d = drying_and_shrink(crop, m_7d, bushels, price)
    loss_pct, standing_days, events = field_loss(crop, ready, today, fc_days)
    loss_usd = value * loss_pct / 100.0

    days_needed = max(1, int(math.ceil(area / machine_rate)))
    w = _prior_field(prior, "workability", fid)
    p_fin = _num((w or {}).get("p_finish_within_7d"), 4)
    if p_fin is not None:
        p_no, risk_source = min(1.0, max(0.0, 1.0 - p_fin)), "workability"
    elif has_forecast:
        p_no = p_no_workable_window(wait_week_risks(fc_days, today), days_needed, lead=_day_risk(fc_days.get(today)))
        risk_source = "forecast" if p_no is not None else None
    else:
        p_no, risk_source = None, None
    weekly_loss_usd = value * FIELD_LOSS_PCT_PER_WEEK[crop] / 100.0
    premium = (p_no or 0.0) * weekly_loss_usd if (p_no is not None and standing_days > 0) else 0.0

    cost_now = drying_now + shrink_now
    cost_wait = drying_7d + shrink_7d + loss_usd + premium
    band = EITHER_BAND_FRAC * value
    diff = cost_now - cost_wait                      # positive: waiting is cheaper
    not_this_week = maturity > today or (ready is not None and ready > today + timedelta(days=WAIT_DAYS))
    if not_this_week:
        recommendation = "wait"
    elif round(abs(diff), 2) <= round(band, 2):      # compared in cents: a tie on the band is "either", not a coin flip
        recommendation = "either"
    elif diff > 0:
        recommendation = "wait"
    else:
        recommendation = "harvest_now"
    savings = abs(diff)

    out = {
        "crop": crop, "stage": stage, "zone_id": rec.get("zone_id") or props.get("zone_id"),
        "area_ha": _num(area, 2), "bushels": _num(bushels, 1), "bushels_source": bushels_source,
        "price_usd_per_bu": price, "crop_value_usd": _num(value),
        "harvest_estimate": _iso(estimate), "estimate_source": est_source,
        "maturity": _iso(maturity), "combine_ready": _iso(ready), "ready_source": ready_source,
        "moisture_today_pct": _num(m_now, 1), "moisture_in_7d_pct": _num(m_7d, 1),
        "days_needed": days_needed,
        "drying_usd_now": _num(drying_now), "shrink_usd_now": _num(shrink_now),
        "drying_usd_7d": _num(drying_7d), "shrink_usd_7d": _num(shrink_7d),
        "field_loss_pct_7d": _num(loss_pct, 3), "field_loss_usd_7d": _num(loss_usd),
        "loss_events": [_iso(e) for e in events],
        "p_no_window_7d": _num(p_no, 4), "risk_source": risk_source, "risk_premium_usd": _num(premium),
        "cost_now_usd": _num(cost_now), "cost_wait_7d_usd": _num(cost_wait),
        "recommendation": recommendation, "savings_usd": _num(savings),
        "reason": _reason(crop, recommendation, m_now, m_7d, savings, band, maturity, ready, today, standing_days,
                          events, p_no, loss_usd),
    }
    return out, None


# ----------------------------------------------------------------------------------------------- entry point
def _empty(notes):
    return {"key": KEY, "fields": {}, "landscape": {"fields_evaluated": 0, "counts": {"harvest_now": 0, "wait": 0, "either": 0},
                                                    "notes": list(notes)},
            "zones": {}, "assumptions": dict(ASSUMPTIONS), "method": METHOD}


def analyze(data, config, prior=None):
    """Price harvest-now against wait-a-week for every standing corn and soybean field. Never raises: a field that
    cannot be priced is None and the reason is counted in landscape["notes"]."""
    notes = []
    data = data if isinstance(data, dict) else {}
    config = config if isinstance(config, dict) else {}
    today = _today(config, data)
    if today is None:
        notes.append("no as_of date in config or status.json: nothing priced")
        return _empty(notes)

    features = _features(data)
    if not features:
        notes.append("no fields in fields.geojson: nothing priced")
        return _empty(notes)
    status = _status_by_field(data)
    weather = data.get("weather") if isinstance(data.get("weather"), dict) else {}
    temps, recent = temperature_by_day(weather)
    if not temps:
        notes.append(f"weather.json has no temperatures: dry-down run at {DEFAULT_TMEAN_C:.0f} C")
    fc_days = forecast_by_day(weather, today)
    has_forecast = bool(fc_days)
    if not has_forecast:
        notes.append("no live forecast in weather.json: no weather-risk premium, no freeze or wet-dry events")
    elif any(day not in fc_days for day in wait_days(today)):
        notes.append("forecast does not cover the whole wait week (tomorrow to today + 7): weather risk judged on the days it has")
    lags = {c: _lag_days(data, c) for c in CROPS}
    if all(v is None for v in lags.values()):
        notes.append("status.json has no peak_to_harvest_days: ready fields dated from their clamped window start")
    try:
        machine_rate = float(config.get("machine_rate_ha_per_day") or DEFAULT_MACHINE_RATE_HA_PER_DAY)
        if not math.isfinite(machine_rate) or machine_rate <= 0:
            raise ValueError
    except (TypeError, ValueError):
        machine_rate = DEFAULT_MACHINE_RATE_HA_PER_DAY
        notes.append(f"machine_rate_ha_per_day unreadable: using {machine_rate:.0f}")

    fields = {}
    skipped = Counter()
    counts = Counter()
    by_crop = {}
    zones = {zid: {"harvest_now": 0, "wait": 0, "either": 0, "evaluated": 0} for zid in _zone_ids(data)}
    totals = {"crop_value": 0.0, "drying_now": 0.0, "drying_after": 0.0, "shrink_now": 0.0, "shrink_after": 0.0,
              "cost_now": 0.0, "cost_after": 0.0, "savings": 0.0, "field_loss_wait": 0.0}
    for fid, props in features:
        rec = status.get(fid)
        if rec is None:
            fields[fid] = None
            skipped["not in status.json"] += 1
            continue
        try:
            row, why = evaluate_field(fid, props, rec, today, temps, recent, fc_days, machine_rate, prior, has_forecast,
                                      lags)
        except Exception as e:                       # one bad field never takes the landscape down
            row, why = None, f"could not price ({type(e).__name__})"
        fields[fid] = row
        if row is None:
            skipped[why] += 1
            continue
        r = row["recommendation"]
        counts[r] += 1
        zid = row["zone_id"]
        z = zones.setdefault(zid, {"harvest_now": 0, "wait": 0, "either": 0, "evaluated": 0})
        z[r] += 1
        z["evaluated"] += 1
        waits = r == "wait" or (r == "either" and row["cost_wait_7d_usd"] <= row["cost_now_usd"])
        totals["crop_value"] += row["crop_value_usd"]
        totals["drying_now"] += row["drying_usd_now"]
        totals["shrink_now"] += row["shrink_usd_now"]
        totals["cost_now"] += row["cost_now_usd"]
        totals["drying_after"] += row["drying_usd_7d"] if waits else row["drying_usd_now"]
        totals["shrink_after"] += row["shrink_usd_7d"] if waits else row["shrink_usd_now"]
        totals["cost_after"] += row["cost_wait_7d_usd"] if waits else row["cost_now_usd"]
        totals["savings"] += row["savings_usd"] if r != "either" else 0.0
        totals["field_loss_wait"] += row["field_loss_usd_7d"]
        c = by_crop.setdefault(row["crop"], {"fields": 0, "harvest_now": 0, "wait": 0, "either": 0,
                                              "crop_value_usd": 0.0, "bushels": 0.0, "drying_usd_now": 0.0})
        c["fields"] += 1
        c[r] += 1
        c["crop_value_usd"] += row["crop_value_usd"]
        c["bushels"] += row["bushels"]
        c["drying_usd_now"] += row["drying_usd_now"]

    for why, n in sorted(skipped.items()):
        notes.append(f"{n} field{'s' if n != 1 else ''} not priced: {why}")
    evaluated = sum(counts.values())
    if evaluated == 0:
        notes.append("no standing corn or soybean field with a harvest estimate")
    for c in by_crop.values():
        for k in ("crop_value_usd", "bushels", "drying_usd_now"):
            c[k] = _num(c[k])
    landscape = {
        "as_of": today.isoformat(),
        "fields_evaluated": evaluated,
        "counts": {"harvest_now": counts["harvest_now"], "wait": counts["wait"], "either": counts["either"]},
        "crop_value_usd_total": _num(totals["crop_value"]),
        "drying_usd_now_total": _num(totals["drying_now"]),
        "drying_usd_after_recommendation_total": _num(totals["drying_after"]),
        "shrink_usd_now_total": _num(totals["shrink_now"]),
        "shrink_usd_after_recommendation_total": _num(totals["shrink_after"]),
        "cost_usd_now_total": _num(totals["cost_now"]),
        "cost_usd_after_recommendation_total": _num(totals["cost_after"]),
        "savings_usd_total": _num(totals["savings"]),
        "field_loss_usd_if_all_wait": _num(totals["field_loss_wait"]),
        "by_crop": by_crop,
        "notes": notes,
    }
    return {"key": KEY, "fields": fields, "landscape": landscape, "zones": zones,
            "assumptions": dict(ASSUMPTIONS), "method": METHOD}
