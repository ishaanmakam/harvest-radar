"""Frost as a second trigger of the harvest-versus-weather check (kind stays harvest_before_rain, so the plan keeps
exactly eight rows): act_now when the crop looks ready and a forecast day in the next 3 days has Tmin at or below
frost_tmin_c. Only active when the forecast carries temperatures; the rain rule is unchanged."""
import json
from datetime import date
from pathlib import Path

import decide
from test_decide import QUIET_PASSES, check, rec, status_of

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 17)
D = date.fromisoformat
READY = dict(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")


def fc(*days):
    """[(date, precip_mm, tmin_c)] forecast entries."""
    return [(D(d), mm, tmin) for d, mm, tmin in days]


class TestFrostTrigger:
    def test_frost_within_3_days_on_a_ready_crop_is_act_now(self):
        forecast = fc(("2026-10-17", 0.0, 6.0), ("2026-10-18", 0.0, 1.5), ("2026-10-19", 0.0, -1.0), ("2026-10-20", 0.0, 4.0))
        plan = decide.farm_plan(rec(**READY), QUIET_PASSES, forecast, [], None, TODAY)
        st = status_of(plan)
        assert st["harvest_before_rain"] == "act_now"
        c = check(plan, "harvest_before_rain")
        assert "frost" in c["detail"].lower() and "2026-10-19" in c["detail"]
        assert c["window"] == {"start": "2026-10-19", "end": "2026-10-19"}
        assert "frost" in plan["today"].lower()

    def test_frost_beyond_3_days_does_not_count(self):
        forecast = fc(("2026-10-17", 0.0, 6.0), ("2026-10-18", 0.0, 5.0), ("2026-10-19", 0.0, 4.0), ("2026-10-20", 0.0, -2.0))
        assert status_of(decide.farm_plan(rec(**READY), QUIET_PASSES, forecast, [], None, TODAY))["harvest_before_rain"] == "clear"

    def test_frost_on_a_crop_that_is_not_ready_is_clear_but_mentioned(self):
        forecast = fc(("2026-10-17", 0.0, 6.0), ("2026-10-18", 0.0, -1.0))
        plan = decide.farm_plan(rec(), QUIET_PASSES, forecast, [], None, TODAY)
        assert status_of(plan)["harvest_before_rain"] == "clear"
        assert "frost" in check(plan, "harvest_before_rain")["detail"].lower()

    def test_rain_and_frost_together_name_both(self):
        forecast = fc(("2026-10-17", 0.0, 6.0), ("2026-10-18", 20.0, 3.0), ("2026-10-19", 0.0, -1.0))
        c = check(decide.farm_plan(rec(**READY), QUIET_PASSES, forecast, [], None, TODAY), "harvest_before_rain")
        assert c["status"] == "act_now" and "rain" in c["detail"].lower() and "frost" in c["detail"].lower()
        assert c["window"] == {"start": "2026-10-18", "end": "2026-10-19"}

    def test_two_tuple_forecasts_without_temperature_still_work(self):
        forecast = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        assert status_of(decide.farm_plan(rec(**READY), QUIET_PASSES, forecast, [], None, TODAY))["harvest_before_rain"] == "act_now"


class TestForecastPairsWithTemperature:
    def test_tmin_is_carried_when_the_file_has_it(self):
        out = decide.forecast_pairs(["2026-10-17", "2026-10-18"], [0.0, 1.0], [5.0, -1.5])
        assert out == [(D("2026-10-17"), 0.0, 5.0), (D("2026-10-18"), 1.0, -1.5)]

    def test_without_tmin_entries_stay_two_tuples(self):
        assert decide.forecast_pairs(["2026-10-17"], [0.0]) == [(D("2026-10-17"), 0.0)]

    def test_run_reads_forecast_tmin_c_from_weather_json(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "timeseries.json", "fires.json", "fields.geojson", "insights.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        w = json.loads((data / "weather.json").read_text())
        w["forecast"]["precip_mm"] = [0.0] * len(w["forecast"]["dates"])          # no rain at all
        w["forecast"]["tmin_c"] = [6.0, -2.0] + [8.0] * (len(w["forecast"]["dates"]) - 2)   # frost tomorrow
        (tmp_path / "weather.json").write_text(json.dumps(w))
        plans, _ = decide.run(tmp_path)
        ready = [p for p in plans["plans"] if any(c["kind"] == "harvest_ready" and c["status"] == "watch" for c in p["checks"])]
        assert ready and all(status_of(p)["harvest_before_rain"] == "act_now" for p in ready)
        assert "frost" in check(ready[0], "harvest_before_rain")["detail"].lower()
