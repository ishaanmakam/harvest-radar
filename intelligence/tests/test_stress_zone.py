"""Stress judged relative to the zone and only across nearby passes: a whole zone senescing (corn in September) is
not stress; a field dropping much faster than its neighbours is. Pass pairs too far apart are not judged."""
from datetime import date

import decide
from test_decide import DRY, check, rec, status_of

TODAY = date(2026, 10, 17)
D = date.fromisoformat


class TestStressRelativeToZone:
    def test_field_declining_like_its_zone_is_not_stress(self):
        passes = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]   # drop 0.14
        plan = decide.farm_plan(rec(), passes, DRY, [], None, TODAY, zone_drop=0.13)                          # zone median drop 0.13
        assert status_of(plan)["stress"] == "clear"
        assert "zone" in check(plan, "stress")["detail"].lower()

    def test_field_declining_much_faster_than_its_zone_is_stress(self):
        passes = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]
        plan = decide.farm_plan(rec(), passes, DRY, [], None, TODAY, zone_drop=0.01)
        assert status_of(plan)["stress"] == "watch"

    def test_without_zone_context_the_absolute_rule_still_applies(self):
        passes = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]
        assert status_of(decide.farm_plan(rec(), passes, DRY, [], None, TODAY))["stress"] == "watch"

    def test_passes_too_far_apart_are_not_judged(self):
        passes = [(D("2026-09-20"), 0.74, 0.42), (D("2026-10-15"), 0.58, 0.30)]                               # 25 days apart
        plan = decide.farm_plan(rec(), passes, DRY, [], None, TODAY)
        assert status_of(plan)["stress"] == "clear"
        assert "apart" in check(plan, "stress")["detail"].lower()


class TestZoneDeclines:
    def test_median_drop_per_zone_between_each_fields_last_two_passes(self):
        dates = ["2026-10-06", "2026-10-11", "2026-10-16"]
        ts = {"dates": dates, "fields": {
            "F0001": {"ndvi": [0.80, 0.70, 0.60], "nbr": [0.4, 0.4, 0.4], "clear_frac": [1, 1, 1]},     # drop 0.10
            "F0002": {"ndvi": [0.80, None, 0.62], "nbr": [0.4, None, 0.4], "clear_frac": [1, 0, 1]},   # drop 0.18 over 10 days
            "F0003": {"ndvi": [0.80, 0.79, 0.50], "nbr": [0.4, 0.4, 0.4], "clear_frac": [1, 1, 1]},     # drop 0.29
            "F0009": {"ndvi": [None, None, 0.50], "nbr": [None, None, 0.4], "clear_frac": [0, 0, 1]},   # one pass: no drop
        }}
        zone_of = {"F0001": "Z01", "F0002": "Z01", "F0003": "Z01", "F0009": "Z02"}
        drops = decide.zone_declines(ts, zone_of, max_gap_days=12)
        assert drops["Z01"] == 0.18                                        # median of 0.10, 0.18, 0.29
        assert "Z02" not in drops
