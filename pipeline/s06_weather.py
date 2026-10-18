"""Observed daily weather and the 14-day rain forecast at the centre of the demo box."""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from jsonschema import Draft202012Validator

from common import CACHE, OUT, ROOT, load_region, log, write_json

STEP = "s06"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
PREV_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_BASES = (
    "precipitation",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_gusts_10m",
    "precipitation_probability",
)
POWER_PARAMS = ("PRECTOTCORR", "T2M_MAX", "T2M_MIN")
FILL_FLOOR = -999


def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


def iso(day: date) -> str:
    return day.isoformat()


def parse_day(text: str) -> date:
    return date.fromisoformat(text)


def days_inclusive(start: date, end: date):
    out = []
    day = start
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return out


def centre(bbox):
    west, south, east, north = bbox
    return (west + east) / 2, (south + north) / 2


def as_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_power(value, fill):
    number = as_number(value)
    if number is None:
        return None
    if number <= FILL_FLOOR or (fill is not None and number == fill):
        return None
    return round(number, 1)


def clean_meteo_amount(value):
    number = as_number(value)
    if number is None:
        return None
    return round(number, 1)


def clean_prob(value):
    number = as_number(value)
    if number is None:
        return None
    if number < 0 or number > 100:
        log(STEP, f"ERROR precip probability {number} is outside 0-100")
        sys.exit(1)
    return int(round(number))


def fetch_json(url, params, timeout):
    last = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            log(STEP, f"request failed ({attempt}/3) {url}: {exc}")
            if attempt < 3:
                time.sleep(attempt)
    log(STEP, f"ERROR giving up on {url}: {last}")
    sys.exit(1)


def require_power(raw):
    try:
        fill = as_number(raw["header"]["fill_value"])
        parameter = raw["properties"]["parameter"]
        for name in POWER_PARAMS:
            if not isinstance(parameter[name], dict):
                raise TypeError(name)
    except (KeyError, TypeError) as exc:
        log(STEP, f"ERROR NASA POWER response shape is not the documented one: {exc}")
        sys.exit(1)
    return fill, parameter


DAILY_KEYS = (
    "time",
    "precipitation_sum",
    "precipitation_probability_max",
    "temperature_2m_max",
    "temperature_2m_min",
    "relative_humidity_2m_mean",
    "wind_gusts_10m_max",
)
FORECAST_KEYS = (
    "source",
    "dates",
    "precip_mm",
    "precip_prob",
    "tmin_c",
    "tmax_c",
    "rh_mean_pct",
    "wind_gust_ms",
)


def require_meteo(raw, from_cache=False):
    daily = raw.get("daily") if isinstance(raw, dict) else None
    if not isinstance(daily, dict):
        log(STEP, "ERROR Open-Meteo response has no daily object")
        sys.exit(1)
    missing = [key for key in DAILY_KEYS if not isinstance(daily.get(key), list)]
    if missing and from_cache:
        log(STEP, f"ERROR cached Open-Meteo response lacks {', '.join(missing)}. Run once without --from-cache.")
        sys.exit(1)
    try:
        hourly = raw["hourly"]
        for key in missing:
            raise TypeError(f"daily.{key}")
        for key in ("time", "precipitation", "wind_gusts_10m", "weather_code"):
            if not isinstance(hourly[key], list):
                raise TypeError(f"hourly.{key}")
        count = len(daily["time"])
        for key in DAILY_KEYS:
            if len(daily[key]) != count:
                raise ValueError(f"daily {key} length")
        if len(hourly["time"]) != len(hourly["wind_gusts_10m"]):
            raise ValueError("hourly wind_gusts_10m length")
    except (KeyError, TypeError, ValueError) as exc:
        log(STEP, f"ERROR Open-Meteo response shape is not the documented one: {exc}")
        sys.exit(1)
    gust_unit = (raw.get("daily_units") or {}).get("wind_gusts_10m_max")
    if gust_unit != "m/s":
        log(STEP, f"ERROR wind_gusts_10m_max unit is {gust_unit!r}, expected m/s")
        sys.exit(1)
    return daily, hourly


def series_by_day(dates, values):
    out = {}
    for day, value in zip(dates, values):
        out[day] = value
    return out


def build_weather(region, power, meteo, from_cache=False):
    fill, parameter = require_power(power)
    daily, hourly = require_meteo(meteo, from_cache=from_cache)
    season_start = parse_day(region["season_start"])
    as_of = parse_day(region["as_of"])
    observed_days = days_inclusive(season_start, as_of - timedelta(days=1))
    if not observed_days:
        log(STEP, "ERROR observed range is empty")
        sys.exit(1)

    meteo_days = series_by_day(daily["time"], range(len(daily["time"])))
    fill_from = as_of - timedelta(days=14)
    filled_days = 0
    precip, tmax, tmin = [], [], []
    for day in observed_days:
        key = ymd(day)
        row = {
            "precip_mm": clean_power(parameter["PRECTOTCORR"].get(key), fill),
            "tmax_c": clean_power(parameter["T2M_MAX"].get(key), fill),
            "tmin_c": clean_power(parameter["T2M_MIN"].get(key), fill),
        }
        if fill_from <= day < as_of and any(value is None for value in row.values()):
            index = meteo_days.get(iso(day))
            if index is not None:
                replacements = {
                    "precip_mm": clean_meteo_amount(daily["precipitation_sum"][index]),
                    "tmax_c": clean_meteo_amount(daily["temperature_2m_max"][index]),
                    "tmin_c": clean_meteo_amount(daily["temperature_2m_min"][index]),
                }
                wrote = False
                for name, value in replacements.items():
                    if row[name] is None and value is not None:
                        row[name] = value
                        wrote = True
                if wrote:
                    filled_days += 1
        precip.append(row["precip_mm"])
        tmax.append(row["tmax_c"])
        tmin.append(row["tmin_c"])

    source = "NASA POWER daily (community AG)"
    if filled_days:
        source += f"; last {filled_days} days filled from Open-Meteo"

    horizon = int(region["forecast_horizon_days"])
    forecast_days = [day for day in daily["time"] if day >= iso(as_of)][:horizon]
    if len(forecast_days) != horizon:
        log(STEP, f"ERROR Open-Meteo has {len(forecast_days)} days on or after {iso(as_of)}, need {horizon}")
        sys.exit(1)
    fc_precip, fc_prob, fc_tmin, fc_tmax, fc_rh, fc_gust = [], [], [], [], [], []
    for day in forecast_days:
        index = meteo_days[day]
        fc_precip.append(clean_meteo_amount(daily["precipitation_sum"][index]))
        fc_prob.append(clean_prob(daily["precipitation_probability_max"][index]))
        fc_tmin.append(clean_meteo_amount(daily["temperature_2m_min"][index]))
        fc_tmax.append(clean_meteo_amount(daily["temperature_2m_max"][index]))
        fc_rh.append(clean_meteo_amount(daily["relative_humidity_2m_mean"][index]))
        fc_gust.append(clean_meteo_amount(daily["wind_gusts_10m_max"][index]))

    doc = {
        "observed": {
            "source": source,
            "dates": [iso(day) for day in observed_days],
            "precip_mm": precip,
            "tmax_c": tmax,
            "tmin_c": tmin,
        },
        "forecast": {
            "source": "Open-Meteo forecast",
            "dates": forecast_days,
            "precip_mm": fc_precip,
            "precip_prob": fc_prob,
            "tmin_c": fc_tmin,
            "tmax_c": fc_tmax,
            "rh_mean_pct": fc_rh,
            "wind_gust_ms": fc_gust,
        },
    }
    return doc, filled_days, hourly


def nulls(values):
    return sum(value is None for value in values)


def rain_total(values):
    return round(sum(value for value in values if value is not None), 1)


def max_gust(hourly, as_of: date):
    start = datetime(as_of.year, as_of.month, as_of.day)
    end = start + timedelta(hours=72)
    best = None
    for stamp, gust in zip(hourly["time"], hourly["wind_gusts_10m"]):
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            log(STEP, f"ERROR hourly time {stamp!r} is not a timestamp")
            sys.exit(1)
        number = as_number(gust)
        if number is None or moment < start or moment >= end:
            continue
        best = number if best is None else max(best, number)
    unit = (hourly.get("hourly_units") or {}).get("wind_gusts_10m")
    # Units live on the parent response, not inside hourly. Caller passes them via the raw file.
    return best, unit


def gust_from_raw(raw, as_of: date):
    hourly = raw["hourly"]
    best, _unit = max_gust(hourly, as_of)
    units = raw.get("hourly_units") or {}
    return best, units.get("wind_gusts_10m", "km/h")


def validate(doc):
    schema = json.loads((ROOT / "contracts" / "weather.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda err: list(err.path))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log(STEP, f"ERROR weather.json @ {loc}: {err.message}")
    region = load_region()
    as_of = parse_day(region["as_of"])
    expected_observed = [iso(day) for day in days_inclusive(parse_day(region["season_start"]), as_of - timedelta(days=1))]
    horizon = int(region["forecast_horizon_days"])
    expected_forecast = [iso(as_of + timedelta(days=offset)) for offset in range(horizon)]
    mock = json.loads((ROOT / "data" / "mock" / "weather.json").read_text())
    observed, forecast = doc["observed"], doc["forecast"]
    if observed["dates"] != expected_observed:
        log(STEP, f"ERROR observed.dates must run {expected_observed[0]}..{expected_observed[-1]} ({len(expected_observed)} days)")
        if observed["dates"]:
            log(STEP, f"got {observed['dates'][0]}..{observed['dates'][-1]} n={len(observed['dates'])}")
        errors.append("dates")
    if forecast["dates"] != expected_forecast:
        log(STEP, f"ERROR forecast.dates must start {iso(as_of)} and have {horizon} days")
        log(STEP, f"got {forecast['dates']}")
        errors.append("dates")
    for block, name in ((observed, "observed"), (forecast, "forecast")):
        count = len(block["dates"])
        for key, values in block.items():
            if key == "source":
                continue
            if len(values) != count:
                log(STEP, f"ERROR {name}.{key} has {len(values)} values for {count} dates")
                errors.append(key)
    if list(doc) != ["observed", "forecast"]:
        log(STEP, f"ERROR top-level keys {list(doc)}")
        errors.append("keys")
    if list(observed) != ["source", "dates", "precip_mm", "tmax_c", "tmin_c"]:
        log(STEP, f"ERROR observed keys {list(observed)}")
        errors.append("keys")
    if list(forecast) != list(FORECAST_KEYS):
        log(STEP, f"ERROR forecast keys {list(forecast)}")
        errors.append("keys")
    if list(mock) != ["observed", "forecast"] or list(mock["observed"]) != list(observed):
        log(STEP, "ERROR mock weather.json key order does not match the observed block")
        errors.append("keys")
    if errors:
        log(STEP, "ERROR validation failed; data/out/weather.json left untouched")
        sys.exit(1)


def publish(doc):
    dest = OUT / "weather.json"
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, dest)
    log(STEP, f"wrote {dest}")


def report(doc, filled_days, raw_meteo):
    observed, forecast = doc["observed"], doc["forecast"]
    dates = observed["dates"]
    log(STEP, f"observed {dates[0]} .. {dates[-1]} ({len(dates)} days)")
    log(STEP, f"nulls precip={nulls(observed['precip_mm'])} tmax={nulls(observed['tmax_c'])} tmin={nulls(observed['tmin_c'])}")
    log(STEP, f"days filled from Open-Meteo: {filled_days}")
    last7 = observed["precip_mm"][-7:]
    log(STEP, f"season rain {rain_total(observed['precip_mm'])} mm; last 7 observed days {rain_total(last7)} mm")
    log(STEP, f"forecast {forecast['dates'][0]} .. {forecast['dates'][-1]}")
    log(STEP, f"next 3 days precip_mm {forecast['precip_mm'][:3]} precip_prob {forecast['precip_prob'][:3]}")
    region = load_region()
    as_of = parse_day(region["as_of"])
    best, unit = gust_from_raw(raw_meteo, as_of)
    shown = "none" if best is None else f"{best:g} {unit}"
    log(STEP, f"max hourly wind gust in the next 72 h: {shown}")
    trigger = any(value is not None and value >= 15 for value in forecast["precip_mm"][:3])
    print(f"RAIN TRIGGER (any day >= 15 mm in next 3): {'YES' if trigger else 'NO'}", flush=True)
    try:
        frost_c = float(region["thresholds"]["frost_tmin_c"])
        lookahead = int(region["thresholds"]["rain_lookahead_days"])
    except (KeyError, TypeError, ValueError) as exc:
        log(STEP, f"ERROR region thresholds missing frost_tmin_c or rain_lookahead_days: {exc}")
        sys.exit(1)
    lows = forecast["tmin_c"][:lookahead]
    frost = any(value is not None and value <= frost_c for value in lows)
    print(
        f"FROST TRIGGER (any tmin <= {frost_c:g} C in next {lookahead}): {'YES' if frost else 'NO'} {lows}",
        flush=True,
    )
    gusts = [value for value in forecast["wind_gust_ms"][:3] if value is not None]
    humidity = [value for value in forecast["rh_mean_pct"][:3] if value is not None]
    gust_text = "none" if not gusts else f"{max(gusts):g}"
    rh_text = "none" if not humidity else f"{min(humidity):g}"
    log(STEP, f"next 3 days max wind_gust_ms {gust_text}; min rh_mean_pct {rh_text}")


def load_previous_runs(lat, lon, start, end):
    path = CACHE / f"openmeteo_prevruns_{start.isoformat()}_{end.isoformat()}.json"
    if path.exists():
        log(STEP, f"reused {path.name}")
        return json.loads(path.read_text())
    names = [f"{base}_previous_day{lead}" for base in ARCHIVE_BASES for lead in range(8)]
    raw = fetch_json(
        PREV_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "timezone": "America/Chicago",
            "wind_speed_unit": "ms",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(names),
        },
        timeout=90,
    )
    hourly = raw.get("hourly") if isinstance(raw, dict) else None
    if not isinstance(hourly, dict) or "time" not in hourly or "precipitation" not in hourly:
        log(STEP, "ERROR previous-runs response has no hourly precipitation for this window")
        if isinstance(raw, dict):
            log(STEP, f"top-level keys {list(raw)[:12]}")
        sys.exit(1)
    write_json(path, raw)
    log(STEP, f"cached {path}")
    return raw


def _hours_on(times, day):
    text = iso(day)
    return [index for index, stamp in enumerate(times) if str(stamp)[:10] == text]


def _reduce(values, indexes, how):
    if not values:
        return None
    numbers = []
    for index in indexes:
        if index >= len(values):
            continue
        number = as_number(values[index])
        if number is not None:
            numbers.append(number)
    if not numbers:
        return None
    if how == "sum":
        return round(sum(numbers), 1)
    if how == "min":
        return round(min(numbers), 1)
    if how == "max":
        return round(max(numbers), 1)
    if how == "mean":
        return round(sum(numbers) / len(numbers), 1)
    return int(round(max(numbers)))


def issued_day(raw, issue, target):
    """Forecast for target that was already published before 6 am Central on issue.

    `_previous_dayN` is the run made 24*N hours before that hour. Lead 0, the unsuffixed
    series, is the current run, and for a past date that is close to what actually fell.
    Reading archive lead (lead + 1) puts every hour on a run from the day before issue,
    including the evening hours, so none of it was published on the morning itself.
    The archive keeps only 7 days, so a target more than 6 days after issue is null.
    """
    empty = {
        "precip_mm": None,
        "precip_prob": None,
        "tmin_c": None,
        "tmax_c": None,
        "rh_mean_pct": None,
        "wind_gust_ms": None,
    }
    lead = (target - issue).days
    if lead < 0 or lead > 6:
        return empty
    series_lead = lead + 1
    hourly = raw["hourly"]
    indexes = _hours_on(hourly["time"], target)
    if not indexes:
        return empty
    return {
        "precip_mm": _reduce(hourly.get(f"precipitation_previous_day{series_lead}"), indexes, "sum"),
        "precip_prob": _reduce(hourly.get(f"precipitation_probability_previous_day{series_lead}"), indexes, "prob"),
        "tmin_c": _reduce(hourly.get(f"temperature_2m_previous_day{series_lead}"), indexes, "min"),
        "tmax_c": _reduce(hourly.get(f"temperature_2m_previous_day{series_lead}"), indexes, "max"),
        "rh_mean_pct": _reduce(hourly.get(f"relative_humidity_2m_previous_day{series_lead}"), indexes, "mean"),
        "wind_gust_ms": _reduce(hourly.get(f"wind_gusts_10m_previous_day{series_lead}"), indexes, "max"),
    }


def archive_source(as_of):
    """Name the run that was published before 6 am Central on as_of, which is the previous day's run."""
    issued = as_of - timedelta(days=1)
    return (
        f"Open-Meteo previous-runs archive: forecast issued {issued.isoformat()}, "
        f"published before 6 am Central on {as_of.isoformat()}"
    )


def build_replay_weather(region, power, archive):
    """Observed days from NASA POWER only. Forecast is the run published before that morning; days after as_of+6 are null."""
    fill, parameter = require_power(power)
    season_start = parse_day(region["season_start"])
    as_of = parse_day(region["as_of"])
    observed_days = days_inclusive(season_start, as_of - timedelta(days=1))
    if not observed_days:
        log(STEP, "ERROR observed range is empty")
        sys.exit(1)
    precip, tmax, tmin = [], [], []
    for day in observed_days:
        key = ymd(day)
        precip.append(clean_power(parameter["PRECTOTCORR"].get(key), fill))
        tmax.append(clean_power(parameter["T2M_MAX"].get(key), fill))
        tmin.append(clean_power(parameter["T2M_MIN"].get(key), fill))
    horizon = int(region["forecast_horizon_days"])
    forecast_days = [as_of + timedelta(days=offset) for offset in range(horizon)]
    rows = [issued_day(archive, as_of, day) for day in forecast_days]
    return {
        "observed": {
            "source": "NASA POWER daily (community AG)",
            "dates": [iso(day) for day in observed_days],
            "precip_mm": precip,
            "tmax_c": tmax,
            "tmin_c": tmin,
        },
        "forecast": {
            "source": archive_source(as_of),
            "dates": [iso(day) for day in forecast_days],
            "precip_mm": [row["precip_mm"] for row in rows],
            "precip_prob": [row["precip_prob"] for row in rows],
            "tmin_c": [row["tmin_c"] for row in rows],
            "tmax_c": [row["tmax_c"] for row in rows],
            "rh_mean_pct": [row["rh_mean_pct"] for row in rows],
            "wind_gust_ms": [row["wind_gust_ms"] for row in rows],
        },
    }


def publish_replay(region, lat, lon):
    as_of = parse_day(region["as_of"])
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    if as_of >= today:
        log(STEP, f"ERROR replay as_of {iso(as_of)} is not before today {iso(today)} (America/Chicago)")
        sys.exit(1)
    power_path = CACHE / "power_raw.json"
    if not power_path.exists():
        log(STEP, f"ERROR missing cache {power_path}")
        sys.exit(1)
    power = json.loads(power_path.read_text())
    horizon = int(region["forecast_horizon_days"])
    archive = load_previous_runs(lat, lon, as_of, as_of + timedelta(days=horizon - 1))
    doc = build_replay_weather(region, power, archive)
    validate(doc)
    publish(doc)
    forecast = doc["forecast"]
    log(STEP, f"replay forecast {forecast['source']}")
    log(STEP, f"next 3 days precip_mm {forecast['precip_mm'][:3]}")
    log(STEP, "no Open-Meteo gap fill; observed is NASA POWER through the day before as_of")


def load_cached():
    power_path = CACHE / "power_raw.json"
    meteo_path = CACHE / "openmeteo_raw.json"
    missing = [path for path in (power_path, meteo_path) if not path.exists()]
    if missing:
        for path in missing:
            log(STEP, f"ERROR missing cache {path}")
        sys.exit(1)
    return (
        json.loads(power_path.read_text()),
        json.loads(meteo_path.read_text()),
    )


def main():
    parser = argparse.ArgumentParser(description="Write observed weather and the rain forecast")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    region = load_region()
    lon, lat = centre(region["bbox"])
    log(STEP, f"centre lon={lon:.5f} lat={lat:.5f}")
    if args.replay:
        log(STEP, "replay: forecast as issued that morning, no gap fill")
        publish_replay(region, lat, lon)
        return
    if args.from_cache:
        log(STEP, "rebuilding from cache, no network")
        power, meteo = load_cached()
    else:
        season_start = parse_day(region["season_start"])
        as_of = parse_day(region["as_of"])
        power = fetch_json(
            POWER_URL,
            {
                "parameters": "PRECTOTCORR,T2M_MAX,T2M_MIN",
                "community": "AG",
                "longitude": lon,
                "latitude": lat,
                "start": ymd(season_start),
                "end": ymd(as_of - timedelta(days=1)),
                "format": "JSON",
            },
            timeout=90,
        )
        meteo = fetch_json(
            METEO_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "timezone": "auto",
                "past_days": 14,
                "forecast_days": 16,
                "daily": "precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,wind_gusts_10m_max",
                "hourly": "precipitation,wind_gusts_10m,weather_code",
                "wind_speed_unit": "ms",
            },
            timeout=30,
        )
        write_json(CACHE / "power_raw.json", power)
        write_json(CACHE / "openmeteo_raw.json", meteo)
        log(STEP, f"cached {CACHE / 'power_raw.json'} and {CACHE / 'openmeteo_raw.json'}")

    doc, filled_days, _hourly = build_weather(region, power, meteo, from_cache=args.from_cache)
    validate(doc)
    publish(doc)
    report(doc, filled_days, meteo)


if __name__ == "__main__":
    main()
