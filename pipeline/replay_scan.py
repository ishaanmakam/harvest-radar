"""Compare the rain forecast issued each harvest morning with the rain that actually fell.

The forecast is Open-Meteo's previous-runs archive. The actual rain is NASA POWER in
data/out/weather.json. A morning is WARNED when any of the next three days was forecast
at 15 mm or more, and a true warning when that also happened.
"""
import json
from datetime import date, timedelta

from common import CACHE, ROOT, load_region, write_json
from s06_weather import centre, issued_day, load_previous_runs

WARN_MM = 15.0
START = date(2026, 9, 10)
END = date(2026, 10, 12)


def days(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def main():
    region = load_region()
    lon, lat = centre(region["bbox"])
    archive = load_previous_runs(lat, lon, START, END + timedelta(days=2))
    weather = json.loads((ROOT / "data" / "out" / "weather.json").read_text())
    actual = dict(zip(weather["observed"]["dates"], weather["observed"]["precip_mm"]))
    rows = []
    print(f"{'morning':<12} {'fc max':>8} {'fc sum':>8} {'act max':>8} {'act sum':>8}  note")
    for morning in days(START, END):
        targets = [morning + timedelta(days=offset) for offset in range(3)]
        forecast = []
        observed = []
        for target in targets:
            forecast.append(issued_day(archive, morning, target)["precip_mm"])
            observed.append(actual.get(target.isoformat()))
        fc_known = [value for value in forecast if value is not None]
        act_known = [value for value in observed if value is not None]
        fc_max = None if not fc_known else max(fc_known)
        act_max = None if not act_known else max(act_known)
        fc_sum = None if not fc_known else round(sum(fc_known), 1)
        act_sum = None if not act_known else round(sum(act_known), 1)
        warned = any(value is not None and value >= WARN_MM for value in forecast)
        hit = any(value is not None and value >= WARN_MM for value in observed)
        note = "WARNED" if warned else ""
        if warned and hit:
            note = "TRUE WARNING"
        row = {
            "date": morning.isoformat(),
            "forecast_mm": forecast,
            "actual_mm": observed,
            "forecast_max_mm": fc_max,
            "forecast_total_mm": fc_sum,
            "actual_max_mm": act_max,
            "actual_total_mm": act_sum,
            "warned": warned,
            "true_warning": warned and hit,
        }
        rows.append(row)
        def shown(value):
            return "none" if value is None else f"{value:.1f}"
        print(
            f"{row['date']:<12} {shown(fc_max):>8} {shown(fc_sum):>8} "
            f"{shown(act_max):>8} {shown(act_sum):>8}  {note}"
        )
    true = [row for row in rows if row["true_warning"]]
    print(f"true warnings: {len(true)}")
    for row in true:
        print(
            f"  {row['date']} forecast {row['forecast_mm']} actual {row['actual_mm']}"
        )
    sep24 = next(row for row in rows if row["date"] == "2026-09-24")
    if sep24["true_warning"]:
        print("2026-09-24 is a true warning")
    else:
        print("2026-09-24 is not a true warning")
    if true:
        best = max(true, key=lambda row: row["actual_total_mm"] or 0)
        print(
            f"strongest true warning by actual 3-day total: {best['date']} "
            f"forecast {best['forecast_mm']} actual {best['actual_mm']}"
        )
    payload = {"start": START.isoformat(), "end": END.isoformat(), "warn_mm": WARN_MM, "rows": rows}
    path = CACHE / "replay_scan.json"
    write_json(path, payload, pretty=True)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
