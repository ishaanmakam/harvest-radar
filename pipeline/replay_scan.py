"""Find harvest mornings where yesterday's forecast warned of rain that then fell.

The forecast is Open-Meteo's previous-runs archive, read one lead later than the
target day so every value was published before 6 am Central that morning
(see s06_weather.issued_day). Actual rain is NASA POWER in data/out/weather.json.

A morning M is a USEFUL WARNING when the forecast put at least 15 mm on any of
M+1, M+2, or M+3, the same window then got at least 15 mm, and M itself was dry
(under 5 mm), so the field was workable that morning.
"""
import json
import sys
from datetime import date, timedelta

from common import CACHE, ROOT, load_region, write_json
from s06_weather import centre, issued_day, load_previous_runs

WARN_MM = 15.0
DRY_MM = 5.0
START = date(2026, 9, 10)
END = date(2026, 10, 12)


def days(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def shown(value):
    return "none" if value is None else f"{value:.1f}"


def main():
    region = load_region()
    lon, lat = centre(region["bbox"])
    archive_end = END + timedelta(days=3)
    archive = load_previous_runs(lat, lon, START, archive_end)
    hourly = archive.get("hourly") or {}
    day7 = hourly.get("precipitation_previous_day7")
    day7_values = 0 if not day7 else sum(value is not None for value in day7)
    if day7_values == 0:
        print("ERROR archive has no precipitation_previous_day7 values in this window")
        print(f"hourly keys include previous_day7: {'precipitation_previous_day7' in hourly}")
        sys.exit(1)
    print(f"precipitation_previous_day7 non-null hours: {day7_values}")
    weather = json.loads((ROOT / "data" / "out" / "weather.json").read_text())
    actual = dict(zip(weather["observed"]["dates"], weather["observed"]["precip_mm"]))
    summary = json.loads((CACHE / "s01_summary.json").read_text())
    clear_dates = [date.fromisoformat(text) for text in summary["likely_clear_dates"]]
    rows = []
    header = (
        f"{'morning':<12} {'act0':>6} {'fc+1':>6} {'fc+2':>6} {'fc+3':>6} "
        f"{'act+1':>6} {'act+2':>6} {'act+3':>6}  {'clear_pass':<12} {'ago':>4}  note"
    )
    print(header)
    for morning in days(START, END):
        ahead = [morning + timedelta(days=offset) for offset in (1, 2, 3)]
        forecast = [issued_day(archive, morning, day)["precip_mm"] for day in ahead]
        observed = [actual.get(day.isoformat()) for day in ahead]
        day0 = actual.get(morning.isoformat())
        prior = [day for day in clear_dates if day <= morning]
        if prior:
            clear_pass = prior[-1]
            days_since = (morning - clear_pass).days
        else:
            clear_pass = None
            days_since = None
        warned = any(value is not None and value >= WARN_MM for value in forecast)
        hit = any(value is not None and value >= WARN_MM for value in observed)
        dry = day0 is not None and day0 < DRY_MM
        useful = warned and hit and dry
        note = "USEFUL WARNING" if useful else ""
        row = {
            "date": morning.isoformat(),
            "actual_day0_mm": day0,
            "forecast_mm": forecast,
            "actual_mm": observed,
            "latest_clear_pass": None if clear_pass is None else clear_pass.isoformat(),
            "days_since_clear": days_since,
            "useful_warning": useful,
        }
        rows.append(row)
        ago = "none" if days_since is None else str(days_since)
        clear_text = "none" if clear_pass is None else clear_pass.isoformat()
        print(
            f"{row['date']:<12} {shown(day0):>6} {shown(forecast[0]):>6} {shown(forecast[1]):>6} "
            f"{shown(forecast[2]):>6} {shown(observed[0]):>6} {shown(observed[1]):>6} "
            f"{shown(observed[2]):>6}  {clear_text:<12} {ago:>4}  {note}"
        )
    useful_rows = [row for row in rows if row["useful_warning"]]
    print(f"useful warnings: {len(useful_rows)}")
    for row in useful_rows:
        print(
            f"  {row['date']} day0 {row['actual_day0_mm']} forecast {row['forecast_mm']} "
            f"actual {row['actual_mm']} clear {row['latest_clear_pass']} "
            f"({row['days_since_clear']} d)"
        )
    if len(useful_rows) >= 3:
        candidates = [row["date"] for row in useful_rows[-3:]]
    else:
        candidates = [row["date"] for row in useful_rows]
    print(f"candidates (latest 3): {candidates}")
    payload = {
        "start": START.isoformat(),
        "end": END.isoformat(),
        "warn_mm": WARN_MM,
        "dry_mm": DRY_MM,
        "rows": rows,
        "useful_warnings": [row["date"] for row in useful_rows],
        "candidates": candidates,
    }
    path = CACHE / "replay_scan.json"
    write_json(path, payload, pretty=True)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
