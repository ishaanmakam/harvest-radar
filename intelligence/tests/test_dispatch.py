"""Tests for the MILP dispatch (intelligence/dispatch_milp.py) and its wiring in routing.py."""
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

import dispatch_milp
import routing
from demand import build_jobs

ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 10, 17)
REGION = {"as_of": "2026-10-17", "machine_rate_ha_per_day": 3.2, "max_service_km": 15,
          "baseline_request_lag_days": 2, "forecast_horizon_days": 14, "sowing_window_days": 15}


def job(fid, zone, ha, open_day, deadline_day):
    return {"field_id": fid, "zone_id": zone, "ha": ha, "kind": "backlog",
            "ready": AS_OF + timedelta(days=open_day), "deadline": AS_OF + timedelta(days=deadline_day)}


def plan_rows(plan):
    return sorted((k[0], k[1], k[2], v[0], round(v[1], 3)) for k, v in plan.items())


class TestTinyInstances:
    def test_one_machine_serves_both_jobs_in_deadline_order(self):
        chcs = [{"chc_id": "C1", "name": "c1", "lon": 75.82, "lat": 30.25, "machines": 1}]
        zones = {"A": (75.80, 30.25), "B": (75.85, 30.25)}
        jobs = [job("F1", "A", 3.2, 0, 0), job("F2", "B", 3.2, 0, 1)]
        res = dispatch_milp.solve(jobs, chcs, zones, REGION)
        assert res["outcome"]["ha_served_in_window"] == pytest.approx(6.4)
        assert res["outcome"]["pct_served"] == 100.0
        assert plan_rows(res["plan"]) == [("2026-10-17", "C1", "A", 1, 3.2), ("2026-10-18", "C1", "B", 1, 3.2)]

    def test_milp_beats_nearest_first_greedy_when_a_zone_is_reachable_by_one_chc_only(self):
        # C1 reaches A and B, C2 reaches only A. Greedy sends C1 (nearest) to the bigger job in A and strands B.
        chcs = [{"chc_id": "C1", "name": "c1", "lon": 75.82, "lat": 30.25, "machines": 1},
                {"chc_id": "C2", "name": "c2", "lon": 75.70, "lat": 30.25, "machines": 1}]
        zones = {"A": (75.80, 30.25), "B": (75.90, 30.25)}
        jobs = [job("F1", "A", 3.2, 0, 0), job("F2", "B", 3.0, 0, 0)]
        greedy, _, _ = routing.simulate(jobs, chcs, zones, REGION, "optimized")
        milp = dispatch_milp.solve(jobs, chcs, zones, REGION)
        assert greedy["ha_served_in_window"] == pytest.approx(3.2)
        assert milp["outcome"]["ha_served_in_window"] == pytest.approx(6.2)
        assert plan_rows(milp["plan"]) == [("2026-10-17", "C1", "B", 1, 3.0), ("2026-10-17", "C2", "A", 1, 3.2)]

    def test_no_idle_machines_in_plan(self):
        chcs = [{"chc_id": "C1", "name": "c1", "lon": 75.82, "lat": 30.25, "machines": 5}]
        zones = {"A": (75.80, 30.25)}
        jobs = [job("F1", "A", 4.0, 0, 3)]
        res = dispatch_milp.solve(jobs, chcs, zones, REGION)
        assert res["outcome"]["ha_served_in_window"] == pytest.approx(4.0)
        assert sum(v[0] for v in res["plan"].values()) == 2          # 4 ha needs 2 machine-days, not 5x4

    def test_unreachable_zone_is_missed_not_crashed(self):
        chcs = [{"chc_id": "C1", "name": "c1", "lon": 75.82, "lat": 30.25, "machines": 1}]
        zones = {"FAR": (76.20, 30.25)}
        res = dispatch_milp.solve([job("F1", "FAR", 3.0, 0, 2)], chcs, zones, REGION)
        assert res["outcome"]["ha_missed_window"] == pytest.approx(3.0)
        assert res["plan"] == {}


def mock_inputs():
    region = routing.load_region()
    chcs = routing.load_chcs()
    data = ROOT / "data" / "mock"
    status = json.loads((data / "status.json").read_text())
    fields = json.loads((data / "fields.geojson").read_text())
    zones = json.loads((data / "zones.geojson").read_text())
    cents = {z["properties"]["zone_id"]: tuple(z["properties"]["centroid"]) for z in zones["features"]}
    as_of = date.fromisoformat(region["as_of"])
    last_ready = as_of + timedelta(days=region["forecast_horizon_days"] - 1)
    jobs = [j for j in build_jobs(status, fields, region) if j["ready"] <= last_ready]
    return region, chcs, cents, jobs


class TestMockData:
    def test_plan_is_feasible_and_not_worse_than_greedy(self):
        region, chcs, cents, jobs = mock_inputs()
        greedy, _, _ = routing.simulate(jobs, chcs, cents, region, "optimized")
        res = dispatch_milp.solve(jobs, chcs, cents, region, time_limit_s=20)
        assert res is not None
        assert res["outcome"]["ha_served_in_window"] >= greedy["ha_served_in_window"] - 1e-6
        cap = {c["chc_id"]: c["machines"] for c in chcs}
        used = {}
        for (day, chc, zone), (m, ha) in res["plan"].items():
            assert isinstance(m, int) and m >= 1
            used[(day, chc)] = used.get((day, chc), 0) + m
            assert ha <= m * region["machine_rate_ha_per_day"] + 1e-6
        assert all(n <= cap[chc] for (day, chc), n in used.items())
        for (day, chc, zone) in res["plan"]:
            c = next(c for c in chcs if c["chc_id"] == chc)
            assert routing.haversine_km(c["lon"], c["lat"], *cents[zone]) <= region["max_service_km"]
        assert res["info"]["solve_s"] < 20


@pytest.fixture
def mock_copy(tmp_path):
    for name in ("status.json", "fields.geojson", "zones.geojson"):
        shutil.copy(ROOT / "data" / "mock" / name, tmp_path / name)
    return tmp_path


class TestRoutingWiring:
    def test_summary_reports_milp_greedy_and_baseline(self, mock_copy):
        out = routing.run(mock_copy)
        sm = out["summary"]
        assert sm["method"] == "milp"
        assert set(sm) >= {"ha_demand", "optimized", "greedy", "milp", "baseline", "method", "solver"}
        assert sm["optimized"] == sm["milp"]
        assert sm["optimized"]["ha_served_in_window"] >= sm["greedy"]["ha_served_in_window"] - 0.1
        assert sm["greedy"]["ha_served_in_window"] > sm["baseline"]["ha_served_in_window"]
        assert all(isinstance(p["machines"], int) for p in out["plan"])
        json.dumps(out)   # everything JSON-serialisable (no numpy scalars)

    def test_falls_back_to_greedy_when_solver_gives_nothing(self, mock_copy, monkeypatch):
        monkeypatch.setattr(dispatch_milp, "solve", lambda *a, **k: None)
        out = routing.run(mock_copy)
        sm = out["summary"]
        assert sm["method"] == "greedy"
        assert sm["optimized"] == sm["greedy"]
        assert sm["milp"] is None

    def test_solver_flag_greedy_skips_milp(self, mock_copy):
        out = routing.run(mock_copy, solver="greedy")
        assert out["summary"]["method"] == "greedy"


class TestPlanStats:
    def test_machine_days_and_travel_km(self):
        chcs = [{"chc_id": "C1", "name": "c1", "lon": 75.82, "lat": 30.25, "machines": 3}]
        cents = {"A": (75.80, 30.25)}
        km = routing.haversine_km(75.82, 30.25, 75.80, 30.25)
        plan = {("2026-10-17", "C1", "A"): [2, 6.4], ("2026-10-18", "C1", "A"): [1, 3.2]}
        st = routing.plan_stats(plan, chcs, cents)
        assert st["machine_days"] == 3
        assert st["travel_km"] == pytest.approx(round(3 * km, 1))

    def test_summary_outcomes_carry_travel_and_milp_drives_less_than_greedy(self, mock_copy):
        sm = routing.run(mock_copy)["summary"]
        for key in ("optimized", "greedy", "milp", "baseline"):
            assert {"machine_days", "travel_km"} <= set(sm[key])
        assert sm["milp"]["travel_km"] <= sm["greedy"]["travel_km"]
