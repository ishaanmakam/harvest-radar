"""A small, explainable harvest-window model on top of weather.json (NASA POWER observed + Open-Meteo forecast).

  growing degree days from green-up (or emergence)                  -> physiological maturity
  grain dry-down after maturity (corn) or days after maturity (soy)  -> combine-ready date
  rain                                                               -> days a combine cannot run
  forecast minimum temperature                                       -> frost nights (light) and hard freezes
  => the best combine window inside the live forecast (the first run of workable days once the crop is ready), the
     next window after the rain, and a one-sentence summary for the farmer's plan.

The numbers are agronomy rules of thumb (Iowa State extension style) kept in CROPS so they can be tuned; the
satellite still decides what actually happened (harvest_model.py), and decide.py hands this model the satellite's own
harvest estimate as ready_override whenever it has one, so the degree-day path is a fallback. Nothing here is a yield
forecast.

Degree-day start: decide.py knows the first clear pass with NDVI >= 0.4, which for corn is roughly V6 (three to four
weeks after emergence), not emergence, so the budget charged from that date is the shorter green-up budget
(ISU PMR 1009: V6 -> R6 about 2225 GDD F = 1240 C; VE -> R6 about 2580 F = 1450 C). A start before the observed
record is unknown (None), never silently truncated.
"""
from datetime import date, timedelta

CROPS = {
    "corn": {"base_c": 10.0, "cap_c": 30.0,
             "gdd_emergence_to_maturity": 1450.0,    # VE -> R6 black layer, ~2580 GDD F (ISU PMR 1009)
             "gdd_green_up_to_maturity": 1240.0,     # first NDVI >= 0.4 pass (~V6) -> R6, ~2225 GDD F
             "moisture_at_maturity": 32.0, "ready_moisture": 25.0,   # kernel moisture, %
             "drydown_per_c": 0.03, "drydown_min": 0.10},           # points per day: ~0.6 at 20 C, ~0.1 in November cold
    "soybean": {"base_c": 10.0, "cap_c": 30.0,
                "gdd_emergence_to_maturity": 1400.0,  # VE -> R7 for maturity group 2.5-3.0 (NDSU 2016, scaled to central Iowa)
                "gdd_green_up_to_maturity": 1150.0,   # first NDVI >= 0.4 pass -> R7
                "days_after_maturity": 14},           # R7 -> R8 leaf drop ~10 days, then ~4 dry days to ~13% moisture
}
DEFAULT_CROP = "corn"
RAIN_BLOCK_MM = 12.0        # that day and the next are not combinable
SOAK_MM = 25.0              # two days after
SHOWER_MM = 5.0             # a shower stops the combine that day only
FROST_C = 0.0               # light frost
HARD_FREEZE_C = -2.0        # killing freeze
MAX_EXTRAPOLATE_DAYS = 60   # corn not field-dry this long after maturity is "not before winter": plan to dry it
DRYDOWN_BEFORE_HARVEST_DAYS = 14   # the satellite's harvest estimate is combine-ready; physiological maturity is about two weeks earlier


def _crop(crop):
    return CROPS.get((crop or DEFAULT_CROP).lower(), CROPS[DEFAULT_CROP])


def _series(doc, key):
    """date -> value for one array of a weather block. Dates may be unsorted or repeated (the last value wins)."""
    vals = doc.get(key) or []
    out = {}
    for i, x in enumerate(doc.get("dates") or []):
        if i < len(vals):
            out[date.fromisoformat(x[:10])] = vals[i]
    return out


def _dates(doc):
    return sorted({date.fromisoformat(x[:10]) for x in doc.get("dates") or []})


def gdd_day(tmax, tmin, crop="corn"):
    """Growing degree days for one day: mean of the capped/floored extremes minus the base."""
    if tmax is None or tmin is None:
        return 0.0
    c = _crop(crop)
    hi = min(max(tmax, c["base_c"]), c["cap_c"])
    lo = min(max(tmin, c["base_c"]), c["cap_c"])
    return max(0.0, (hi + lo) / 2 - c["base_c"])


def _daily(obs):
    """date -> (tmax, tmin, precip) from the observed block, sorted (missing arrays tolerated)."""
    tmax, tmin, pr = _series(obs, "tmax_c"), _series(obs, "tmin_c"), _series(obs, "precip_mm")
    return {d: (tmax.get(d), tmin.get(d), pr.get(d)) for d in _dates(obs)}


def gdd_sum(obs, start, end, crop="corn"):
    return sum(gdd_day(v[0], v[1], crop) for d, v in _daily(obs).items() if start <= d <= end)


def maturity_date(obs, emergence, crop="corn", start_stage="emergence"):
    """First day the degree days accumulated since the start date reach the crop's budget for that start stage
    ("emergence", or "green_up" = the first NDVI >= 0.4 pass), else None. None too when the start is None or lies
    before the observed record: the missing days cannot be summed, so the answer is unknown, not late."""
    if emergence is None:
        return None
    daily = _daily(obs)
    if not daily or emergence < min(daily):
        return None
    c = _crop(crop)
    need = c["gdd_green_up_to_maturity"] if start_stage == "green_up" else c["gdd_emergence_to_maturity"]
    total = 0.0
    for d, (hi, lo, _) in daily.items():
        if d < emergence:
            continue
        total += gdd_day(hi, lo, crop)
        if total >= need:
            return d
    return None


def _tmeans(obs, fc=None):
    """date -> mean temperature: the observed record, then the forecast where it carries temperatures."""
    out = {}
    for d, (hi, lo, _) in _daily(obs).items():
        if hi is not None and lo is not None:
            out[d] = (hi + lo) / 2
    if fc:
        hi_s, lo_s = _series(fc, "tmax_c"), _series(fc, "tmin_c")
        for d in _dates(fc):
            if hi_s.get(d) is not None and lo_s.get(d) is not None:
                out[d] = (hi_s[d] + lo_s[d]) / 2
    return out


def combine_ready_date(obs, maturity, crop="corn", fc=None):
    """Corn: kernel moisture from 32% at maturity, drying max(0.10, 0.03 * Tmean) points a day; temperatures come from
    the observed record, then the forecast, then the mean of the last seven known days. None when the grain is not
    field-dry within MAX_EXTRAPOLATE_DAYS of maturity at those temperatures (plan to dry it).
    Soybeans: days_after_maturity after R7, plus a day for every shower in between."""
    if maturity is None:
        return None
    c = _crop(crop)
    if "days_after_maturity" in c:
        ready = maturity + timedelta(days=c["days_after_maturity"])
        pr = {d: (p or 0.0) for d, p in _series(obs, "precip_mm").items()}
        if fc:
            pr.update({d: (p or 0.0) for d, p in _series(fc, "precip_mm").items()})
        wet = sum(1 for d, p in pr.items() if maturity <= d <= ready and p >= SHOWER_MM)
        return ready + timedelta(days=wet)
    temps = _tmeans(obs, fc)
    known = sorted(temps)
    tail = [temps[d] for d in known[-7:]]
    recent = sum(tail) / len(tail) if tail else 15.0
    moisture, d = c["moisture_at_maturity"], maturity
    while d <= maturity + timedelta(days=MAX_EXTRAPOLATE_DAYS):
        if moisture <= c["ready_moisture"]:
            return d
        moisture -= max(c["drydown_min"], c["drydown_per_c"] * temps.get(d, recent))
        d += timedelta(days=1)
    return None


def workable_from_precip(precip, start, through):
    """date -> can a combine run that day, from a date -> mm map. A shower (>= 5 mm) blocks the day; >= 12 mm also
    the next day; >= 25 mm the two following days."""
    out = {}
    d = start
    while d <= through:
        p0 = precip.get(d) or 0.0
        p1 = precip.get(d - timedelta(days=1)) or 0.0
        p2 = precip.get(d - timedelta(days=2)) or 0.0
        out[d] = not (p0 >= SHOWER_MM or p1 >= RAIN_BLOCK_MM or p2 >= SOAK_MM)
        d += timedelta(days=1)
    return out


def workable_days(obs, fc, through):
    """Workability from the first observed day through `through`: observed precipitation first, then the forecast."""
    precip = {d: (p or 0.0) for d, p in _series(obs, "precip_mm").items()}
    precip.update({d: (p or 0.0) for d, p in _series(fc, "precip_mm").items()})
    if not precip:
        return {}
    return workable_from_precip(precip, min(precip), through)


def first_fit_day(precip, after, limit_days=14):
    """The first day after `after` on which a combine can run, by the same rule; None when none within limit_days."""
    w = workable_from_precip(precip, after + timedelta(days=1), after + timedelta(days=limit_days))
    return next((d for d in sorted(w) if w[d]), None)


def frost_nights(fc, threshold=FROST_C):
    """Forecast dates with a minimum at or below the threshold; None when the forecast has no temperatures."""
    tmin = _series(fc, "tmin_c")
    if not tmin:
        return None
    return [d for d in sorted(tmin) if tmin[d] is not None and tmin[d] <= threshold]


def hard_freezes(fc):
    return frost_nights(fc, HARD_FREEZE_C)


def window_phrase(start, end, today):
    """How a farmer hears a short window: today only / today and tomorrow (with the dates), else the date pair."""
    n = (end - start).days + 1
    if start == today and n == 1:
        return f"today only ({start.isoformat()})"
    if start == today and n == 2:
        return f"today and tomorrow ({start.isoformat()} to {end.isoformat()})"
    return f"{start.isoformat()} to {end.isoformat()}"


def _runs(dates):
    runs, cur = [], []
    for d in dates:
        if cur and (d - cur[-1]).days != 1:
            runs.append(cur)
            cur = []
        cur.append(d)
    if cur:
        runs.append(cur)
    return runs


def _mm(x):
    return f"{x:.0f} mm"


def harvest_outlook(obs, fc, today, crop="corn", emergence=None, ready_override=None, horizon_days=14, green_up=None):
    """The weather side of the harvest decision for one field.
    ready_override is the satellite's own estimate when it has one (preferred): the predicted harvest start for a
    ready field, the window centre for a maturing one. Otherwise degree days from green_up (first NDVI >= 0.4 pass) or
    emergence, then dry-down. Only forecast days on or after today count; a forecast that ends before today is stale
    and is named as such, never read as rain."""
    crop_key = (crop or DEFAULT_CROP).lower()
    if green_up:
        maturity = maturity_date(obs, green_up, crop, "green_up")
    elif emergence:
        maturity = maturity_date(obs, emergence, crop, "emergence")
    else:
        maturity = None
    ready, source = None, None
    if ready_override:
        ready, source = ready_override, "satellite"
    elif maturity:
        ready, source = combine_ready_date(obs, maturity, crop, fc=fc), "degree_days"   # None = not field-dry before winter

    all_dates = _dates(fc)
    live = [d for d in all_dates if d >= today][:horizon_days]
    stale = bool(all_dates) and not live
    through = live[-1] if live else today
    precip_fc = _series(fc, "precip_mm")
    rain_mm = {d: float(precip_fc[d]) for d in live if (precip_fc.get(d) or 0) >= SHOWER_MM}
    work = workable_days(obs, fc, through) if live else {}
    frost = frost_nights(fc)
    hard = hard_freezes(fc) if frost is not None else None
    if frost is not None:
        frost = [d for d in frost if d in live]
    candidates = [d for d in live if ready is not None and d >= ready and work.get(d, True)]
    runs = _runs(candidates)
    window = {"start": runs[0][0].isoformat(), "end": runs[0][-1].isoformat()} if runs else None
    next_window = {"start": runs[1][0].isoformat(), "end": runs[1][-1].isoformat()} if len(runs) > 1 else None
    to_horizon = bool(runs) and runs[0][-1] == through
    next_to_horizon = len(runs) > 1 and runs[1][-1] == through

    def facts():
        if rain_mm:
            return "Rain on " + ", ".join(f"{d.isoformat()} ({_mm(rain_mm[d])})" for d in sorted(rain_mm)) + "."
        return f"Dry through {through.isoformat()}."

    def frost_note():
        if not frost:
            return ""
        named = ", ".join(f"{d.isoformat()} ({'hard freeze' if hard and d in hard else 'light frost'})" for d in frost)
        # the satellite's reading wins: its harvest estimate less the dry-down fortnight is when the crop was mature;
        # only the degree-day path uses its own maturity date
        if source == "satellite":
            mature = ready - timedelta(days=DRYDOWN_BEFORE_HARVEST_DAYS) <= frost[0]
        else:
            mature = maturity is not None and maturity <= frost[0]
        tail = (": the crop is mature, so it will not hurt the grain." if mature
                else ": if the crop is still filling it may lose some yield; nothing to do today.")
        return f" Frost on {named}{tail}"

    if stale:
        summary = f"Forecast ends {all_dates[-1].isoformat()}, before today: refresh weather.json before trusting a combine window."
    elif ready is None:
        if source == "degree_days":
            summary = (f"Mature about {maturity.isoformat()} by the degree days, but the grain will not field-dry within two "
                       f"months at these temperatures: plan to dry it. " + facts())
        elif green_up or emergence:
            summary = "Not mature yet by the degree days so far; no combine-ready date. " + facts()
        else:
            summary = "No combine-ready estimate yet. " + facts()
    elif ready > today and source == "satellite":
        lead = (f"Not combine-ready before {ready.isoformat()}. " if ready <= through
                else f"Not combine-ready inside the forecast (expected about {ready.isoformat()}). ")
        summary = lead + facts()
    elif ready > through:
        summary = f"Combine-ready about {ready.isoformat()} by the degree days (still drying down); no window inside the forecast. " + facts()
    else:
        lead = f"Combine-ready since {ready.isoformat()}." if ready <= today else f"Combine-ready from {ready.isoformat()} by the degree days."
        if window is None:
            summary = lead + (" No workable day in the forecast: rain on " + ", ".join(d.isoformat() for d in sorted(rain_mm)) + "."
                              if rain_mm else " No workable day in the forecast.")
        elif to_horizon:
            ws = runs[0][0]
            summary = lead + (f" Dry through {through.isoformat()}: start as soon as the combine is free." if ws <= today
                              else f" Field fit from {ws.isoformat()}, then dry through {through.isoformat()}.")
        else:
            we = runs[0][-1]
            summary = lead + f" Best window {window_phrase(runs[0][0], we, today)}"
            breaker = next((d for d in sorted(rain_mm) if d > we), None)
            if breaker is not None:
                mm = rain_mm[breaker]
                summary += (f", before {_mm(mm)} of rain on {breaker.isoformat()}" if mm >= RAIN_BLOCK_MM
                            else f", until a shower ({_mm(mm)}) on {breaker.isoformat()}")
            if next_window:
                summary += (f"; next window from {next_window['start']}" if next_to_horizon
                            else f"; next window {next_window['start']} to {next_window['end']}")
            summary += "."
    if not stale:
        summary += frost_note()
    return {
        "crop": crop_key,
        "maturity": maturity.isoformat() if maturity else None,
        "combine_ready": ready.isoformat() if ready else None,
        "ready_source": source,
        "window": window, "next_window": next_window, "window_to_horizon": to_horizon,
        "rain_days": [d.isoformat() for d in sorted(rain_mm)],
        "rain_mm": {d.isoformat(): rain_mm[d] for d in sorted(rain_mm)},
        "frost_nights": None if frost is None else [d.isoformat() for d in frost],
        "hard_freeze": None if hard is None else [d.isoformat() for d in hard if d in live],
        "workable_days_in_forecast": sum(1 for d in live if work.get(d, True)),
        "forecast_stale": stale,
        "horizon_end": through.isoformat() if live else None,
        "summary": summary,
    }
