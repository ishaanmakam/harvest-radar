"""Fixture tests for the decision layer (intelligence/decide.py), per the spec's Testing section:
fire, flood, harvest-before-rain, burn scar, stress, a new visible change, a quiet farm with eight clear checks,
a farm with two failures taking the worse flag, and the route order."""
import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import decide

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 17)
D = date.fromisoformat
KINDS = ["harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change"]


def rec(**kw):
    """A maturing rice field: peak 0.78 on Sep 5, harvest expected Oct 21-27 (not ready yet)."""
    base = {"field_id": "F0001", "zone_id": "Z01", "stage": "maturing", "peak_date": "2026-09-05", "peak_ndvi": 0.78,
            "harvest_date": None, "predicted_harvest_start": "2026-10-21", "predicted_harvest_end": "2026-10-27",
            "sowing_deadline": "2026-11-08", "burn_date": None, "burn_evidence": "none", "confidence": 0.6, "notes": ""}
    base.update(kw)
    return base


QUIET_PASSES = [(D("2026-10-06"), 0.72, 0.40), (D("2026-10-11"), 0.70, 0.38), (D("2026-10-16"), 0.69, 0.37)]
DRY = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 0.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]


def status_of(plan):
    return {c["kind"]: c["status"] for c in plan["checks"]}


def check(plan, kind):
    return next(c for c in plan["checks"] if c["kind"] == kind)


class TestQuietFarm:
    def test_eight_clear_checks_in_table_order_and_fine_flag(self):
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], None, TODAY)
        assert [c["kind"] for c in plan["checks"]] == KINDS
        assert all(c["status"] == "clear" for c in plan["checks"])
        assert plan["flag"] == "fine"
        assert plan["risks"] == []
        assert plan["as_of"] == "2026-10-16"            # the latest clear pass, not today
        assert plan["source"] == "vegetation_index"
        assert plan["farm_id"] == "F0001"
        assert "fine" in plan["today"].lower() or "nothing" in plan["today"].lower()
        for c in plan["checks"]:                        # every row has a detail, even when clear
            assert c["detail"]
            assert c["where"] == ""
            assert c["window"] == {}


class TestFire:
    def test_hotspot_in_last_5_days_is_act_now(self):
        hot = [{"date": "2026-10-15", "satellite": "VIIRS_SNPP_NRT", "confidence": "h"}]
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, hot, None, TODAY)
        assert status_of(plan)["fire"] == "act_now"
        assert plan["flag"] == "act_now"
        assert check(plan, "fire")["window"] == {"start": "2026-10-15", "end": "2026-10-15"}
        assert "fire" in plan["today"].lower()
        assert plan["risks"] == [check(plan, "fire")["detail"]]

    def test_older_hotspot_is_clear(self):
        hot = [{"date": "2026-10-11", "satellite": "VIIRS_SNPP_NRT", "confidence": "h"}]
        assert status_of(decide.farm_plan(rec(), QUIET_PASSES, DRY, hot, None, TODAY))["fire"] == "clear"


class TestFlood:
    def test_water_signature_in_latest_pass_is_act_now(self):
        passes = QUIET_PASSES[:2] + [(D("2026-10-16"), -0.08, 0.10)]
        plan = decide.farm_plan(rec(), passes, DRY, [], None, TODAY)
        assert status_of(plan)["flood"] == "act_now"
        assert plan["flag"] == "act_now"

    def test_perceptron_flood_cue_wins_and_sets_source(self):
        insight = {"flood": {"kind": "flood", "observed": True, "date": "2026-10-16", "where": "north-east corner",
                             "detail": "standing water on ~30% of the field", "confidence": 0.8}}
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], insight, TODAY)
        assert status_of(plan)["flood"] == "act_now"
        assert check(plan, "flood")["where"] == "north-east corner"
        assert plan["source"] == "perceptron"

    def test_perceptron_saw_no_water_is_clear_but_still_perceptron_sourced(self):
        insight = {"flood": {"kind": "flood", "observed": False, "date": "2026-10-16", "where": "", "detail": "", "confidence": 0.9}}
        passes = QUIET_PASSES[:2] + [(D("2026-10-16"), -0.08, 0.10)]      # index cue would say flood
        plan = decide.farm_plan(rec(), passes, DRY, [], insight, TODAY)
        assert status_of(plan)["flood"] == "clear"
        assert plan["source"] == "perceptron"

    def test_stale_perceptron_flood_is_not_current(self):
        insight = {"flood": {"kind": "flood", "observed": True, "date": "2026-10-06", "where": "", "detail": "", "confidence": 0.7}}
        assert status_of(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], insight, TODAY))["flood"] == "clear"


class TestHarvestBeforeRain:
    def test_ready_crop_and_heavy_rain_within_3_days_is_act_now(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 2.0), (D("2026-10-19"), 18.0), (D("2026-10-20"), 1.0)]
        plan = decide.farm_plan(ready, QUIET_PASSES, wet, [], None, TODAY)
        st = status_of(plan)
        assert st["harvest_ready"] == "watch"
        assert st["harvest_before_rain"] == "act_now"
        assert st["establishment"] == "clear"
        assert plan["flag"] == "act_now"
        assert check(plan, "harvest_before_rain")["window"] == {"start": "2026-10-19", "end": "2026-10-19"}
        assert "18" in check(plan, "harvest_before_rain")["detail"]
        assert "harvest" in plan["today"].lower()

    def test_ready_crop_with_light_rain_is_only_watch(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        light = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 14.0), (D("2026-10-19"), 0.0), (D("2026-10-20"), 0.0)]
        plan = decide.farm_plan(ready, QUIET_PASSES, light, [], None, TODAY)
        assert status_of(plan)["harvest_before_rain"] == "clear"
        assert plan["flag"] == "watch"
        assert check(plan, "harvest_ready")["window"] == {"start": "2026-10-17", "end": "2026-10-21"}

    def test_heavy_rain_beyond_3_days_does_not_count(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        later = DRY + [(D("2026-10-21"), 30.0)]
        assert status_of(decide.farm_plan(ready, QUIET_PASSES, later, [], None, TODAY))["harvest_before_rain"] == "clear"

    def test_heavy_rain_but_crop_not_ready_is_clear(self):
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        assert status_of(decide.farm_plan(rec(), QUIET_PASSES, wet, [], None, TODAY))["harvest_before_rain"] == "clear"


class TestEstablishment:
    def test_never_greened_is_watch_and_readiness_clear(self):
        weak = rec(stage="unknown", peak_ndvi=0.41, predicted_harvest_start=None, predicted_harvest_end=None,
                   notes="never reached crop-level greenness (not paddy?)")
        passes = [(D("2026-10-06"), 0.38, 0.20), (D("2026-10-11"), 0.40, 0.21), (D("2026-10-16"), 0.39, 0.20)]
        plan = decide.farm_plan(weak, passes, DRY, [], None, TODAY)
        st = status_of(plan)
        assert st["establishment"] == "watch"
        assert st["harvest_ready"] == "clear"
        assert plan["flag"] == "watch"


class TestBurnScar:
    harvested = dict(stage="burned", harvest_date="2026-10-04", predicted_harvest_start=None, predicted_harvest_end=None,
                     burn_evidence="scar", burn_date="2026-10-16")

    def test_scar_dated_latest_pass_is_act_now(self):
        passes = [(D("2026-10-06"), 0.24, 0.08), (D("2026-10-11"), 0.23, 0.07), (D("2026-10-16"), 0.11, -0.22)]
        plan = decide.farm_plan(rec(**self.harvested), passes, DRY, [], None, TODAY)
        assert status_of(plan)["burn_scar"] == "act_now"
        assert check(plan, "burn_scar")["window"] == {"start": "2026-10-16", "end": "2026-10-16"}
        assert plan["flag"] == "act_now"

    def test_older_scar_is_watch(self):
        passes = [(D("2026-10-06"), 0.11, -0.22), (D("2026-10-11"), 0.10, -0.20), (D("2026-10-16"), 0.11, -0.19)]
        plan = decide.farm_plan(rec(**dict(self.harvested, burn_date="2026-10-06")), passes, DRY, [], None, TODAY)
        assert status_of(plan)["burn_scar"] == "watch"
        assert check(plan, "burn_scar")["window"]["start"] == "2026-10-06"
        assert plan["flag"] == "watch"

    def test_hotspot_only_evidence_is_no_scar(self):
        passes = [(D("2026-10-06"), 0.24, 0.08), (D("2026-10-11"), 0.23, 0.07), (D("2026-10-16"), 0.22, 0.08)]
        r = rec(**dict(self.harvested, burn_evidence="hotspot", burn_date="2026-10-07"))
        assert status_of(decide.farm_plan(r, passes, DRY, [], None, TODAY))["burn_scar"] == "clear"


class TestStress:
    def test_decline_without_harvest_is_watch(self):
        passes = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]
        plan = decide.farm_plan(rec(), passes, DRY, [], None, TODAY)
        assert status_of(plan)["stress"] == "watch"
        assert check(plan, "stress")["window"] == {"start": "2026-10-11", "end": "2026-10-16"}
        assert plan["flag"] == "watch"

    def test_harvest_collapse_is_not_stress(self):
        passes = [(D("2026-10-06"), 0.70, 0.40), (D("2026-10-11"), 0.62, 0.35), (D("2026-10-16"), 0.22, 0.08)]
        r = rec(stage="harvested", harvest_date="2026-10-13", predicted_harvest_start=None, predicted_harvest_end=None)
        assert status_of(decide.farm_plan(r, passes, DRY, [], None, TODAY))["stress"] == "clear"


class TestVisibleChange:
    def test_harvest_since_previous_pass_is_watch(self):
        passes = [(D("2026-10-06"), 0.70, 0.40), (D("2026-10-11"), 0.62, 0.35), (D("2026-10-16"), 0.22, 0.08)]
        r = rec(stage="harvested", harvest_date="2026-10-13", predicted_harvest_start=None, predicted_harvest_end=None)
        plan = decide.farm_plan(r, passes, DRY, [], None, TODAY)
        st = status_of(plan)
        assert st["visible_change"] == "watch"
        assert st["harvest_ready"] == "clear"
        assert check(plan, "visible_change")["window"] == {"start": "2026-10-11", "end": "2026-10-16"}

    def test_old_harvest_is_no_longer_a_change(self):
        passes = [(D("2026-10-06"), 0.22, 0.08), (D("2026-10-11"), 0.22, 0.08), (D("2026-10-16"), 0.23, 0.08)]
        r = rec(stage="harvested", harvest_date="2026-10-01", predicted_harvest_start=None, predicted_harvest_end=None)
        assert status_of(decide.farm_plan(r, passes, DRY, [], None, TODAY))["visible_change"] == "clear"

    def test_perceptron_dated_change_with_place(self):
        insight = {"visible_change": {"kind": "visible_change", "observed": True, "date": "2026-10-14", "where": "western half",
                                      "detail": "cut between Oct 11 and Oct 16", "confidence": 0.85}}
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], insight, TODAY)
        assert status_of(plan)["visible_change"] == "watch"
        assert check(plan, "visible_change")["where"] == "western half"
        assert plan["source"] == "perceptron"

    def test_perceptron_change_before_previous_pass_does_not_count(self):
        insight = {"visible_change": {"kind": "visible_change", "observed": True, "date": "2026-10-09", "where": "", "detail": "", "confidence": 0.6}}
        assert status_of(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], insight, TODAY))["visible_change"] == "clear"


class TestPerceptronBurnScar:
    def test_perceptron_scar_confirms_and_dates_a_scar_the_index_missed(self):
        burned = rec(stage="harvested", harvest_date="2026-10-04", predicted_harvest_start=None, predicted_harvest_end=None)
        passes = [(D("2026-10-06"), 0.24, 0.08), (D("2026-10-11"), 0.23, 0.07), (D("2026-10-16"), 0.22, 0.06)]
        insight = {"burn_scar": {"kind": "burn_scar", "observed": True, "date": "2026-10-16", "where": "south strip",
                                 "detail": "dark burn scar along the southern bund", "confidence": 0.9}}
        plan = decide.farm_plan(burned, passes, DRY, [], insight, TODAY)
        assert status_of(plan)["burn_scar"] == "act_now"
        assert check(plan, "burn_scar")["where"] == "south strip"
        assert "perceptron" in check(plan, "burn_scar")["detail"].lower()


class TestInsightsFile:
    def test_run_reads_field_cues_from_insights_json(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "timeseries.json", "weather.json", "fires.json", "fields.geojson"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        status = json.loads((data / "status.json").read_text())
        standing = next(r["field_id"] for r in status["fields"] if r["stage"] == "maturing")
        insights = json.loads((data / "insights.json").read_text())
        insights["as_of"] = "2026-10-16"
        insights["verdict"] = "perceptron"
        insights["field_cues"] = [{"field_id": standing, "kind": "stress", "observed": True, "date": "2026-10-16",
                                   "where": "east half", "detail": "browning on the eastern half", "confidence": 0.7},
                                  {"field_id": "F0002", "kind": "flood", "observed": False, "date": "2026-10-16",
                                   "where": "", "detail": "", "confidence": 0.9}]
        (tmp_path / "insights.json").write_text(json.dumps(insights))
        plans, _ = decide.run(tmp_path)
        by_id = {p["farm_id"]: p for p in plans["plans"]}
        assert by_id[standing]["source"] == "perceptron"
        assert status_of(by_id[standing])["stress"] == "watch"
        assert check(by_id[standing], "stress")["where"] == "east half"
        assert by_id["F0002"]["source"] == "perceptron"
        assert plans["vision_verdict"] == "perceptron"
        assert sum(p["source"] == "perceptron" for p in plans["plans"]) == 2


class TestTwoFailures:
    def test_both_checks_kept_and_worse_flag_wins(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        passes = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]  # also stress
        plan = decide.farm_plan(ready, passes, wet, [], None, TODAY)
        st = status_of(plan)
        assert st["harvest_before_rain"] == "act_now" and st["stress"] == "watch" and st["harvest_ready"] == "watch"
        assert plan["flag"] == "act_now"
        assert len(plan["checks"]) == 8
        assert len(plan["risks"]) == 3


class TestRoute:
    def test_stops_follow_spec_order_and_fine_farms_are_not_stops(self):
        ready = dict(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        flood_passes = QUIET_PASSES[:2] + [(D("2026-10-16"), -0.08, 0.10)]
        new_scar = [(D("2026-10-06"), 0.24, 0.08), (D("2026-10-11"), 0.23, 0.07), (D("2026-10-16"), 0.11, -0.22)]
        old_scar = [(D("2026-10-06"), 0.11, -0.22), (D("2026-10-11"), 0.10, -0.20), (D("2026-10-16"), 0.11, -0.19)]
        stress = [(D("2026-10-06"), 0.74, 0.42), (D("2026-10-11"), 0.72, 0.40), (D("2026-10-16"), 0.58, 0.30)]
        cut = [(D("2026-10-06"), 0.70, 0.40), (D("2026-10-11"), 0.62, 0.35), (D("2026-10-16"), 0.22, 0.08)]
        burned = dict(stage="burned", harvest_date="2026-10-04", predicted_harvest_start=None,
                      predicted_harvest_end=None, burn_evidence="scar")
        farms = {
            "quiet": decide.farm_plan(rec(field_id="F0010"), QUIET_PASSES, DRY, [], None, TODAY),
            "old_scar": decide.farm_plan(rec(field_id="F0009", **dict(burned, burn_date="2026-10-06")), old_scar, DRY, [], None, TODAY),
            "estab": decide.farm_plan(rec(field_id="F0008", stage="unknown", peak_ndvi=0.4, predicted_harvest_start=None,
                                          predicted_harvest_end=None), QUIET_PASSES, DRY, [], None, TODAY),
            "ready": decide.farm_plan(rec(field_id="F0007", **ready), QUIET_PASSES, DRY, [], None, TODAY),
            "change": decide.farm_plan(rec(field_id="F0006", stage="harvested", harvest_date="2026-10-13",
                                           predicted_harvest_start=None, predicted_harvest_end=None), cut, DRY, [], None, TODAY),
            "stress": decide.farm_plan(rec(field_id="F0005"), stress, DRY, [], None, TODAY),
            "new_scar": decide.farm_plan(rec(field_id="F0004", **dict(burned, burn_date="2026-10-16")), new_scar, DRY, [], None, TODAY),
            "rain": decide.farm_plan(rec(field_id="F0003", **ready), QUIET_PASSES, wet, [], None, TODAY),
            "flood": decide.farm_plan(rec(field_id="F0002"), flood_passes, DRY, [], None, TODAY),
            "fire": decide.farm_plan(rec(field_id="F0001"), QUIET_PASSES, DRY,
                                     [{"date": "2026-10-16", "satellite": "VIIRS_NOAA20_NRT", "confidence": "n"}], None, TODAY),
        }
        route = decide.build_route(list(farms.values()), TODAY)
        assert [s["farm_id"] for s in route["stops"]] == ["F0001", "F0002", "F0003", "F0004", "F0005", "F0006", "F0007", "F0008", "F0009"]
        assert [s["action"] for s in route["stops"]] == ["visit"] * 4 + ["call"] * 5
        assert all(s["why"] for s in route["stops"])
        assert route["as_of"] == "2026-10-17"

    def test_two_failures_farm_is_placed_by_its_most_urgent_check(self):
        ready = rec(stage="ready", predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-21")
        wet = [(D("2026-10-17"), 0.0), (D("2026-10-18"), 20.0)]
        hot = [{"date": "2026-10-16", "satellite": "VIIRS_SNPP_NRT", "confidence": "h"}]
        both = decide.farm_plan(ready, QUIET_PASSES, wet, hot, None, TODAY)
        flood_only = decide.farm_plan(rec(field_id="F0002"), QUIET_PASSES[:2] + [(D("2026-10-16"), -0.08, 0.10)], DRY, [], None, TODAY)
        route = decide.build_route([flood_only, both], TODAY)
        assert [s["farm_id"] for s in route["stops"]] == ["F0001", "F0002"]      # fire outranks flood
        assert "hotspot" in route["stops"][0]["why"].lower() or "fire" in route["stops"][0]["why"].lower()


class TestMockRun:
    def test_run_on_mock_writes_valid_plans_and_route(self):
        plans, route = decide.run(ROOT / "data" / "mock")
        data = ROOT / "data" / "mock"
        for name, doc in (("plans", plans), ("route", route)):
            schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())
            assert not list(Draft202012Validator(schema).iter_errors(doc)), name
            assert (data / f"{name}.json").exists()
        fields = json.loads((data / "fields.geojson").read_text())["features"]
        assert len(plans["plans"]) == len(fields)
        assert all(len(p["checks"]) == 8 for p in plans["plans"])
        flags = {p["flag"] for p in plans["plans"]}
        assert flags == {"fine", "watch", "act_now"}                      # the stand-in season exercises every flag
        stop_ids = [s["farm_id"] for s in route["stops"]]
        assert len(stop_ids) == len(set(stop_ids))
        assert set(stop_ids) == {p["farm_id"] for p in plans["plans"] if p["flag"] != "fine"}

    def test_invalid_output_does_not_replace_previous_files(self, tmp_path, monkeypatch):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "timeseries.json", "weather.json", "fires.json", "fields.geojson", "insights.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        (tmp_path / "plans.json").write_text("{\"previous\": true}")
        monkeypatch.setattr(decide, "build_route", lambda plans, today: {"stops": "not-a-list"})
        with pytest.raises(ValueError):
            decide.run(tmp_path)
        assert json.loads((tmp_path / "plans.json").read_text()) == {"previous": True}
        assert not (tmp_path / "route.json").exists()
