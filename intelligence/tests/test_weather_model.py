"""weather_model.py: a small, explainable harvest-window model on top of weather.json.
  growing degree days -> physiological maturity; grain dry-down -> combine-ready date; rain -> workable days;
  frost nights; then the best combine window inside the forecast. Synthetic weather only."""
from datetime import date, timedelta

import pytest

import weather_model as wm

D = date.fromisoformat
TODAY = D("2026-10-17")


def days(start, n):
    return [start + timedelta(days=i) for i in range(n)]


def observed(start, n, tmax=25.0, tmin=12.0, precip=0.0):
    return {"dates": [d.isoformat() for d in days(start, n)], "tmax_c": [tmax] * n, "tmin_c": [tmin] * n, "precip_mm": [precip] * n}


class TestDegreeDays:
    def test_corn_base_10_cap_30(self):
        assert wm.gdd_day(30, 10, crop="corn") == pytest.approx(10.0)      # (30+10)/2 - 10
        assert wm.gdd_day(35, 20, crop="corn") == pytest.approx(15.0)      # tmax capped at 30
        assert wm.gdd_day(12, 2, crop="corn") == pytest.approx(1.0)        # tmin floored at 10 -> (12+10)/2 - 10

    def test_cold_days_add_nothing(self):
        assert wm.gdd_day(8, -2, crop="corn") == 0.0

    def test_accumulation_from_emergence(self):
        obs = observed(D("2026-05-20"), 150, tmax=28.0, tmin=16.0)        # 12 GDD / day
        total = wm.gdd_sum(obs, D("2026-06-01"), D("2026-09-30"), crop="corn")
        assert total == pytest.approx(12.0 * 122)                          # Jun 1 .. Sep 30 inclusive


class TestMaturityAndDryDown:
    def test_corn_matures_when_gdd_requirement_is_met(self):
        obs = observed(D("2026-05-01"), 170, tmax=28.0, tmin=16.0)        # 12 GDD/day -> 1450 after ~121 days
        mat = wm.maturity_date(obs, emergence=D("2026-05-15"), crop="corn")
        assert D("2026-09-10") <= mat <= D("2026-09-16")

    def test_not_mature_yet_returns_none(self):
        obs = observed(D("2026-05-01"), 170, tmax=20.0, tmin=10.0)        # 5 GDD/day: never reaches 1450 by Oct 17
        assert wm.maturity_date(obs, emergence=D("2026-05-15"), crop="corn") is None

    def test_corn_dries_faster_when_warm(self):
        warm = observed(D("2026-09-10"), 60, tmax=26.0, tmin=14.0)         # mean 20 C -> 0.6 pt/day
        cool = observed(D("2026-09-10"), 60, tmax=16.0, tmin=6.0)          # mean 11 C -> ~0.33 pt/day
        r_warm = wm.combine_ready_date(warm, maturity=D("2026-09-10"), crop="corn")
        r_cool = wm.combine_ready_date(cool, maturity=D("2026-09-10"), crop="corn")
        assert r_warm < r_cool
        assert D("2026-09-20") <= r_warm <= D("2026-09-28")                # 32% -> 25% at ~0.6/day is ~12 days

    def test_soybeans_are_ready_days_after_leaf_drop(self):
        obs = observed(D("2026-09-20"), 40, tmax=20.0, tmin=8.0)
        r = wm.combine_ready_date(obs, maturity=D("2026-09-25"), crop="soybean")
        assert D("2026-09-30") <= r <= D("2026-10-06")


class TestWorkability:
    def test_rain_blocks_the_day_and_the_next_one(self):
        obs = observed(D("2026-10-10"), 5)
        obs["precip_mm"] = [0.0, 14.0, 0.0, 0.0, 0.0]
        fc = {"dates": [d.isoformat() for d in days(D("2026-10-15"), 3)], "precip_mm": [0.0, 30.0, 0.0]}
        w = wm.workable_days(obs, fc, through=D("2026-10-17"))
        assert w[D("2026-10-10")] is True
        assert w[D("2026-10-11")] is False and w[D("2026-10-12")] is False     # 14 mm: that day and the next
        assert w[D("2026-10-13")] is True
        assert w[D("2026-10-16")] is False and w[D("2026-10-17")] is False     # 30 mm: that day and the next (two if >= 25)

    def test_light_showers_do_not_block(self):
        obs = observed(D("2026-10-10"), 3)
        obs["precip_mm"] = [3.0, 0.0, 4.9]
        w = wm.workable_days(obs, {"dates": [], "precip_mm": []}, through=D("2026-10-12"))
        assert all(w.values())


class TestFrost:
    def test_frost_nights_in_forecast(self):
        fc = {"dates": ["2026-10-17", "2026-10-18", "2026-10-19"], "precip_mm": [0, 0, 0], "tmin_c": [4.0, -1.0, 2.0]}
        assert wm.frost_nights(fc) == [D("2026-10-18")]

    def test_no_temperatures_means_unknown(self):
        assert wm.frost_nights({"dates": ["2026-10-17"], "precip_mm": [0]}) is None


class TestBestWindow:
    def test_window_is_the_first_run_of_workable_days_once_ready(self):
        obs = observed(D("2026-09-01"), 46, tmax=24.0, tmin=12.0)        # through Oct 16
        fc = {"dates": [d.isoformat() for d in days(TODAY, 7)], "precip_mm": [0, 0, 20.0, 0, 0, 0, 0], "tmin_c": [5, 4, 3, 2, 6, 7, 8]}
        out = wm.harvest_outlook(obs, fc, today=TODAY, crop="corn", emergence=D("2026-05-15"), ready_override=D("2026-10-15"))
        assert out["combine_ready"] == "2026-10-15"
        assert out["window"] == {"start": "2026-10-17", "end": "2026-10-18"}      # before the 20 mm day
        assert out["next_window"] == {"start": "2026-10-21", "end": "2026-10-23"}  # after the rain and its wet day
        assert out["rain_days"] == ["2026-10-19"] and out["frost_nights"] == []

    def test_not_ready_yet_gives_an_estimate_not_a_window(self):
        obs = observed(D("2026-09-01"), 46, tmax=18.0, tmin=8.0)
        fc = {"dates": [d.isoformat() for d in days(TODAY, 7)], "precip_mm": [0] * 7}
        out = wm.harvest_outlook(obs, fc, today=TODAY, crop="corn", emergence=D("2026-05-15"), ready_override=D("2026-10-25"))
        assert out["combine_ready"] == "2026-10-25" and out["window"] is None
        assert "ready" in out["summary"].lower() and "2026-10-25" in out["summary"]

    def test_summary_reads_as_a_sentence(self):
        obs = observed(D("2026-09-01"), 46)
        fc = {"dates": [d.isoformat() for d in days(TODAY, 4)], "precip_mm": [0, 0, 0, 0], "tmin_c": [3, -2, 1, 5]}
        out = wm.harvest_outlook(obs, fc, today=TODAY, crop="soybean", emergence=D("2026-05-25"), ready_override=D("2026-10-10"))
        assert out["frost_nights"] == ["2026-10-18"]
        assert "frost" in out["summary"].lower() and out["summary"].endswith(".")
