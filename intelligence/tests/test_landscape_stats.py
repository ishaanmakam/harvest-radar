"""landscape_stats.py: the season at landscape scale (harvest progress per crop with a logistic fit, combine demand
over the next two weeks, a zone summary, the USDA comparison and the headline numbers). Hand-built fixtures pin the
math; one read-only check on data/mock asserts shapes and ranges only (the mock regenerates)."""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import landscape_stats as ls

D = date.fromisoformat
TODAY = D("2026-10-17")
SEASON_START = D("2026-06-01")
ROOT = Path(__file__).resolve().parents[2]
KINDS = ["harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change"]


# ----------------------------------------------------------------------------- fixture builders
def config(**over):
    c = {"as_of": "2026-10-17", "season_start": "2026-06-01", "machine_rate_ha_per_day": 40.0,
         "forecast_horizon_days": 14, "thresholds": {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25}}
    c.update(over)
    return c


def feature(fid, zone="Z01", ha=10.0, crop="corn"):
    ring = [[-93.5, 42.0], [-93.5, 42.01], [-93.49, 42.01], [-93.49, 42.0], [-93.5, 42.0]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": ha, "crop": crop, "source": "test"}}


def fields(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def zones(*ids):
    feats = [{"type": "Feature", "geometry": None,
              "properties": {"zone_id": z, "name": f"Zone {z}", "centroid": [-93.5, 42.0]}} for z in ids]
    return {"type": "FeatureCollection", "features": feats}


def rec(fid, zone="Z01", stage="harvested", harvest=None, start=None, end=None, peak=0.8):
    return {"field_id": fid, "zone_id": zone, "stage": stage, "peak_date": "2026-08-01", "peak_ndvi": peak,
            "harvest_date": harvest, "predicted_harvest_start": start, "predicted_harvest_end": end,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.8, "notes": ""}


def status(*recs):
    return {"as_of": "2026-10-17", "peak_to_harvest_days": 60, "peak_to_harvest_days_by_crop": {}, "fields": list(recs)}


def timeseries(dates, series):
    return {"source": "test", "dates": list(dates),
            "fields": {fid: {"ndvi": list(v), "nbr": [None] * len(v), "clear_frac": [1.0] * len(v)}
                       for fid, v in series.items()}}


def plan(fid, flag, *kinds, zone="Z01"):
    """A plan whose non-clear checks are the (kind, status) pairs given; every other check is clear."""
    given = dict(kinds)
    checks = [{"kind": k, "status": given.get(k, "clear"), "detail": "", "where": "", "window": {}} for k in KINDS]
    return {"farm_id": fid, "as_of": "2026-10-16", "flag": flag, "source": "vegetation_index", "today": "",
            "checks": checks, "risks": [], "zone_id": zone}


def plans(*ps):
    return {"as_of": "2026-10-16", "source": "test", "vision_verdict": None, "plans": list(ps)}


def data(**over):
    base = {"fields": fields(), "zones": zones("Z01"), "timeseries": timeseries([], {}), "status": status(),
            "weather": {}, "fires": {"detections": []}, "insights": {}, "plans": None, "route": None,
            "truth": None, "usda": None, "chcs": None}
    base.update(over)
    return base


def iso(day_offset, base=TODAY):
    return (base + timedelta(days=day_offset)).isoformat()


def logistic_season():
    """Four corn fields harvested on season days 0, 10, 20, 30 with areas that put the cumulative share of the
    10000 ha crop at 0.1192, 0.2689, 0.5000, 0.7311 = 1 / (1 + exp(-(-2 + 0.1 * day))). The logistic with slope 0.1
    per day and intercept -2 passes through all four, so the least-squares fit must return it:
      50% at logit 0      -> day 20   -> 2026-06-21
      75% at logit ln 3   -> day 31.0 -> 2026-07-02
      90% at logit ln 9   -> day 42.0 -> 2026-07-13
    A fifth field (2689 ha) stays standing, so 73.11% of the crop area is harvested today."""
    areas = {"A": 1192.0, "B": 1497.0, "C": 2311.0, "D": 2311.0, "E": 2689.0}
    days = {"A": 0, "B": 10, "C": 20, "D": 30}
    feats = [feature(f, ha=a, crop="corn") for f, a in areas.items()]
    recs = [rec(f, harvest=iso(days[f], SEASON_START)) for f in days]
    recs.append(rec("E", stage="maturing", start=iso(0), end=iso(3)))
    return data(fields=fields(*feats), status=status(*recs))


def check_json(out):
    """Everything the module returns must survive json.dumps unchanged (no numpy scalars, no NaN)."""
    text = json.dumps(out, allow_nan=False)
    assert json.loads(text) == out


# ----------------------------------------------------------------------------- contract
class TestContract:
    def test_key_and_sections(self):
        out = ls.analyze(logistic_season(), config())
        assert out["key"] == "landscape"
        for k in ("fields", "landscape", "zones", "assumptions", "method"):
            assert k in out
        assert isinstance(out["landscape"]["notes"], list)
        assert isinstance(out["method"], str) and len(out["method"]) > 40
        check_json(out)

    def test_per_field_rows_are_small(self):
        out = ls.analyze(logistic_season(), config())
        assert set(out["fields"]) == {"A", "B", "C", "D", "E"}
        for row in out["fields"].values():
            assert len(json.dumps(row)) < 1000

    def test_nothing_mentions_datetime_now(self):
        src = (ROOT / "intelligence" / "landscape_stats.py").read_text(encoding="utf-8")
        assert "datetime.now" not in src and "date.today" not in src


# ----------------------------------------------------------------------------- harvest progress
class TestHarvestProgress:
    def test_logistic_fit_recovers_known_curve(self):
        out = ls.analyze(logistic_season(), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["total_ha"] == pytest.approx(10000.0)
        assert corn["harvested_ha"] == pytest.approx(7311.0)
        assert corn["pct_harvested_today"] == pytest.approx(73.11, abs=0.01)
        assert corn["fit_points"] == 4
        assert corn["fit_slope_per_day"] == pytest.approx(0.1, abs=1e-3)
        assert corn["fit_r2"] == pytest.approx(1.0, abs=1e-4)
        assert corn["projected_50_pct"] == "2026-06-21"
        assert corn["projected_75_pct"] == "2026-07-02"
        assert corn["projected_90_pct"] == "2026-07-13"
        assert corn["recent_rate_ha_per_day"] == 0.0          # nothing cut in the last ten days
        # the observed curve already crossed 50% on day 20 and never reached 75%
        assert corn["crossed_50_pct"] == "2026-06-21"
        assert corn["crossed_75_pct"] is None
        # the fit put 75% on 2026-07-02, long before today, yet the curve never got there: say so
        assert corn["fit_caveat"] is not None
        assert corn["curve"][0] == ["2026-06-01", pytest.approx(11.92, abs=0.01)]
        assert corn["curve"][-1] == ["2026-07-01", pytest.approx(73.11, abs=0.01)]

    def test_fewer_than_three_points_gives_no_projection(self):
        feats = [feature("A", ha=30.0), feature("B", ha=30.0), feature("C", ha=40.0)]
        recs = [rec("A", harvest="2026-10-01"), rec("B", harvest="2026-10-10"), rec("C", stage="ready", start=iso(0), end=iso(2))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["fit_points"] == 2
        assert corn["pct_harvested_today"] == pytest.approx(60.0)
        assert corn["projected_50_pct"] is None and corn["projected_90_pct"] is None
        assert corn["fit_r2"] is None and corn["fit_caveat"] is None

    def test_flat_curve_has_no_positive_slope(self):
        # zero-area fields add points without adding share: three equal shares -> slope 0 -> no projection
        feats = [feature("A", ha=50.0), feature("B", ha=0.0), feature("C", ha=0.0), feature("D", ha=50.0)]
        recs = [rec("A", harvest="2026-09-01"), rec("B", harvest="2026-09-06"), rec("C", harvest="2026-09-11"),
                rec("D", stage="maturing", start=iso(0), end=iso(5))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["fit_points"] == 3
        assert corn["projected_50_pct"] is None
        assert corn["fit_r2"] is None

    def test_points_outside_2_to_98_pct_are_not_fitted(self):
        feats = [feature("A", ha=100.0), feature("B", ha=9800.0), feature("C", ha=50.0), feature("D", ha=50.0)]
        recs = [rec("A", harvest="2026-09-01"), rec("B", harvest="2026-09-11"), rec("C", harvest="2026-09-21"),
                rec("D", stage="ready", start=iso(0), end=iso(1))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["fit_points"] == 0                           # shares 0.01, 0.99, 0.995
        assert corn["projected_50_pct"] is None
        assert corn["pct_harvested_today"] == pytest.approx(99.5)

    def test_recent_rate_counts_the_last_ten_days_only(self):
        # today (20 ha) and nine days ago (30 ha) count; ten days ago (40 ha) and a month ago (99 ha) do not
        feats = [feature("A", ha=20.0), feature("B", ha=30.0), feature("C", ha=40.0), feature("D", ha=99.0)]
        recs = [rec("A", harvest=iso(0)), rec("B", harvest=iso(-9)), rec("C", harvest=iso(-10)), rec("D", harvest=iso(-30))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["recent_rate_ha_per_day"] == pytest.approx(5.0)   # (20 + 30) / 10

    def test_crop_other_without_harvest_reads_zero(self):
        feats = [feature("A", ha=12.0, crop="other"), feature("B", ha=8.0, crop="corn")]
        recs = [rec("A", stage="unknown", peak=None), rec("B", harvest="2026-10-10")]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        prog = out["landscape"]["harvest_progress"]
        assert prog["other"]["pct_harvested_today"] == 0.0
        assert prog["other"]["fit_points"] == 0 and prog["other"]["projected_50_pct"] is None
        assert prog["corn"]["pct_harvested_today"] == pytest.approx(100.0)

    def test_field_missing_from_status_counts_as_standing(self):
        feats = [feature("A", ha=10.0), feature("B", ha=10.0)]
        out = ls.analyze(data(fields=fields(*feats), status=status(rec("A", harvest="2026-10-01"))), config())
        corn = out["landscape"]["harvest_progress"]["corn"]
        assert corn["pct_harvested_today"] == pytest.approx(50.0)
        assert out["fields"]["B"]["stage"] == "unknown"


# ----------------------------------------------------------------------------- combine demand
class TestCombineDemand:
    def demand_data(self, **over):
        # R: 40 ha ready, window today..today+3 (4 days)        -> 10 ha/day on days 0..3
        # M: 80 ha maturing, window today+2..today+5 (4 days)   -> 20 ha/day on days 2..5
        # H: harvested, X: growing without a window              -> no demand
        feats = [feature("R", ha=40.0), feature("M", ha=80.0, crop="soybean"), feature("H", ha=30.0), feature("X", ha=30.0)]
        recs = [rec("R", stage="ready", start=iso(0), end=iso(3)), rec("M", stage="maturing", start=iso(2), end=iso(5)),
                rec("H", harvest="2026-10-01"), rec("X", stage="growing")]
        base = data(fields=fields(*feats), status=status(*recs))
        base.update(over)
        return base

    def test_uniform_spread_and_machine_days(self):
        out = ls.analyze(self.demand_data(), config(machine_rate_ha_per_day=10.0))
        dem = out["landscape"]["combine_demand"]
        ha = [row["ha"] for row in dem["days"]]
        assert len(dem["days"]) == 14
        assert dem["days"][0]["date"] == iso(0) and dem["days"][-1]["date"] == iso(13)
        assert ha == pytest.approx([10, 10, 30, 30, 20, 20] + [0] * 8)
        assert [row["machine_days"] for row in dem["days"]] == pytest.approx([1, 1, 3, 3, 2, 2] + [0] * 8)
        assert dem["peak_day"] == iso(2)                         # first day of the maximum
        assert dem["peak_ha"] == pytest.approx(30.0)
        assert dem["fields_in_window"] == 2
        assert dem["ha_in_horizon"] == pytest.approx(120.0)
        assert dem["total_machine_days"] == pytest.approx(12.0)
        assert out["fields"]["R"]["demand_ha_per_day"] == pytest.approx(10.0)
        assert out["fields"]["H"]["demand_ha_per_day"] is None

    def test_capacity_from_simulated_depots(self):
        chcs = {"note": "simulated", "chcs": [{"chc_id": "CHC-1", "machines": 1}]}
        out = ls.analyze(self.demand_data(chcs=chcs), config(machine_rate_ha_per_day=10.0))
        dem = out["landscape"]["combine_demand"]
        assert dem["capacity_machines"] == 1
        assert "simulated" in dem["capacity_source"]
        assert dem["days_over_capacity"] == 4                    # machine-days 3, 3, 2, 2 exceed one machine

    def test_capacity_list_shape_and_absence(self):
        out = ls.analyze(self.demand_data(chcs=[{"chc_id": "a", "machines": 2}, {"chc_id": "b", "machines": 3}]), config())
        assert out["landscape"]["combine_demand"]["capacity_machines"] == 5
        out = ls.analyze(self.demand_data(chcs=None), config())
        dem = out["landscape"]["combine_demand"]
        assert dem["capacity_machines"] is None and dem["days_over_capacity"] is None

    def test_window_already_open_is_spread_from_today(self):
        # 40 ha, window today-2..today+1: the two days already gone fold into today..today+1 -> 20 ha/day
        feats = [feature("R", ha=40.0)]
        recs = [rec("R", stage="ready", start=iso(-2), end=iso(1))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        ha = [row["ha"] for row in out["landscape"]["combine_demand"]["days"]]
        assert ha[:3] == pytest.approx([20.0, 20.0, 0.0])

    def test_window_past_horizon_is_counted_beyond(self):
        # 70 ha over 7 days starting today+10: four days inside the horizon (40 ha), three beyond (30 ha)
        feats = [feature("M", ha=70.0)]
        recs = [rec("M", stage="maturing", start=iso(10), end=iso(16))]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs)), config())
        dem = out["landscape"]["combine_demand"]
        assert dem["ha_in_horizon"] == pytest.approx(40.0)
        assert dem["ha_beyond_horizon"] == pytest.approx(30.0)

    def test_workable_days_from_forecast_when_present(self):
        fc = {"dates": [iso(i) for i in range(14)], "precip_mm": [0.0, 20.0] + [0.0] * 12}
        out = ls.analyze(self.demand_data(weather={"forecast": fc}), config())
        days = out["landscape"]["combine_demand"]["days"]
        assert days[0]["workable"] is True
        assert days[1]["workable"] is False                      # 20 mm blocks that day and the next
        assert days[2]["workable"] is False
        assert days[3]["workable"] is True
        out = ls.analyze(self.demand_data(weather={}), config())
        assert out["landscape"]["combine_demand"]["days"][0]["workable"] is None


# ----------------------------------------------------------------------------- zones
class TestZones:
    def zone_data(self, with_plans=True, ts=True):
        feats = [feature("A", "Z01", 10.0, "corn"), feature("B", "Z01", 30.0, "soybean"), feature("C", "Z01", 20.0, "corn"),
                 feature("D", "Z02", 50.0, "corn")]
        recs = [rec("A", harvest="2026-10-01"), rec("B", stage="ready", start=iso(0), end=iso(3)),
                rec("C", stage="maturing", start=iso(2), end=iso(8)), rec("D", stage="growing")]
        series = timeseries(["2026-10-06", "2026-10-11", "2026-10-16"],
                            {"A": [0.5, None, 0.2], "B": [0.6, 0.7, None], "D": [None, None, None]}) if ts else None
        ps = plans(plan("A", "fine"), plan("B", "act_now", ("harvest_before_rain", "act_now"), ("harvest_ready", "watch")),
                   plan("C", "watch", ("harvest_ready", "watch")), plan("D", "fine", zone="Z02")) if with_plans else None
        return data(fields=fields(*feats), zones=zones("Z01", "Z02"), status=status(*recs), timeseries=series, plans=ps)

    def test_zone_rows(self):
        out = ls.analyze(self.zone_data(), config())
        z1, z2 = out["zones"]["Z01"], out["zones"]["Z02"]
        assert z1["name"] == "Zone Z01"
        assert z1["fields"] == 3 and z1["ha"] == pytest.approx(60.0)
        assert z1["ha_by_crop"] == {"corn": pytest.approx(30.0), "soybean": pytest.approx(30.0)}
        assert z1["pct_harvested_area"] == pytest.approx(100 * 10 / 60, abs=0.05)   # zone percents carry one decimal
        assert z1["pct_ready_area"] == pytest.approx(50.0)
        assert z1["mean_latest_ndvi"] == pytest.approx((0.2 + 0.7) / 2)   # C absent from the timeseries
        assert z1["act_now"] == 1 and z1["watch"] == 1 and z1["fine"] == 1
        assert z1["top_kind"] == "harvest_ready"                 # two watch rows beat one act_now row
        assert z2["fields"] == 1 and z2["mean_latest_ndvi"] is None   # all-null series
        assert z2["top_kind"] is None and z2["fine"] == 1

    def test_top_kind_tie_breaks_on_severity(self):
        d0 = self.zone_data()
        d0["plans"] = plans(plan("A", "fine"), plan("B", "act_now", ("flood", "act_now")), plan("C", "watch", ("stress", "watch")),
                            plan("D", "fine", zone="Z02"))
        out = ls.analyze(d0, config())
        assert out["zones"]["Z01"]["top_kind"] == "flood"

    def test_without_plans_flags_are_none(self):
        out = ls.analyze(self.zone_data(with_plans=False), config())
        z1 = out["zones"]["Z01"]
        assert z1["act_now"] is None and z1["top_kind"] is None
        assert out["landscape"]["flags"] is None
        assert any("plans" in n for n in out["landscape"]["notes"])

    def test_without_timeseries(self):
        out = ls.analyze(self.zone_data(ts=False), config())
        assert out["zones"]["Z01"]["mean_latest_ndvi"] is None
        assert out["fields"]["A"]["latest_ndvi"] is None
        assert any("timeseries" in n for n in out["landscape"]["notes"])

    def test_zone_in_fields_but_not_in_zones_file_still_appears(self):
        d0 = self.zone_data()
        d0["zones"] = zones("Z01")
        out = ls.analyze(d0, config())
        assert "Z02" in out["zones"] and out["zones"]["Z02"]["fields"] == 1


# ----------------------------------------------------------------------------- USDA comparison
class TestUsda:
    def usda(self):
        return {"source": "sample", "state": "IA", "district": "CENTRAL", "series": {
            "CORN": {"commodity": "CORN", "year": 2025, "rows": [
                {"week_ending": "2025-10-12", "year": 2025, "pct_harvested": 31.0},
                {"week_ending": "2025-10-19", "year": 2025, "pct_harvested": 52.0},
                {"week_ending": "2025-10-26", "year": 2025, "pct_harvested": 71.0}]},
            "SOYBEANS": {"commodity": "SOYBEANS", "year": 2025, "rows": [
                {"week_ending": "2025-10-19", "year": 2025, "pct_harvested": 86.0}]}}}

    def test_nearest_week_by_month_and_day(self):
        feats = [feature("A", ha=60.0), feature("B", ha=40.0), feature("S", ha=10.0, crop="soybean")]
        recs = [rec("A", harvest="2026-10-10"), rec("B", stage="ready", start=iso(0), end=iso(3)), rec("S", harvest="2026-10-01")]
        out = ls.analyze(data(fields=fields(*feats), status=status(*recs), usda=self.usda()), config())
        cmp = out["landscape"]["usda_comparison"]
        assert cmp["district"] == "CENTRAL"
        corn = cmp["crops"]["corn"]
        assert corn["week_ending"] == "2025-10-19"              # 2 days from 10-17; 10-12 is 5 days away
        assert corn["usda_pct_harvested"] == 52.0
        assert corn["ours_pct_harvested"] == pytest.approx(60.0)
        assert corn["diff_pp"] == pytest.approx(8.0)             # ours minus USDA
        soy = cmp["crops"]["soybean"]
        assert soy["ours_pct_harvested"] == pytest.approx(100.0) and soy["diff_pp"] == pytest.approx(14.0)

    def test_method_and_assumption_say_which_year_usda_is(self):
        # The cache holds last season's rows (year 2025) while as_of is 2026, so diff_pp is a gap to last year's
        # pace for the same calendar week, not to a USDA figure for this season. The officer- and pitch-facing
        # method paragraph and the usda_match assumption have to say so, not only the note inside the usda block.
        out = ls.analyze(data(fields=fields(feature("A")), status=status(rec("A", harvest="2026-10-10")), usda=self.usda()),
                         config())
        method = out["method"].lower()
        assert "same week" in method and "latest" in method and "published" in method
        assumption = out["assumptions"]["usda_match"].lower()
        assert "same week" in assumption and "latest" in assumption and "last season" in assumption
        assert out["landscape"]["usda_comparison"]["crops"]["corn"]["year"] == 2025
        note = out["landscape"]["usda_comparison"]["note"].lower()
        assert "last season" in note and "latest" in note

    def test_absent_or_empty_is_none(self):
        base = data(fields=fields(feature("A")), status=status(rec("A", harvest="2026-10-10")))
        assert ls.analyze(base, config())["landscape"]["usda_comparison"] is None
        base["usda"] = {"series": {"CORN": {"rows": []}}}
        out = ls.analyze(base, config())
        assert out["landscape"]["usda_comparison"] is None
        base["usda"] = {"series": {"CORN": {"rows": [{"week_ending": "bad", "pct_harvested": None}]}}}
        assert ls.analyze(base, config())["landscape"]["usda_comparison"] is None


# ----------------------------------------------------------------------------- headline and degrade
class TestLandscape:
    def test_headline_numbers_and_narrative(self):
        d0 = logistic_season()
        d0["plans"] = plans(plan("A", "fine"), plan("B", "fine"), plan("C", "watch", ("stress", "watch")),
                            plan("D", "fine"), plan("E", "act_now", ("flood", "act_now")))
        d0["route"] = {"as_of": "2026-10-17", "stops": [{"farm_id": "E", "action": "visit", "why": "", "kind": "flood", "status": "act_now"},
                                                      {"farm_id": "C", "action": "call", "why": "", "kind": "stress", "status": "watch"}]}
        out = ls.analyze(d0, config())
        L = out["landscape"]
        assert L["as_of"] == "2026-10-17"
        assert L["fields"] == 5
        assert L["ha_total"] == pytest.approx(10000.0)
        assert L["ha_by_crop"] == {"corn": pytest.approx(10000.0)}
        assert L["harvested_ha"] == pytest.approx(7311.0)
        assert L["standing_ha"] == pytest.approx(2689.0)
        assert L["flags"] == {"act_now": 1, "watch": 1, "fine": 3}
        assert L["route_stops"] == 2 and L["route_visits"] == 1 and L["route_calls"] == 1
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", L["narrative"].strip()) if x]
        assert len(sentences) == 3
        assert chr(8212) not in L["narrative"]                  # no em dash in farmer-facing text

    def test_missing_fields_file(self):
        out = ls.analyze(data(fields=None), config())
        assert out["fields"] == {}
        assert out["landscape"]["fields"] == 0
        assert any("fields" in n for n in out["landscape"]["notes"])
        check_json(out)

    def test_missing_status_file(self):
        out = ls.analyze(data(fields=fields(feature("A")), status=None), config())
        assert out["fields"]["A"]["stage"] == "unknown"
        assert out["landscape"]["harvest_progress"]["corn"]["pct_harvested_today"] == 0.0
        assert any("status" in n for n in out["landscape"]["notes"])
        check_json(out)

    def test_one_pass_only_and_empty_fires(self):
        d0 = data(fields=fields(feature("A")), status=status(rec("A", stage="growing")),
                  timeseries=timeseries(["2026-10-16"], {"A": [0.61]}), fires={"detections": []})
        out = ls.analyze(d0, config())
        assert out["fields"]["A"]["latest_ndvi"] == pytest.approx(0.61)
        assert out["fields"]["A"]["latest_pass"] == "2026-10-16"
        assert out["landscape"]["latest_pass"] == "2026-10-16"
        check_json(out)

    def test_garbage_input_never_raises(self):
        out = ls.analyze({}, {"as_of": "2026-10-17"})
        assert out["key"] == "landscape" and out["fields"] == {}
        check_json(out)
        out = ls.analyze({"fields": "nope", "status": 3, "timeseries": [], "plans": {"plans": "x"}, "usda": 7, "chcs": "y"},
                         config())
        assert out["key"] == "landscape"
        check_json(out)

    def test_prior_is_optional(self):
        a = ls.analyze(logistic_season(), config(), prior=None)
        b = ls.analyze(logistic_season(), config(), prior={"weather": {"anything": 1}})
        assert a["landscape"]["harvest_progress"] == b["landscape"]["harvest_progress"]


# ----------------------------------------------------------------------------- data/mock (read-only)
class TestMock:
    def test_mock_shapes_and_ranges(self):
        mock = ROOT / "data" / "mock"
        if not (mock / "fields.geojson").exists():
            pytest.skip("data/mock not present on this box")

        def load(n, default=None):
            p = mock / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

        chcs_path = ROOT / "config" / "chcs.json"
        d0 = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
              "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
              "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
              "truth": load("truth.json"), "usda": load("usda_crop_progress.json"),
              "chcs": json.loads(chcs_path.read_text(encoding="utf-8")) if chcs_path.exists() else None}
        cfg = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
        out = ls.analyze(d0, cfg)
        check_json(out)
        L = out["landscape"]
        n = len(d0["fields"]["features"])
        assert len(out["fields"]) == n and L["fields"] == n
        assert len(out["zones"]) >= len(d0["zones"]["features"])
        assert L["ha_total"] > 0
        assert abs(sum(L["ha_by_crop"].values()) - L["ha_total"]) < 0.5
        assert 0 <= L["pct_harvested_area"] <= 100
        for crop, p in L["harvest_progress"].items():
            assert 0 <= p["pct_harvested_today"] <= 100
            assert p["recent_rate_ha_per_day"] >= 0
            for k in ("projected_50_pct", "projected_75_pct", "projected_90_pct"):
                assert p[k] is None or date.fromisoformat(p[k])
        dem = L["combine_demand"]
        assert len(dem["days"]) == cfg.get("forecast_horizon_days", 14)
        assert all(row["ha"] >= 0 and row["machine_days"] >= 0 for row in dem["days"])
        if d0["chcs"]:
            assert dem["capacity_machines"] == sum(c["machines"] for c in d0["chcs"]["chcs"])
        for z in out["zones"].values():
            assert 0 <= z["pct_harvested_area"] <= 100 and 0 <= z["pct_ready_area"] <= 100
            assert z["mean_latest_ndvi"] is None or -1 <= z["mean_latest_ndvi"] <= 1
        if d0["plans"]:
            assert sum(L["flags"].values()) == n
        if d0["usda"]:
            assert L["usda_comparison"] is None or set(L["usda_comparison"]["crops"]) <= {"corn", "soybean"}
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", L["narrative"].strip()) if x]
        assert len(sentences) == 3
        for row in out["fields"].values():
            assert len(json.dumps(row)) < 1000
