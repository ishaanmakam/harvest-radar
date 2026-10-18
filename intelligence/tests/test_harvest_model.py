"""Tests for the peak and stage rules in intelligence/harvest_model.py."""
from datetime import date

import harvest_model as hm

AS_OF = date(2026, 10, 17)


def test_stage_is_measured_from_window_centre_not_clamped_start():
    # window starts today (clamped) but is centred ~8 days out -> maturing, not ready
    assert hm.predicted_stage(date(2026, 10, 17), date(2026, 11, 3), AS_OF) == ("maturing", 8)


def test_ready_when_centre_within_3_days():
    assert hm.predicted_stage(date(2026, 10, 17), date(2026, 10, 23), AS_OF)[0] == "ready"


def test_growing_when_centre_beyond_14_days():
    assert hm.predicted_stage(date(2026, 10, 25), date(2026, 11, 15), AS_OF)[0] == "growing"


def test_overdue_window_counts_as_ready():
    assert hm.predicted_stage(AS_OF, date(2026, 10, 20), AS_OF) == ("ready", 1)


def test_peak_is_centre_of_plateau_not_noisy_argmax():
    sm = [0.2, 0.4, 0.76, 0.77, 0.78, 0.80, 0.77, 0.76, 0.6, 0.3]   # argmax 5, plateau (>= 0.76) spans 2..7
    assert hm.plateau_centre(list(range(10)), sm, 5, drop=0.04) == 4.5


def test_sharp_peak_keeps_argmax():
    assert hm.plateau_centre(list(range(5)), [0.2, 0.5, 0.8, 0.5, 0.2], 2, drop=0.04) == 2


def test_plateau_centre_is_taken_in_time_not_index_when_sampling_is_uneven():
    xs = [0, 20, 30, 32, 34, 36, 40]                      # sparse before the peak, dense after (monsoon clouds)
    sm = [0.4, 0.76, 0.80, 0.78, 0.77, 0.76, 0.5]         # plateau idx 1..5 -> time midpoint (20+36)/2
    assert hm.plateau_centre(xs, sm, 2, drop=0.04) == 28


def test_plateau_centre_works_on_dates():
    xs = [date(2026, 8, 1), date(2026, 8, 21), date(2026, 8, 31), date(2026, 9, 5), date(2026, 9, 10)]
    sm = [0.4, 0.78, 0.80, 0.78, 0.5]
    assert hm.plateau_centre(xs, sm, 2, drop=0.04) == date(2026, 8, 28)


def test_lag_window_is_median_and_80pct_half_range():
    lags = [30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50]
    lag, half = hm.lag_window(lags, default_lag=40)
    assert lag == 40
    assert abs(half - 9.6) < 1e-9          # (p90 49.6 - p10 30.4) / 2, not the old IQR/2 = 5


def test_lag_window_falls_back_to_defaults_with_few_fields():
    assert hm.lag_window([38, 41, 40], default_lag=40) == (40, 7)
