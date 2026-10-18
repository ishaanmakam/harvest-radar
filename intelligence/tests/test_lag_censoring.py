"""Regional peak-to-harvest lag learned per crop, and corrected for censoring: fields still standing at as_of only
tell us their lag is at least (as_of - peak). Using only the harvested fields (the early ones) biases the lag short
and calls standing corn 'ready' weeks early."""
from datetime import date

import harvest_model as hm

AS_OF = date(2026, 10, 17)


class TestCensoredMedian:
    def test_without_censoring_it_is_the_plain_median(self):
        assert hm.censored_median([50, 52, 54, 56, 58], []) == 54

    def test_censored_lower_bounds_pull_the_median_up(self):
        # 5 harvested at 50-58 d; 5 still standing whose lag is already > 70 d: the true median is well above 58
        med = hm.censored_median([50, 52, 54, 56, 58], [70, 72, 74, 76, 78])
        assert med > 58

    def test_censored_values_below_the_events_do_not_matter(self):
        assert hm.censored_median([50, 52, 54, 56, 58], [10, 12]) == 54

    def test_all_censored_returns_none(self):
        assert hm.censored_median([], [60, 70]) is None


class TestLagWindowPerCrop:
    def test_lag_is_learned_per_crop_when_enough_fields(self):
        lags = {"corn": [55 + i % 7 for i in range(30)], "soybean": [48 + i % 5 for i in range(30)]}
        out = hm.lag_windows(lags, {}, default_lag=58)
        assert out["corn"][0] > out["soybean"][0]
        assert abs(out["corn"][0] - 58) <= 1 and abs(out["soybean"][0] - 50) <= 1

    def test_crop_with_too_few_fields_falls_back_to_the_pooled_lag(self):
        lags = {"corn": [55 + i % 7 for i in range(30)], "other": [40, 41]}
        out = hm.lag_windows(lags, {}, default_lag=58)
        assert out["other"][0] == out["*"][0]

    def test_censoring_raises_the_crop_lag(self):
        lags = {"corn": [55 + i % 7 for i in range(30)]}
        censored = {"corn": [75 + i % 5 for i in range(30)]}
        plain = hm.lag_windows(lags, {}, default_lag=58)["corn"][0]
        corrected = hm.lag_windows(lags, censored, default_lag=58)["corn"][0]
        assert corrected > plain


class TestRunUsesCropLags:
    def test_status_carries_per_crop_lags(self, tmp_path):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for name in ("fields.geojson", "timeseries.json", "fires.json"):
            (tmp_path / name).write_bytes((root / "data" / "mock" / name).read_bytes())
        out = hm.run(tmp_path)
        assert "peak_to_harvest_days_by_crop" in out
        assert set(out["peak_to_harvest_days_by_crop"]) >= {"corn", "soybean"}
        assert out["peak_to_harvest_days_by_crop"]["corn"] != out["peak_to_harvest_days_by_crop"]["soybean"]
