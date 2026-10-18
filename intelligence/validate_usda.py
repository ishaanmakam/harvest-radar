"""Validation figure: our satellite percent-harvested curve for the AOI against USDA NASS Crop Progress.

USDA's weekly Crop Progress survey reports the percent of corn and soybeans harvested per crop-reporting district;
for an Iowa landscape the Central district is the published reference closest to a ground truth. This module
derives the same statistic from status.json (share of fields, and of area, with a harvest_date on or before each
date), fetches the USDA series through the NASS QuickStats API, aligns the two by week-ending Sunday and scores
the gap.

    python intelligence/validate_usda.py --data data/mock               # offline: uses the cached USDA rows
    python intelligence/validate_usda.py --data data/out --refresh      # re-fetch (NASS_API_KEY in .env)
    python intelligence/validate_usda.py --data data/mock --sample      # seed the cache from the built-in sample

Inputs:  status.json (harvest_date per field), fields.geojson (area_ha, crop), timeseries.json (observation dates)
Outputs: <data>/usda_crop_progress.json  the fetched USDA rows (cache; reruns and tests need no network)
         <data>/validation_usda.json     both curves aligned by week-ending date + metrics

Metrics per commodity
  mean_abs_diff_pp   mean |satellite - USDA| in percentage points over the weeks both curves cover
  lag_days_50pct     satellite 50%-crossing date minus USDA's (negative = the satellite sees harvest earlier)

QuickStats (https://quickstats.nass.usda.gov/api): free key, GET api_GET/ with the column descriptors as
parameters, JSON back as {"data": [record, ...]} with the value in "Value" and the Sunday in "week_ending".
The requested year falls back to the latest year with data (district rows arrive a few days after the week).
Never depends on the network: the cache is read first, the fake/sample transport answers tests and demos.
"""
import argparse
import json
import os
import statistics
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from common import d, load_env, read_json, s, write_json

NASS_API_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
NASS_SIGNUP_URL = "https://quickstats.nass.usda.gov/api/signup"
CACHE_NAME = "usda_crop_progress.json"
OUT_NAME = "validation_usda.json"
COMMODITIES = ("CORN", "SOYBEANS")
STATE, DISTRICT = "IA", "CENTRAL"
FALLBACK_YEARS = 5           # how far back to look when the requested year has no district rows yet
NO_SIGNAL = ("unknown",)     # stages that mean "no crop seen here": not in the denominator


class NassError(RuntimeError):
    """QuickStats could not be queried, or had nothing for the request."""


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


# ----------------------------------------------------------------------------- 1. satellite curve
def _cropped(status, fields, crop=None):
    """(record, area_ha) for the fields that count: a crop signal (stage not unknown), and the crop filter when
    given (matched against fields.geojson properties.crop). Fields missing from the geojson count with 0 ha."""
    meta = {f["properties"]["field_id"]: f["properties"] for f in fields.get("features", [])}
    out = []
    for rec in status["fields"]:
        props = meta.get(rec["field_id"], {})
        if crop and (props.get("crop") or "").strip().lower() != crop.strip().lower():
            continue
        out.append((rec, float(props.get("area_ha") or 0.0)))
    return out


def population(status, fields, crop=None):
    """Who is in the denominator, for the report."""
    all_recs = _cropped(status, fields, crop)
    cropped = [(r, a) for r, a in all_recs if r["stage"] not in NO_SIGNAL]
    return {"crop_filter": crop, "n_fields": len(all_recs), "n_cropped": len(cropped),
            "n_excluded_unknown": len(all_recs) - len(cropped), "area_ha": round(sum(a for _, a in cropped), 1)}


def harvest_share(cropped, at: date):
    """(pct of fields, pct of area) with harvest_date on or before `at`, over the cropped fields given."""
    cropped = [(r, a) for r, a in cropped if r["stage"] not in NO_SIGNAL]
    done = [(r, a) for r, a in cropped if r.get("harvest_date") and d(r["harvest_date"]) <= at]
    return _pct(len(done), len(cropped)), _pct(sum(a for _, a in done), sum(a for _, a in cropped))


def satellite_curve(status, fields, dates, crop=None):
    """Percent harvested at each observation date (step function of the per-field harvest dates)."""
    cropped = _cropped(status, fields, crop)
    out = []
    for dt in dates:
        pf, pa = harvest_share(cropped, d(dt))
        out.append({"date": dt, "pct_fields": pf, "pct_area": pa})
    return out


def week_endings(first: date, last: date):
    """Every Sunday in [first, last]: USDA's week_ending dates."""
    sunday = first + timedelta(days=(6 - first.weekday()) % 7)
    out = []
    while sunday <= last:
        out.append(sunday)
        sunday += timedelta(days=7)
    return out


def weekly_curve(status, fields, dates, crop=None):
    """The satellite curve sampled on the Sundays inside the observed window, so it joins USDA's rows directly."""
    cropped = _cropped(status, fields, crop)
    out = []
    for sunday in week_endings(d(dates[0]), d(dates[-1])):
        pf, pa = harvest_share(cropped, sunday)
        out.append({"week_ending": s(sunday), "pct_fields": pf, "pct_area": pa})
    return out


# ----------------------------------------------------------------------------- 2. QuickStats: query, parse, fetch
def quickstats_params(commodity, year, key, state=STATE, district=DISTRICT, year_op=None):
    """QuickStats filters for weekly PCT HARVESTED at agricultural-district level. `year_op` ("GE", "LE", ...)
    turns the year into a range filter (QuickStats' year__GE=... syntax)."""
    p = {"key": key, "source_desc": "SURVEY", "sector_desc": "CROPS", "commodity_desc": commodity,
         "statisticcat_desc": "PROGRESS", "unit_desc": "PCT HARVESTED", "agg_level_desc": "AGRICULTURAL DISTRICT",
         "state_alpha": state, "asd_desc": district, "format": "JSON"}
    p["year" if not year_op else f"year__{year_op}"] = str(year)
    return p


def quickstats_url(params):
    return NASS_API_URL + "?" + urlencode(params)


def _number(value):
    """QuickStats values are strings; '(D)', '(NA)', '(Z)' and blanks mean no number."""
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_progress(payload, district=DISTRICT):
    """QuickStats JSON -> [{"week_ending", "year", "pct_harvested"}] sorted by week, one row per week, only the
    district asked for (a LIKE query or a loose district name would otherwise pull NORTH CENTRAL etc. in too)."""
    rows = {}
    for rec in (payload or {}).get("data") or []:
        if district and (rec.get("asd_desc") or "").strip().upper() != district.strip().upper():
            continue
        value, week = _number(rec.get("Value")), (rec.get("week_ending") or "").strip()
        if value is None or not week or week in rows:
            continue
        rows[week] = {"week_ending": week, "year": int(rec.get("year") or week[:4]), "pct_harvested": value}
    return [rows[k] for k in sorted(rows)]


def _urllib_http(method, url, headers, body, timeout):
    """(status, body bytes); HTTP errors come back as a status, transport errors raise NassError."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise NassError(f"QuickStats unreachable: {getattr(e, 'reason', e)}")


class NassClient:
    """Fetches one commodity's weekly PCT HARVESTED rows. `http(method, url, headers, body, timeout)` returns
    (status, bytes); tests pass a fake, the sample transport answers offline."""

    def __init__(self, api_key, http=None, timeout=60):
        self.api_key, self.http, self.timeout = api_key, http or _urllib_http, timeout
        self.source = getattr(self.http, "source", "nass_quickstats")

    def _get(self, params):
        status, body = self.http("GET", quickstats_url(params), {"Accept": "application/json"}, None, self.timeout)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {"error": [(body or b"")[:200].decode(errors="replace")]}
        if not 200 <= status < 300:
            err = payload.get("error") if isinstance(payload, dict) else None
            text = " ".join(map(str, err)) if isinstance(err, list) else str(err or f"HTTP {status}")
            if "no data" in text.lower():          # QuickStats answers an empty match with a 400
                return {"data": []}
            raise NassError(f"QuickStats {params.get('commodity_desc')} {params.get('year', params.get('year__GE'))}: "
                            f"{text} (HTTP {status})")
        return payload

    def fetch(self, commodity, year, state=STATE, district=DISTRICT):
        """Rows for `year`, else the latest year in the FALLBACK_YEARS before it that has any."""
        params = quickstats_params(commodity, year, self.api_key, state, district)
        rows = parse_progress(self._get(params), district)
        if not rows:
            params = quickstats_params(commodity, year - FALLBACK_YEARS, self.api_key, state, district, year_op="GE")
            recent = parse_progress(self._get(params), district)
            if recent:
                latest = max(r["year"] for r in recent)
                rows = [r for r in recent if r["year"] == latest]
        if not rows:
            raise NassError(f"no PCT HARVESTED rows for {commodity} in {state} {district} for {year} "
                            f"or the {FALLBACK_YEARS} years before it")
        query = {k: v for k, v in params.items() if k != "key"}
        return {"commodity": commodity, "requested_year": year, "year": rows[0]["year"], "rows": rows, "query": query}


# ----------------------------------------------------------------------------- 2b. offline sample transport
# Stand-in weekly values shaped like a QuickStats answer for IA CENTRAL. They are NOT USDA figures: they let the
# mock pipeline, demos and tests run with no key and no network. Real rows come from --refresh with NASS_API_KEY.
SAMPLE_ROWS = {
    "CORN": [("2025-09-21", "2"), ("2025-09-28", "5"), ("2025-10-05", "13"), ("2025-10-12", "31"),
             ("2025-10-19", "52"), ("2025-10-26", "71"), ("2025-11-02", "86"), ("2025-11-09", "94")],
    "SOYBEANS": [("2025-09-21", "3"), ("2025-09-28", "11"), ("2025-10-05", "36"), ("2025-10-12", "66"),
                 ("2025-10-19", "86"), ("2025-10-26", "94"), ("2025-11-02", "98")],
}


def sample_payload(commodity, state=STATE, district=DISTRICT):
    """A QuickStats-shaped payload (the columns the API returns) built from SAMPLE_ROWS."""
    first_sunday = _first_sunday(2025)
    data = []
    for week, value in SAMPLE_ROWS.get(commodity.upper(), []):
        week_no = (d(week) - first_sunday).days // 7 + 1
        data.append({"source_desc": "SURVEY", "sector_desc": "CROPS", "group_desc": "FIELD CROPS",
                     "commodity_desc": commodity.upper(), "class_desc": "ALL CLASSES",
                     "prodn_practice_desc": "ALL PRODUCTION PRACTICES", "util_practice_desc": "ALL UTILIZATION PRACTICES",
                     "statisticcat_desc": "PROGRESS", "unit_desc": "PCT HARVESTED",
                     "short_desc": f"{commodity.upper()} - PROGRESS, MEASURED IN PCT HARVESTED",
                     "domain_desc": "TOTAL", "domaincat_desc": "NOT SPECIFIED", "agg_level_desc": "AGRICULTURAL DISTRICT",
                     "state_alpha": state, "state_name": "IOWA" if state == "IA" else state, "asd_code": "50",
                     "asd_desc": district, "location_desc": f"{'IOWA' if state == 'IA' else state}, {district}",
                     "year": d(week).year, "freq_desc": "WEEKLY", "begin_code": f"{week_no:02d}", "end_code": f"{week_no:02d}",
                     "reference_period_desc": f"WEEK #{week_no}", "week_ending": week, "load_time": "", "Value": value, "CV (%)": ""})
    return {"data": data}


class SampleHttp:
    """Transport that answers QuickStats queries from SAMPLE_ROWS. An exact-year query for a year other than the
    sample's gets no rows, so the year fallback is exercised the way it is with the live API."""
    source = "sample"

    def __call__(self, method, url, headers, body, timeout):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if q.get("year") and q["year"] != "2025":
            return 200, json.dumps({"data": []}).encode()
        return 200, json.dumps(sample_payload(q.get("commodity_desc", "CORN"), q.get("state_alpha", STATE),
                                              q.get("asd_desc", DISTRICT))).encode()


sample_http = SampleHttp()


# ----------------------------------------------------------------------------- 2c. cache
def load_or_fetch(data_dir, year, client=None, refresh=False, commodities=COMMODITIES, state=STATE, district=DISTRICT):
    """The USDA rows: from <data>/usda_crop_progress.json when it covers this state, district and every commodity
    (and --refresh was not asked), otherwise fetched through `client` and written there. The key is never stored."""
    path = Path(data_dir) / CACHE_NAME
    if not refresh and path.exists():
        doc = json.loads(path.read_text())
        series = doc.get("series") or {}
        if doc.get("state") == state and doc.get("district") == district and all(c in series for c in commodities):
            return doc
    if client is None:
        raise NassError(f"{path} is missing or does not cover {state} {district} {'/'.join(commodities)}, and there is "
                        f"no NASS client: put NASS_API_KEY in .env (free key: {NASS_SIGNUP_URL}) or run with --sample")
    doc = {"source": client.source, "api": NASS_API_URL,
           "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "state": state, "district": district, "requested_year": year,
           "series": {c: client.fetch(c, year, state, district) for c in commodities}}
    write_json(data_dir, CACHE_NAME, doc)
    return doc


# ----------------------------------------------------------------------------- 3. alignment + metrics
def _first_sunday(year):
    jan1 = date(year, 1, 1)
    return jan1 + timedelta(days=(6 - jan1.weekday()) % 7)


def season_week_ending(week_ending: date, season_year: int) -> date:
    """USDA week N ends on the N-th Sunday of its year; a row from a fallback year lands on the same week number's
    Sunday in the season year, so both curves share one calendar."""
    if week_ending.year == season_year:
        return week_ending
    weeks = (week_ending - _first_sunday(week_ending.year)).days // 7
    return _first_sunday(season_year) + timedelta(weeks=weeks)


def align(status, fields, usda_rows, season_year, first: date, last: date, crop=None):
    """One row per USDA week that falls inside the satellite's observed window [first, last], with the satellite
    share evaluated on that same week-ending date."""
    cropped = _cropped(status, fields, crop)
    out = []
    for r in usda_rows:
        we = season_week_ending(d(r["week_ending"]), season_year)
        if not first <= we <= last:
            continue
        pf, pa = harvest_share(cropped, we)
        out.append({"week_ending": s(we), "usda_week_ending": r["week_ending"], "usda_pct": r["pct_harvested"],
                    "satellite_pct_fields": pf, "satellite_pct_area": pa})
    return sorted(out, key=lambda r: r["week_ending"])


def crossing_date(points, level=50.0):
    """First date a piecewise-linear curve through [(date, pct), ...] reaches `level`; None if it never does."""
    prev = None
    for dt, v in sorted(points):
        if v is not None and v >= level:
            if prev is None or v == prev[1]:
                return dt
            frac = (level - prev[1]) / (v - prev[1])
            return prev[0] + timedelta(days=round(frac * (dt - prev[0]).days))
        if v is not None:
            prev = (dt, v)
    return None


def _mean(xs):
    return round(statistics.mean(xs), 1) if xs else None


def metrics(aligned, sat_curve, usda_season_rows, level=50.0):
    """aligned: rows from align(); sat_curve: the satellite curve on its observation dates (every clear pass, so
    the crossing is placed with all the dates status.json knows, not only Sundays); usda_season_rows: USDA rows
    with week_ending already in the season calendar. The 50% crossings use the whole curves, not just the overlap."""
    diff_f = [r["satellite_pct_fields"] - r["usda_pct"] for r in aligned if r["satellite_pct_fields"] is not None]
    diff_a = [r["satellite_pct_area"] - r["usda_pct"] for r in aligned if r["satellite_pct_area"] is not None]
    sat = crossing_date([(d(p["date"]), p["pct_fields"]) for p in sat_curve], level)
    usda = crossing_date([(d(r["week_ending"]), r["pct_harvested"]) for r in usda_season_rows], level)
    return {"weeks_compared": len(aligned),
            "mean_abs_diff_pp_fields": _mean([abs(x) for x in diff_f]),
            "mean_abs_diff_pp_area": _mean([abs(x) for x in diff_a]),
            "mean_diff_pp_fields": _mean(diff_f),
            "satellite_50pct_date": s(sat) if sat else None,
            "usda_50pct_date": s(usda) if usda else None,
            "lag_days_50pct": (sat - usda).days if sat and usda else None}


# ----------------------------------------------------------------------------- 4. run + report
def run(data_dir, year=None, client=None, refresh=False, crop=None, commodities=COMMODITIES, state=STATE, district=DISTRICT):
    data_dir = Path(data_dir)
    status = read_json(data_dir, "status.json")
    fields = read_json(data_dir, "fields.geojson")
    dates = read_json(data_dir, "timeseries.json")["dates"]
    first, last = d(dates[0]), d(dates[-1])
    season_year = year or last.year

    usda = load_or_fetch(data_dir, season_year, client, refresh, commodities, state, district)
    curve = satellite_curve(status, fields, dates, crop)
    weekly = weekly_curve(status, fields, dates, crop)
    aligned, mets, series_out = {}, {}, {}
    for c in commodities:
        series = usda["series"][c]
        season_rows = [{"week_ending": s(season_week_ending(d(r["week_ending"]), season_year)),
                        "usda_week_ending": r["week_ending"], "pct_harvested": r["pct_harvested"]} for r in series["rows"]]
        aligned[c] = align(status, fields, series["rows"], season_year, first, last, crop)
        mets[c] = {"usda_year": series["year"], **metrics(aligned[c], curve, season_rows)}
        series_out[c] = {"year": series["year"], "requested_year": series["requested_year"], "rows": season_rows}

    doc = {"as_of": status["as_of"], "season_year": season_year, "window": {"first": dates[0], "last": dates[-1]},
           "region": {"state": state, "district": district, "label": f"{state} {district} agricultural district"},
           "satellite": {**population(status, fields, crop), "curve": curve, "weekly": weekly},
           "usda": {"source": usda["source"], "fetched_at": usda["fetched_at"], "series": series_out},
           "aligned": aligned, "metrics": mets}
    write_json(data_dir, OUT_NAME, doc)
    print(report(doc))
    return doc


def report(doc) -> str:
    sat, reg = doc["satellite"], doc["region"]
    last = sat["weekly"][-1] if sat["weekly"] else {"week_ending": doc["window"]["last"], "pct_fields": None, "pct_area": None}
    lines = [f"USDA validation: {reg['label']}, season {doc['season_year']} (as of {doc['as_of']})",
             f"  satellite: {sat['n_cropped']} cropped fields, {sat['area_ha']} ha ({sat['n_excluded_unknown']} without crop "
             f"signal excluded{', crop=' + sat['crop_filter'] if sat['crop_filter'] else ''}); "
             f"{last['pct_fields']}% of fields / {last['pct_area']}% of area harvested by {last['week_ending']}"]
    if doc["usda"]["source"] != "nass_quickstats":
        lines.append(f"  USDA rows are a '{doc['usda']['source']}' stand-in, not NASS figures: run with NASS_API_KEY and --refresh")
    for c, m in doc["metrics"].items():
        year = f"USDA {m['usda_year']}" + (f" (no {doc['season_year']} rows yet)" if m["usda_year"] != doc["season_year"] else "")
        if not m["weeks_compared"]:
            lines.append(f"  {c:<9} {year}: no overlapping weeks inside the satellite window")
            continue
        lag = m["lag_days_50pct"]
        lag_text = ("not reached by both" if lag is None else
                    "same day" if lag == 0 else f"satellite {abs(lag)} d {'earlier' if lag < 0 else 'later'}")
        lines.append(f"  {c:<9} {year}: {m['weeks_compared']} weeks compared, mean |diff| {m['mean_abs_diff_pp_fields']} pp "
                     f"(fields) / {m['mean_abs_diff_pp_area']} pp (area); 50% crossed satellite {m['satellite_50pct_date']} "
                     f"vs USDA {m['usda_50pct_date']} -> {lag_text}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Satellite percent-harvested curve vs USDA NASS Crop Progress (district level).")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--year", type=int, help="season year to ask USDA for (default: year of the last observation)")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again (needs NASS_API_KEY)")
    ap.add_argument("--sample", action="store_true", help="answer QuickStats from the built-in sample rows: offline, not USDA data")
    ap.add_argument("--crop", help="only fields whose fields.geojson crop matches (e.g. corn, soybeans)")
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--district", default=DISTRICT, help="agricultural district name as QuickStats spells it")
    args = ap.parse_args(argv)
    load_env()
    client = None
    if args.sample:
        client = NassClient("", http=sample_http)
    elif os.environ.get("NASS_API_KEY"):
        client = NassClient(os.environ["NASS_API_KEY"])
    try:
        run(Path(args.data), year=args.year, client=client, refresh=args.refresh or args.sample, crop=args.crop,
            state=args.state, district=args.district.upper())
    except NassError as e:
        print(f"ERROR {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
