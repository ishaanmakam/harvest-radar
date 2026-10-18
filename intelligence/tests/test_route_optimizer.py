"""route_optimizer.py: a drivable order for the officer's day on top of route.json (which it never changes).
  calls first from the office (5 min each), then the visits tier by tier (fire, flood, harvest_before_rain, burn_scar
  as listed), nearest neighbour inside a tier, 2-opt on that tier's segment, 40 km/h and 20 min per visit.
Synthetic geometry: fields on one meridian, so every distance is a plain lat difference:
  0.01 deg of latitude = pi * 6371 / 180 * 0.01 = 1.11195 km (haversine along a meridian is exact)."""
import json
import math
import re
from pathlib import Path

import pytest

import route_optimizer as ro

KM_PER_DEG = math.pi * 6371.0 / 180.0        # 111.195 km
LON, LAT = -93.47, 42.00                      # the test depot
CONFIG = {"as_of": "2026-10-17", "bbox": [-93.52, 41.98, -93.42, 42.07]}
DEPOT = {"chcs": [{"chc_id": "CHC-T", "name": "Test depot", "lon": LON, "lat": LAT, "machines": 1}]}
HHMM = re.compile(r"^\d{2}:\d{2}( \+\d+d)?$")


def square(fid, zone, lon, lat, half=0.002, crop="corn"):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": 20.0, "crop": crop, "source": "test"}}


def fields_at(spec):
    """spec = {field_id: (lat_offset_deg, zone_id)}; every field sits on the depot's meridian."""
    return {"type": "FeatureCollection",
            "features": [square(f, z, LON, LAT + off) for f, (off, z) in spec.items()]}


def fields_xy(spec):
    """spec = {field_id: (lon, lat, zone_id)}: fields anywhere in the box (centroid = the given point)."""
    return {"type": "FeatureCollection",
            "features": [square(f, z, lon, lat) for f, (lon, lat, z) in spec.items()]}


def stop(fid, action, kind):
    return {"farm_id": fid, "action": action, "why": "test", "kind": kind,
            "status": "act_now" if action == "visit" else "watch"}


def route(*stops):
    return {"as_of": "2026-10-17", "stops": list(stops)}


def data(fields=None, rt=None, chcs=DEPOT, zones=None):
    return {"fields": fields, "zones": zones, "timeseries": None, "status": None, "weather": {}, "fires": {"detections": []},
            "insights": None, "plans": None, "route": rt, "truth": None, "usda": None, "chcs": chcs}


def km(deg):
    return KM_PER_DEG * deg


class TestEmptyInputs:
    def test_route_none_gives_empty_lists_and_a_note(self):
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), None), CONFIG)
        assert out["key"] == "route"
        assert out["landscape"]["visits"] == [] and out["landscape"]["calls"] == []
        assert out["landscape"]["total_km"] == 0.0 and out["fields"] == {}
        assert out["landscape"]["notes"]

    def test_no_stops_gives_empty_lists_and_a_note(self):
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), route()), CONFIG)
        assert out["landscape"]["visits"] == [] and out["landscape"]["calls"] == []
        assert out["landscape"]["notes"]

    def test_fields_none_keeps_visits_unlocated(self):
        out = ro.analyze(data(None, route(stop("F1", "visit", "fire"))), CONFIG)
        v = out["landscape"]["visits"]
        assert len(v) == 1 and v[0]["farm_id"] == "F1" and v[0]["km_from_previous"] is None
        assert out["landscape"]["total_km"] == 0.0
        assert any("F1" in n for n in out["landscape"]["notes"])

    def test_everything_none_does_not_raise(self):
        out = ro.analyze({k: None for k in ("fields", "zones", "route", "chcs")}, {})
        assert out["key"] == "route" and out["landscape"]["visits"] == []


class TestCalls:
    def test_calls_first_five_minutes_each_no_travel(self):
        # 2 calls: 08:00, 08:05; the officer is done at 08:10; 10 min = 0.17 h
        rt = route(stop("F1", "call", "visible_change"), stop("F2", "call", "establishment"))
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01"), "F2": (0.02, "Z01")}), rt), CONFIG)
        L = out["landscape"]
        assert [c["eta"] for c in L["calls"]] == ["08:00", "08:05"]
        assert [c["order"] for c in L["calls"]] == [1, 2]
        assert L["finish_time"] == "08:10" and L["total_km"] == 0.0
        assert L["total_hours"] == pytest.approx(10 / 60, abs=0.01)
        assert out["fields"]["F2"] == {"call_order": 2, "eta": "08:05", "km_from_previous": None}

    def test_drive_starts_after_the_calls(self):
        # 2 calls end 08:10; F3 is 1.112 km = 1.67 min away -> eta 08:11.67 -> "08:12"
        rt = route(stop("F3", "visit", "fire"), stop("F1", "call", "visible_change"), stop("F2", "call", "stress"))
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01"), "F2": (0.02, "Z01"), "F3": (0.01, "Z01")}), rt), CONFIG)
        L = out["landscape"]
        assert L["departure"] == "08:10"
        assert L["visits"][0]["eta"] == "08:12"
        assert L["finish_time"] == "08:32"


class TestVisitOrder:
    def test_nearest_neighbour_inside_one_tier(self):
        # listed far, near, mid (all one tier) -> near, mid, far
        # km: 1.112, 1.112, 2.224 = 4.448 ; listed order 4.448 + 3.336 + 1.112 = 8.896 ; saved 4.448
        # etas at 40 km/h with 20 min stops: 08:01.7 -> "08:02", 08:23.3 -> "08:23", 08:46.7 -> "08:47", end 09:06.7
        flds = fields_at({"FAR": (0.04, "Z01"), "NEAR": (0.01, "Z01"), "MID": (0.02, "Z01")})
        rt = route(stop("FAR", "visit", "harvest_before_rain"), stop("NEAR", "visit", "harvest_before_rain"),
                   stop("MID", "visit", "harvest_before_rain"))
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert [v["farm_id"] for v in L["visits"]] == ["NEAR", "MID", "FAR"]
        assert [v["order"] for v in L["visits"]] == [1, 2, 3]
        assert [v["km_from_previous"] for v in L["visits"]] == pytest.approx([km(0.01), km(0.01), km(0.02)], abs=0.002)
        assert L["total_km"] == pytest.approx(km(0.04), abs=0.002)
        assert L["listed_order_km"] == pytest.approx(km(0.08), abs=0.002)
        assert L["km_saved"] == pytest.approx(km(0.04), abs=0.002)
        assert [v["eta"] for v in L["visits"]] == ["08:02", "08:23", "08:47"]
        assert L["finish_time"] == "09:07"
        assert L["total_hours"] == pytest.approx(66.67 / 60, abs=0.01)
        assert out["fields"]["FAR"]["visit_order"] == 3 and out["fields"]["FAR"]["eta"] == "08:47"
        assert out["fields"]["FAR"]["km_from_previous"] == pytest.approx(km(0.02), abs=0.002)
        assert L["visits"][0]["lon"] == pytest.approx(LON, abs=1e-6) and L["visits"][0]["lat"] == pytest.approx(LAT + 0.01, abs=1e-6)

    def test_two_opt_repairs_a_greedy_order(self):
        # A +0.01, B -0.02, C +0.045 from the depot. Greedy: A (1.112), then B (3.336) nearer than C (3.892), then
        # C (7.228) = 11.676. 2-opt reverses [A, B] -> B, A, C = 2.224 + 3.336 + 3.892 = 9.452.
        # listed A, C, B = 1.112 + 3.892 + 7.228 = 12.232 ; saved 2.780
        flds = fields_at({"A": (0.01, "Z01"), "B": (-0.02, "Z01"), "C": (0.045, "Z01")})
        rt = route(stop("A", "visit", "flood"), stop("C", "visit", "flood"), stop("B", "visit", "flood"))
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert [v["farm_id"] for v in L["visits"]] == ["B", "A", "C"]
        assert L["total_km"] == pytest.approx(km(0.085), abs=0.002)
        assert L["listed_order_km"] == pytest.approx(km(0.11), abs=0.002)
        assert L["km_saved"] == pytest.approx(km(0.025), abs=0.002)

    def test_helpers_nearest_neighbour_then_two_opt(self):
        # same geometry as above, on the helpers themselves: index 0 = depot, 1 = A, 2 = B, 3 = C
        dist = ro.distance_matrix([LON] * 4, [LAT, LAT + 0.01, LAT - 0.02, LAT + 0.045])
        assert dist[0, 1] == pytest.approx(km(0.01), abs=0.001) and dist[2, 3] == pytest.approx(km(0.065), abs=0.001)
        nn = ro.nearest_neighbour(dist, 0, [1, 3, 2])
        assert nn == [1, 2, 3]
        assert ro.two_opt(dist, 0, nn) == [2, 1, 3]
        assert ro.path_km(dist, 0, [2, 1, 3]) == pytest.approx(km(0.085), abs=0.002)

    def test_tiers_keep_their_priority_order(self):
        # fire at +0.05 stays first although two harvest_before_rain farms are nearer; inside that tier the
        # nearest to the fire farm (+0.06) comes before the one back near the depot (-0.01)
        flds = fields_at({"FIRE": (0.05, "Z01"), "H1": (-0.01, "Z02"), "H2": (0.06, "Z01")})
        rt = route(stop("FIRE", "visit", "fire"), stop("H1", "visit", "harvest_before_rain"), stop("H2", "visit", "harvest_before_rain"))
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert [v["farm_id"] for v in L["visits"]] == ["FIRE", "H2", "H1"]
        assert [v["kind"] for v in L["visits"]] == ["fire", "harvest_before_rain", "harvest_before_rain"]
        assert L["total_km"] == pytest.approx(km(0.05) + km(0.01) + km(0.07), abs=0.002)
        assert L["km_saved"] >= 0

    def test_two_tiers_listed_km_is_route_json_order_driven_from_the_start(self):
        # fire tier listed FAR +0.04, NEAR +0.01 ; flood tier listed X +0.05, Y +0.02 (all on the meridian).
        # route.json's order from the depot: 0.04 + 0.03 + 0.04 + 0.03 = 0.14 deg = 15.567 km.
        # optimised: NEAR, FAR (0.01 + 0.03) then from FAR: X, Y (0.01 + 0.03) = 0.08 deg = 8.896 km ; saved 0.06 deg.
        # (measuring the flood tier's listed order from the optimised fire end would wrongly give 0.07 + 0.04 = 0.11)
        flds = fields_at({"FAR": (0.04, "Z01"), "NEAR": (0.01, "Z01"), "X": (0.05, "Z01"), "Y": (0.02, "Z01")})
        rt = route(stop("FAR", "visit", "fire"), stop("NEAR", "visit", "fire"), stop("X", "visit", "flood"), stop("Y", "visit", "flood"))
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert [v["farm_id"] for v in L["visits"]] == ["NEAR", "FAR", "X", "Y"]
        assert L["total_km"] == pytest.approx(km(0.08), abs=0.002)
        assert L["listed_order_km"] == pytest.approx(km(0.14), abs=0.002)
        assert L["km_saved"] == pytest.approx(km(0.06), abs=0.002)

    def test_two_tiers_never_drive_longer_than_route_json_order(self):
        # Real-shaped instance: depot (-93.505, 42.062); fire A0 (-93.4416, 42.0677), A1 (-93.517, 42.0172),
        # A2 (-93.4692, 41.9975); flood B0 (-93.4901, 41.9866), B1 (-93.4473, 41.988).
        # route.json's order A0 A1 A2 B0 B1 from the depot = 5.272 + 8.385 + 4.516 + 2.110 + 3.541 = 23.824 km
        # (common.haversine_km). Per-tier greedy + 2-opt gives A1 A2 A0 then B1 B0 = 30.143 km, longer, because the
        # fire tier ends far from the flood tier. The day must then keep the listed order: no kilometres saved.
        depot = {"chcs": [{"chc_id": "CHC-R", "name": "Review depot", "lon": -93.505, "lat": 42.062, "machines": 1}]}
        flds = fields_xy({"A0": (-93.4416, 42.0677, "Z01"), "A1": (-93.517, 42.0172, "Z01"), "A2": (-93.4692, 41.9975, "Z01"),
                          "B0": (-93.4901, 41.9866, "Z01"), "B1": (-93.4473, 41.988, "Z01")})
        rt = route(stop("A0", "visit", "fire"), stop("A1", "visit", "fire"), stop("A2", "visit", "fire"),
                   stop("B0", "visit", "flood"), stop("B1", "visit", "flood"))
        out = ro.analyze(data(flds, rt, chcs=depot), CONFIG)
        L = out["landscape"]
        assert L["listed_order_km"] == pytest.approx(23.824, abs=0.002)
        assert L["total_km"] <= L["listed_order_km"] + 1e-6
        assert L["km_saved"] == pytest.approx(0.0, abs=1e-6)
        assert [v["farm_id"] for v in L["visits"]] == ["A0", "A1", "A2", "B0", "B1"]
        assert [v["kind"] for v in L["visits"]] == ["fire"] * 3 + ["flood"] * 2
        assert L["total_km"] == pytest.approx(23.824, abs=0.002)
        assert [v["km_from_previous"] for v in L["visits"]] == pytest.approx([5.272, 8.385, 4.516, 2.110, 3.541], abs=0.002)
        assert any("listed order" in n for n in L["notes"])

    def test_order_day_helper_falls_back_to_the_listed_tiers(self):
        # same two tiers on the helper: index 0 = depot, 1..3 = A0..A2, 4..5 = B0..B1
        lons = [-93.505, -93.4416, -93.517, -93.4692, -93.4901, -93.4473]
        lats = [42.062, 42.0677, 42.0172, 41.9975, 41.9866, 41.988]
        dist = ro.distance_matrix(lons, lats)
        planned, kept_listed = ro.order_day(dist, 0, [[1, 2, 3], [4, 5]])
        assert kept_listed is True and planned == [[1, 2, 3], [4, 5]]
        assert ro.path_km(dist, 0, [1, 2, 3, 4, 5]) == pytest.approx(23.824, abs=0.002)
        # and the fire tier alone is still re-ordered: A1 A2 A0 (about 17.7 km) beats listed A0 A1 A2 (18.173 km)
        planned, kept_listed = ro.order_day(dist, 0, [[1, 2, 3]])
        assert kept_listed is False and planned == [[2, 3, 1]]
        assert ro.path_km(dist, 0, [2, 3, 1]) < ro.path_km(dist, 0, [1, 2, 3])

    def test_never_worse_than_the_listed_order(self):
        # a tier where the listed order is already the best cannot lose kilometres
        flds = fields_at({"P": (0.01, "Z01"), "Q": (0.02, "Z01"), "R": (0.03, "Z01")})
        rt = route(stop("P", "visit", "flood"), stop("Q", "visit", "flood"), stop("R", "visit", "flood"))
        out = ro.analyze(data(flds, rt), CONFIG)
        assert out["landscape"]["km_saved"] == pytest.approx(0.0, abs=1e-6)
        assert [v["farm_id"] for v in out["landscape"]["visits"]] == ["P", "Q", "R"]

    def test_visit_absent_from_fields_goes_last_in_its_tier(self):
        # V2 has no polygon: it closes its tier with no travel, 20 min after V1's arrival, and is named in a note
        flds = fields_at({"V1": (0.01, "Z01")})
        rt = route(stop("V2", "visit", "fire"), stop("V1", "visit", "fire"))
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert [v["farm_id"] for v in L["visits"]] == ["V1", "V2"]
        assert L["visits"][1]["km_from_previous"] is None and L["visits"][1]["lon"] is None
        assert L["visits"][0]["eta"] == "08:02" and L["visits"][1]["eta"] == "08:22"
        assert out["fields"]["V2"] == {"visit_order": 2, "eta": "08:22", "km_from_previous": None}
        assert any("V2" in n for n in L["notes"])
        assert L["total_km"] == pytest.approx(km(0.01), abs=0.002)


class TestStartPoint:
    def test_first_depot_is_the_start(self):
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), route(stop("F1", "visit", "fire"))), CONFIG)
        assert out["landscape"]["start"] == {"lon": LON, "lat": LAT, "name": "Test depot"}

    def test_depots_as_a_bare_list_work_too(self):
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), route(stop("F1", "visit", "fire")), chcs=DEPOT["chcs"]), CONFIG)
        assert out["landscape"]["start"]["name"] == "Test depot"

    def test_no_depot_falls_back_to_the_bbox_centre(self):
        # bbox centre (-93.47, 42.025); F1 at 42.01 on the same meridian -> 0.015 deg = 1.668 km
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), route(stop("F1", "visit", "fire")), chcs=None), CONFIG)
        st = out["landscape"]["start"]
        assert st["lon"] == pytest.approx(-93.47) and st["lat"] == pytest.approx(42.025) and "bbox" in st["name"]
        assert out["landscape"]["total_km"] == pytest.approx(km(0.015), abs=0.002)

    def test_no_depot_and_no_bbox_starts_at_the_first_visit(self):
        out = ro.analyze(data(fields_at({"F1": (0.01, "Z01")}), route(stop("F1", "visit", "fire")), chcs=None), {"as_of": "2026-10-17"})
        assert out["landscape"]["start"] is None or out["landscape"]["start"]["lat"] == pytest.approx(LAT + 0.01, abs=1e-6)
        assert out["landscape"]["visits"][0]["km_from_previous"] in (None, 0.0)
        assert out["landscape"]["notes"]


class TestDayAndZones:
    def test_long_day_is_named_with_visits_that_fit(self):
        # 40 visits on the depot itself: no travel, 40 x 20 min = 800 min -> 21:20; by 18:00 thirty of them are done
        flds = fields_at({f"F{i:02d}": (0.0, "Z01") for i in range(40)})
        rt = route(*[stop(f"F{i:02d}", "visit", "flood") for i in range(40)])
        out = ro.analyze(data(flds, rt), CONFIG)
        L = out["landscape"]
        assert L["finish_time"] == "21:20" and L["total_hours"] == pytest.approx(800 / 60, abs=0.01)
        assert L["visits_by_day_end"] == 30 and L["day_end"] == "18:00"
        assert any("18:00" in n for n in L["notes"])

    def test_eta_past_midnight_is_marked(self):
        assert ro.clock(8 * 60) == "08:00"
        assert ro.clock(24 * 60 + 5) == "00:05 +1d"
        assert ro.clock(481.6) == "08:02"

    def test_zone_summary_counts_visits_calls_and_km(self):
        flds = fields_at({"A": (0.01, "Z01"), "B": (0.02, "Z02"), "C": (0.03, "Z02")})
        zones = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"zone_id": z, "name": z, "centroid": [LON, LAT]}} for z in ("Z01", "Z02", "Z03")]}
        rt = route(stop("A", "visit", "fire"), stop("B", "visit", "fire"), stop("C", "call", "stress"))
        out = ro.analyze(data(flds, rt, zones=zones), CONFIG)
        Z = out["zones"]
        assert Z["Z01"]["visits"] == 1 and Z["Z01"]["calls"] == 0
        assert Z["Z02"]["visits"] == 1 and Z["Z02"]["calls"] == 1
        assert Z["Z03"]["visits"] == 0 and Z["Z03"]["calls"] == 0
        assert Z["Z01"]["drive_km"] == pytest.approx(km(0.01), abs=0.002)
        assert Z["Z02"]["drive_km"] == pytest.approx(km(0.01), abs=0.002)


class TestContract:
    def test_output_is_json_and_deterministic(self):
        flds = fields_at({"A": (0.01, "Z01"), "B": (-0.02, "Z01"), "C": (0.045, "Z02"), "D": (0.03, "Z02")})
        rt = route(stop("A", "visit", "fire"), stop("C", "visit", "flood"), stop("B", "visit", "flood"), stop("D", "call", "stress"))
        one = ro.analyze(data(flds, rt), CONFIG)
        two = ro.analyze(data(flds, rt), CONFIG, prior={"weather": {}})
        assert json.loads(json.dumps(one)) == json.loads(json.dumps(two))
        for k in ("key", "fields", "landscape", "zones", "assumptions", "method"):
            assert k in one
        for k in ("start", "calls", "visits", "total_km", "listed_order_km", "km_saved", "total_hours", "finish_time", "notes"):
            assert k in one["landscape"]
        assert isinstance(one["method"], str) and one["method"]
        assert all(isinstance(v, str) for v in one["assumptions"].values())
        for v in one["landscape"]["visits"]:
            assert HHMM.match(v["eta"])

    def test_bad_geometry_and_bad_stop_do_not_raise(self):
        flds = fields_at({"A": (0.01, "Z01")})
        flds["features"].append({"type": "Feature", "geometry": None, "properties": {"field_id": "BROKEN", "zone_id": "Z01"}})
        flds["features"].append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": {"field_id": "EMPTY", "zone_id": "Z01"}})
        rt = route(stop("A", "visit", "fire"), stop("BROKEN", "visit", "fire"), {"action": "visit", "kind": "fire"}, stop("EMPTY", "call", "stress"))
        out = ro.analyze(data(flds, rt), CONFIG)
        assert [v["farm_id"] for v in out["landscape"]["visits"]] == ["A", "BROKEN"]
        assert out["landscape"]["calls"][0]["farm_id"] == "EMPTY"
        assert out["landscape"]["notes"]


ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


@pytest.mark.skipif(not (MOCK / "route.json").exists(), reason="data/mock is not present")
def test_mock_season_shapes_and_sanity():
    def load(name, default=None):
        p = MOCK / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    dat = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
           "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
           "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
           "truth": load("truth.json"), "usda": load("usda_crop_progress.json"),
           "chcs": json.loads((ROOT / "config" / "chcs.json").read_text(encoding="utf-8"))}
    config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
    out = ro.analyze(dat, config)
    L = out["landscape"]
    stops = dat["route"]["stops"]
    n_visit = sum(st["action"] == "visit" for st in stops)
    n_call = len(stops) - n_visit
    assert out["key"] == "route"
    assert len(L["visits"]) == n_visit and len(L["calls"]) == n_call
    assert [v["order"] for v in L["visits"]] == list(range(1, n_visit + 1))
    assert [c["order"] for c in L["calls"]] == list(range(1, n_call + 1))
    assert set(out["fields"]) == {st["farm_id"] for st in stops}
    # tiers are kept: the kinds of the optimised visits follow the first-appearance order of the listed visits
    listed_kinds = []
    for st in stops:
        if st["action"] == "visit" and st["kind"] not in listed_kinds:
            listed_kinds.append(st["kind"])
    ranks = [listed_kinds.index(v["kind"]) for v in L["visits"]]
    assert ranks == sorted(ranks)
    assert L["start"]["name"].startswith("Depot North")
    if n_visit:
        assert 0 < L["total_km"] <= L["listed_order_km"] + 1e-6
        assert L["km_saved"] >= -1e-6
        assert L["total_km"] < 400                           # the box is about 8 x 10 km
        assert all(0 <= (v["km_from_previous"] or 0) < 20 for v in L["visits"])
        assert all(HHMM.match(v["eta"]) for v in L["visits"])
        assert all(config["bbox"][0] - 0.01 <= v["lon"] <= config["bbox"][2] + 0.01 for v in L["visits"] if v["lon"] is not None)
    assert 0 < L["total_hours"] < 48
    assert set(out["zones"]) >= {f["properties"]["zone_id"] for f in dat["zones"]["features"]}
    assert sum(z["visits"] for z in out["zones"].values()) == n_visit
    text = json.dumps(out)
    assert "numpy" not in text and "NaN" not in text and "Infinity" not in text
    assert all(len(json.dumps(v)) < 1024 for v in out["fields"].values())
