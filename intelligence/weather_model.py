"""A small, explainable harvest-window model on top of weather.json (NASA POWER observed + Open-Meteo forecast).

  growing degree days from emergence (observed tmax / tmin)      -> physiological maturity
  grain dry-down after maturity (corn) or days after leaf drop   -> combine-ready date
  rain                                                            -> days a combine cannot run
  forecast minimum temperature                                    -> frost nights
  => the best combine window inside the forecast (the first run of workable days once the crop is ready), the next
     window after the rain, and a one-sentence summary for the farmer's plan.

The numbers are agronomy rules of thumb (Iowa State extension style) kept in CROPS so they can be tuned; the
satellite still decides what actually happened (harvest_model.py), and decide.py uses the satellite's own
predicted harvest date as the "ready" date when it has one, so this model mostly adds weather: workability,
rain, frost, dry-down. Nothing here is a yield forecast.
"""
from datetime import date, timedelta

CROPS = {
    "corn": {"base_c": 10.0, "cap_c": 30.0, "gdd_to_maturity": 1450.0,            # black layer, ~2700 GDD F from emergence
             "moisture_at_maturity": 32.0, "ready_moisture": 25.0,                  # kernel moisture, %
             "drydown_per_c": 0.03, "drydown_min": 0.30},                           # points per day: ~0.6 at 20 C, 0.3 floor
    "soybean": {"base_c": 10.0, "cap_c": 30.0, "gdd_to_maturity": 1250.0,
                "days_after_leaf_drop": 7},                                         # ~13% moisture a week after leaf drop, dry weather
}
DEFAULT_CROP = "corn"
RAIN_BLOCK_MM = 12.0      # that day and the next are not combinable
SOAK_MM = 25.0            # two days after
SHOWER_MM = 5.0           # a shower stops the combine that day only
FROST_C = 0.0
MAX_EXTRAPOLATE_DAYS = 60


def _crop(crop):
    return CROPS.get((crop or DEFAULT_CROP).lower(), CROPS[DEFAULT_CROP])


def _dates(doc):
    return [date.fromisoformat(x[:10]) for x in doc.get("dates", [])]


def gdd_day(tmax, tmin, crop="corn"):
    """Growing degree days for one day: mean of the capped/floored extremes minus the base."""
    if tmax is None or tmin is None:
        return 0.0
    c = _crop(crop)
    hi = min(max(tmax, c["base_c"]), c["cap_c"])
    lo = min(max(tmin, c["base_c"]), c["cap_c"])
    return max(0.0, (hi + lo) / 2 - c["base_c"])


def _daily(obs):
    """date -> (tmax, tmin, precip) from the observed block (missing arrays tolerated)."""
    out = {}
    tmax, tmin, pr = obs.get("tmax_c") or [], obs.get("tmin_c") or [], obs.get("precip_mm") or []
    for i, d in enumerate(_dates(obs)):
        out[d] = (tmax[i] if i < len(tmax) else None, tmin[i] if i < len(tmin) else None, pr[i] if i < len(pr) else None)
    return out


def gdd_sum(obs, start, end, crop="corn"):
    daily = _daily(obs)
    return sum(gdd_day(v[0], v[1], crop) for d, v in daily.items() if start <= d <= end)


def maturity_date(obs, emergence, crop="corn"):
    """First day the accumulated degree days since emergence reach the crop's requirement, else None."""
    need = _crop(crop)["gdd_to_maturity"]
    total = 0.0
    for d, (hi, lo, _) in sorted(_daily(obs).items()):
        if d < emergence:
            continue
        total += gdd_day(hi, lo, crop)
        if total >= need:
            return d
    return None


def combine_ready_date(obs, maturity, crop="corn"):
    """Corn: kernel moisture from 32% at maturity, drying max(0.3, 0.03 * Tmean) points a day (recent mean temperature
    carries beyond the observed record). Soybeans: a week after leaf drop, plus a day for every soaking rain."""
    c = _crop(crop)
    daily = _daily(obs)
    if "days_after_leaf_drop" in c:
        ready = maturity + timedelta(days=c["days_after_leaf_drop"])
        wet = sum(1 for d, v in daily.items() if maturity <= d <= ready and (v[2] or 0) >= RAIN_BLOCK_MM)
        return ready + timedelta(days=wet)
    means = [(hi + lo) / 2 for hi, lo, _ in daily.values() if hi is not None and lo is not None]
    recent = sum(means[-7:]) / len(means[-7:]) if means else 15.0
    moisture, d = c["moisture_at_maturity"], maturity
    for _ in range(MAX_EXTRAPOLATE_DAYS + len(daily)):
        if moisture <= c["ready_moisture"]:
            return d
        hi, lo, _ = daily.get(d, (None, None, None))
        tmean = (hi + lo) / 2 if hi is not None and lo is not None else recent
        moisture -= max(c["drydown_min"], c["drydown_per_c"] * tmean)
        d += timedelta(days=1)
    return d


def workable_days(obs, fc, through):
    """date -> can a combine run that day. A shower (>= 5 mm) blocks the day; >= 12 mm also the next day;
    >= 25 mm the two following days. Observed precipitation first, then the forecast."""
    precip = {}
    for d, (_, _, p) in _daily(obs).items():
        precip[d] = p or 0.0
    for d, p in zip(_dates(fc), fc.get("precip_mm") or []):
        precip[d] = p or 0.0
    if not precip:
        return {}
    start = min(precip)
    out = {}
    d = start
    while d <= through:
        p0 = precip.get(d, 0.0)
        p1 = precip.get(d - timedelta(days=1), 0.0)
        p2 = precip.get(d - timedelta(days=2), 0.0)
        out[d] = not (p0 >= SHOWER_MM or p1 >= RAIN_BLOCK_MM or p2 >= SOAK_MM)
        d += timedelta(days=1)
    return out


def frost_nights(fc):
    """Forecast dates with a minimum at or below 0 C; None when the forecast has no temperatures."""
    tmin = fc.get("tmin_c")
    if not tmin:
        return None
    return [d for d, t in zip(_dates(fc), tmin) if t is not None and t <= FROST_C]


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


def harvest_outlook(obs, fc, today, crop="corn", emergence=None, ready_override=None, horizon_days=14):
    """The weather side of the harvest decision for one field. ready_override is the satellite's own harvest
    estimate when it has one (preferred); otherwise degree days and dry-down give it."""
    maturity = maturity_date(obs, emergence, crop) if emergence else None
    ready = ready_override or (combine_ready_date(obs, maturity, crop) if maturity else None)
    fc_dates = _dates(fc)[:horizon_days]
    through = fc_dates[-1] if fc_dates else today
    work = workable_days(obs, fc, through)
    precip = dict(zip(fc_dates, fc.get("precip_mm") or []))
    rain_days = [d for d in fc_dates if (precip.get(d) or 0) >= RAIN_BLOCK_MM]
    frost = frost_nights(fc)
    candidates = [d for d in fc_dates if d >= today and ready is not None and d >= ready and work.get(d, True)]
    runs = _runs(candidates)
    window = {"start": runs[0][0].isoformat(), "end": runs[0][-1].isoformat()} if runs else None
    next_window = {"start": runs[1][0].isoformat(), "end": runs[1][-1].isoformat()} if len(runs) > 1 else None

    if ready is None:
        summary = "Not mature yet by the degree days so far; no combine-ready date."
    elif ready > through:
        summary = f"Combine-ready about {ready.isoformat()} (still drying down); no window inside the forecast."
    elif window:
        summary = f"Combine-ready since {ready.isoformat()}." if ready <= today else f"Combine-ready from {ready.isoformat()}."
        summary += f" Best window {window['start']} to {window['end']}"
        after = [d for d in rain_days if d > runs[0][-1]]
        if after:
            summary += f", before {precip.get(after[0]):.0f} mm of rain on {after[0].isoformat()}"
        if next_window:
            summary += f"; next window {next_window['start']} to {next_window['end']}"
        summary += "."
    else:
        summary = f"Combine-ready since {ready.isoformat()}, but no workable day left in the forecast (rain)."
    if frost:
        summary += " Frost night on " + ", ".join(d.isoformat() for d in frost) + "."
    return {
        "crop": (crop or DEFAULT_CROP).lower(),
        "maturity": maturity.isoformat() if maturity else None,
        "combine_ready": ready.isoformat() if ready else None,
        "window": window, "next_window": next_window,
        "rain_days": [d.isoformat() for d in rain_days],
        "frost_nights": None if frost is None else [d.isoformat() for d in frost],
        "workable_days_in_forecast": sum(1 for d in fc_dates if work.get(d, True)),
        "summary": summary,
    }
