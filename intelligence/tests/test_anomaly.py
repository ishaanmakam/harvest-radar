"""anomaly.py: which fields behave unlike their neighbours. Small hand-built cohorts pin the robust z math and each
anomaly type; the edge cases return None with a note instead of raising; one read-only run on data/mock checks shapes."""
import json
from pathlib import Path

import pytest

import anomaly

ROOT = Path(__file__).resolve().parents[2]
CONFIG = {"as_of": "2026-10-17", "thresholds": {"peak_ndvi_min": 0.55}}
TWO = ["2026-10-11", "2026-10-16"]
THREE = ["2026-10-06", "2026-10-11", "2026-10-16"]


def ts(dates, series):
    """timeseries.json from fid -> ndvi list (nbr and clear_frac are not read by the module)."""
    return {"source": "test", "dates": dates,
            "fields": {fid: {"ndvi": v, "nbr": [None] * len(v), "clear_frac": [1.0] * len(v)} for fid, v in series.items()}}


def fields(props):
    """fields.geojson from (field_id, zone_id, crop) triples."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None,
         "properties": {"field_id": f, "zone_id": z, "crop": c, "area_ha": 10.0, "source": "test"}} for f, z, c in props]}


def status(recs):
    """status.json from partial records (field_id, zone_id, stage, harvest_date)."""
    return {"as_of": "2026-10-17", "peak_to_harvest_days": 60.0, "peak_to_harvest_days_by_crop": {},
            "fields": [{"field_id": r["field_id"], "zone_id": r.get("zone_id", "Z01"), "stage": r.get("stage", "ready"),
                        "peak_date": None, "peak_ndvi": None, "harvest_date": r.get("harvest_date"),
                        "predicted_harvest_start": None, "predicted_harvest_end": None, "sowing_deadline": None,
                        "burn_date": None, "burn_evidence": "none", "confidence": 0.5, "notes": ""} for r in recs]}


def data(fields_doc, ts_doc, status_doc=None):
    return {"fields": fields_doc, "zones": None, "timeseries": ts_doc, "status": status_doc, "weather": {},
            "fires": {"detections": []}, "insights": None, "plans": None, "route": None, "truth": None,
            "usda": None, "chcs": None}


def corn(zone="Z01", *ids):
    return [(f, zone, "corn") for f in ids]


# Five corn fields whose NDVI is the same on every pass: A 0.80, B 0.78, C 0.82, D 0.79, E 0.50.
#   median 0.79; |dev| = 0.01, 0.01, 0.03, 0.00, 0.29 -> MAD 0.01; scale = 1.4826 * 0.01 + 0.02 = 0.034826
#   z(E) = -0.29 / 0.034826 = -8.33   z(C) = 0.03 / 0.034826 = 0.86   z(A) = 0.29   z(D) = 0.00
FLAT = {"A": 0.80, "B": 0.78, "C": 0.82, "D": 0.79, "E": 0.50}


def flat(n_passes, values=FLAT):
    return {fid: [v] * n_passes for fid, v in values.items()}


class TestRobustZ:
    def test_z_is_distance_from_the_cohort_median_in_mad_units(self):
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(THREE, flat(3))), CONFIG)
        f = out["fields"]
        assert f["E"]["latest_z"] == pytest.approx(-8.33, abs=0.01)
        assert f["C"]["latest_z"] == pytest.approx(0.86, abs=0.01)
        assert f["A"]["latest_z"] == pytest.approx(0.29, abs=0.01)
        assert f["D"]["latest_z"] == pytest.approx(0.0, abs=0.01)
        assert f["E"]["latest_pass"] == "2026-10-16"
        assert f["E"]["cohort"] == "corn/Z01"
        assert f["E"]["n_passes"] == 3

    def test_pass_with_too_few_cohort_values_gets_no_z(self):
        series = flat(3)
        for fid in "ABC":                       # only D and E are clear on the last pass: no median there
            series[fid][2] = None
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(THREE, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["latest_pass"] == "2026-10-11" and e["n_passes"] == 3 and e["n_compared"] == 2
        assert e["latest_z"] == pytest.approx(-8.33, abs=0.01)
        assert out["fields"]["D"]["n_compared"] == 2


class TestAnomalyTypes:
    def test_chronic_low_is_z_at_or_below_minus_1_5_on_the_last_two_passes(self):
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(THREE, flat(3))), CONFIG)
        e, a = out["fields"]["E"], out["fields"]["A"]
        assert e["persistent_low"] is True
        assert e["anomaly_type"] == "chronic_low"
        assert e["sudden_drop"] is False and e["outlier_high"] is False
        assert a["anomaly_type"] == "none" and a["persistent_low"] is False
        assert "below" in e["note"].lower()
        # one flagged field of five = 20%: isolated; its neighbours see one flagged field
        assert e["event_scope"] == "isolated" and e["neighbors_flagged"] == 0
        assert a["event_scope"] is None and a["neighbors_flagged"] == 1
        # 1 = greenest latest NDVI among the zone's corn: C 0.82, A 0.80, D 0.79, B 0.78, E 0.50
        assert [out["fields"][x]["rank_in_zone"] for x in "ABCDE"] == [2, 4, 1, 3, 5]
        assert out["fields"]["A"]["zone_crop_n"] == 5

    def test_sudden_drop_when_the_cohort_holds_steady(self):
        # pass 1: A 0.80 B 0.78 C 0.82 D 0.79 E 0.81 -> median 0.80, MAD 0.01, z(E) = 0.01 / 0.034826 = 0.29
        # pass 2: E falls to 0.55            -> median 0.79, MAD 0.01, z(E) = -0.24 / 0.034826 = -6.89
        # z fell 7.18 >= 1.5 while the median fell 0.01 < 0.05
        series = {"A": [0.80, 0.80], "B": [0.78, 0.78], "C": [0.82, 0.82], "D": [0.79, 0.79], "E": [0.81, 0.55]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["prev_z"] == pytest.approx(0.29, abs=0.01)
        assert e["latest_z"] == pytest.approx(-6.89, abs=0.01)
        assert e["sudden_drop"] is True and e["persistent_low"] is False
        assert e["anomaly_type"] == "sudden_drop"
        assert "2026-10-11" in e["note"] and "2026-10-16" in e["note"]

    def test_no_sudden_drop_when_the_whole_cohort_falls(self):
        # the cohort median falls 0.80 -> 0.69 (0.11 >= 0.05): a dry-down, not one field's problem
        series = {"A": [0.80, 0.70], "B": [0.78, 0.68], "C": [0.82, 0.72], "D": [0.79, 0.69], "E": [0.81, 0.40]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["latest_z"] == pytest.approx(-8.33, abs=0.01)
        assert e["sudden_drop"] is False
        assert e["anomaly_type"] == "none"

    def test_sudden_drop_needs_passes_close_together(self):
        far = ["2026-09-01", "2026-10-16"]                                       # 45 days apart
        series = {"A": [0.80, 0.80], "B": [0.78, 0.78], "C": [0.82, 0.82], "D": [0.79, 0.79], "E": [0.81, 0.55]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(far, series)), CONFIG)
        assert out["fields"]["E"]["sudden_drop"] is None
        assert out["fields"]["E"]["anomaly_type"] == "none"

    def test_flat_field_in_a_shrinking_cohort_is_not_a_sudden_drop(self):
        # X sits at 0.30 on every pass. Passes 1-2 (9 soybean fields): 0.20 0.22 0.24 0.26 | 0.30 | 0.340 0.342 0.344 0.346
        #   -> median 0.30 (X itself), z(X) = 0. The four brown fields are harvested on 2026-10-13, so pass 3 holds
        #   0.30 0.340 0.342 0.344 0.346 -> median 0.342, MAD 0.002, scale 0.0229652, z(X) = -0.042 / 0.0229652 = -1.83.
        # The z fell 1.83 while X did not move an inch: the cohort changed, the field did not.
        brown = {"B1": 0.20, "B2": 0.22, "B3": 0.24, "B4": 0.26}
        green = {"G1": 0.340, "G2": 0.342, "G3": 0.344, "G4": 0.346}
        series = {fid: [v] * 3 for fid, v in {**brown, **green, "X": 0.30}.items()}
        st = status([{"field_id": b, "stage": "harvested", "harvest_date": "2026-10-13"} for b in brown])
        props = [(f, "Z01", "soybean") for f in series]
        out = anomaly.analyze(data(fields(props), ts(THREE, series), st), CONFIG)
        x = out["fields"]["X"]
        assert x["prev_z"] == pytest.approx(0.0, abs=0.01) and x["latest_z"] == pytest.approx(-1.83, abs=0.01)
        assert x["ndvi_change"] == 0.0
        assert x["sudden_drop"] is False and x["anomaly_type"] == "none"
        assert "walk" not in x["note"]

    def test_green_outlier_coming_back_to_the_pack_is_not_a_sudden_drop(self):
        # pass 1: A 0.60 B 0.58 C 0.62 D 0.59 E 0.85 -> median 0.60, MAD 0.02, z(E) = 0.25 / 0.049652 = 5.04
        # pass 2: E 0.63 -> median 0.60, MAD 0.02, z(E) = 0.03 / 0.049652 = 0.60: the z fell 4.44 but E is still
        # a touch greener than its neighbours, so nothing fell below the pack
        series = {"A": [0.60, 0.60], "B": [0.58, 0.58], "C": [0.62, 0.62], "D": [0.59, 0.59], "E": [0.85, 0.63]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["prev_z"] == pytest.approx(5.04, abs=0.01) and e["latest_z"] == pytest.approx(0.60, abs=0.01)
        assert e["sudden_drop"] is False and e["anomaly_type"] == "none"

    def test_sudden_drop_needs_the_field_itself_to_fall(self):
        # a uniform cohort (A-D 0.80, MAD 0, scale 0.02) turns a small move into a big z: E 0.81 -> 0.77 is z +0.5 -> -1.5,
        # a fall of 2.0 in z for 0.04 NDVI, under the 0.05 minimum; E 0.81 -> 0.75 (0.06) is z +0.5 -> -2.5 and counts
        base = {"A": [0.80, 0.80], "B": [0.80, 0.80], "C": [0.80, 0.80], "D": [0.80, 0.80]}
        small = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, {**base, "E": [0.81, 0.77]})), CONFIG)["fields"]["E"]
        real = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, {**base, "E": [0.81, 0.75]})), CONFIG)["fields"]["E"]
        assert small["latest_z"] == pytest.approx(-1.5, abs=0.01) and small["ndvi_change"] == pytest.approx(-0.04)
        assert small["sudden_drop"] is False and small["anomaly_type"] == "none"
        assert real["latest_z"] == pytest.approx(-2.5, abs=0.01) and real["sudden_drop"] is True
        assert real["anomaly_type"] == "sudden_drop" and "0.81 to 0.75" in real["note"]

    def test_early_senescence_is_onset_more_than_7_days_before_the_cohort(self):
        dates = ["2026-09-01", "2026-09-06", "2026-09-11", "2026-09-16", "2026-09-21", "2026-09-26"]
        late = [0.85, 0.85, 0.85, 0.85, 0.70, 0.70]           # 0.70 <= 0.9 * 0.85 = 0.765: onset 2026-09-21
        early = [0.85, 0.70, 0.70, 0.70, 0.70, 0.70]          # onset 2026-09-06, 15 days before the median onset
        dip = [0.85, 0.70, 0.85, 0.85, 0.70, 0.70]            # a dip that climbs back to green is not the onset
        series = {fid: list(late) for fid in "ABCDE"}
        series["F"], series["G"] = early, dip
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDEFG")), ts(dates, series)), CONFIG)
        f, a, g = out["fields"]["F"], out["fields"]["A"], out["fields"]["G"]
        assert f["onset"] == "2026-09-06" and f["cohort_onset"] == "2026-09-21"
        assert f["early_senescence"] is True
        assert f["persistent_low"] is False                   # the last two passes are level with the cohort
        assert f["anomaly_type"] == "early_senescence"
        assert "15 days" in f["note"]
        assert a["onset"] == "2026-09-21" and a["early_senescence"] is False and a["anomaly_type"] == "none"
        assert g["onset"] == "2026-09-21" and g["early_senescence"] is False and g["anomaly_type"] == "none"

    def test_outlier_high_is_latest_z_at_or_above_2(self):
        # A 0.60 B 0.58 C 0.62 D 0.59 E 0.85 -> median 0.60; |dev| 0, 0.02, 0.02, 0.01, 0.25 -> MAD 0.02
        # scale = 1.4826 * 0.02 + 0.02 = 0.049652; z(E) = 0.25 / 0.049652 = 5.04
        series = flat(2, {"A": 0.60, "B": 0.58, "C": 0.62, "D": 0.59, "E": 0.85})
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["latest_z"] == pytest.approx(5.04, abs=0.01)
        assert e["outlier_high"] is True and e["anomaly_type"] == "outlier_high"
        assert e["rank_in_zone"] == 1
        assert "greener" in e["note"].lower()

    def test_chronic_low_wins_over_sudden_drop(self):
        # pass 1: E 0.70 -> median 0.79, MAD 0.01, z = -0.09 / 0.034826 = -2.58; pass 2: E 0.50 -> z = -8.33
        series = {"A": [0.80, 0.80], "B": [0.78, 0.78], "C": [0.82, 0.82], "D": [0.79, 0.79], "E": [0.70, 0.50]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(TWO, series)), CONFIG)
        e = out["fields"]["E"]
        assert e["persistent_low"] is True and e["sudden_drop"] is True
        assert e["anomaly_type"] == "chronic_low"

    def test_zone_wide_when_at_least_30_percent_of_the_cohort_is_flagged(self):
        # A 0.80 B 0.78 C 0.82 D 0.79 E 0.50 F 0.52 -> median 0.785, MAD 0.025, z(E) = -0.285 / 0.057065 = -4.99
        series = flat(2, {"A": 0.80, "B": 0.78, "C": 0.82, "D": 0.79, "E": 0.50, "F": 0.52})
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDEF")), ts(TWO, series)), CONFIG)
        e, f, a = out["fields"]["E"], out["fields"]["F"], out["fields"]["A"]
        assert e["latest_z"] == pytest.approx(-4.99, abs=0.01)
        assert e["anomaly_type"] == "chronic_low" and f["anomaly_type"] == "chronic_low"
        assert e["event_scope"] == "zone_wide" and f["event_scope"] == "zone_wide"      # 2 of 6 = 33%
        assert e["neighbors_flagged"] == 1 and a["neighbors_flagged"] == 2
        assert out["zones"]["Z01"]["flagged"] == 2
        assert out["zones"]["Z01"]["share"] == pytest.approx(2 / 6, abs=0.001)
        assert out["landscape"]["counts"]["chronic_low"] == 2 and out["landscape"]["counts"]["none"] == 4
        assert out["landscape"]["top"][0]["field_id"] == "E" and out["landscape"]["top"][0]["type"] == "chronic_low"
        assert len(out["landscape"]["top"]) == 6


class TestCohorts:
    def test_small_zone_cohort_falls_back_to_the_crop_landscape_wide(self):
        props = corn("Z01", "A", "B") + corn("Z02", "C", "D", "E", "F")          # 2 and 4 corn fields: both under 5
        series = flat(2, {"A": 0.80, "B": 0.78, "C": 0.82, "D": 0.79, "E": 0.81, "F": 0.50})
        out = anomaly.analyze(data(fields(props), ts(TWO, series)), CONFIG)
        assert out["fields"]["A"]["cohort"] == "corn/all"
        assert out["fields"]["F"]["cohort"] == "corn/all"
        assert out["fields"]["F"]["anomaly_type"] == "chronic_low"
        assert out["landscape"]["cohorts"]["corn/all"]["fallback"] is True
        assert out["fields"]["A"]["rank_in_zone"] == 1 and out["fields"]["A"]["zone_crop_n"] == 2   # rank stays in the zone

    def test_cohorts_are_per_crop(self):
        props = corn("Z01", *"ABCDE") + [(f, "Z01", "soybean") for f in "PQRST"]
        series = flat(2, {"A": 0.80, "B": 0.78, "C": 0.82, "D": 0.79, "E": 0.81,
                          "P": 0.50, "Q": 0.48, "R": 0.52, "S": 0.49, "T": 0.51})
        out = anomaly.analyze(data(fields(props), ts(TWO, series)), CONFIG)
        assert out["fields"]["P"]["cohort"] == "soybean/Z01"
        assert all(out["fields"][x]["anomaly_type"] == "none" for x in "ABCDEPQRST")
        assert out["fields"]["P"]["rank_in_zone"] == 3 and out["fields"]["P"]["zone_crop_n"] == 5   # R 0.52, T 0.51, P 0.50

    def test_fallback_cohort_share_counts_every_field_it_was_compared_against(self):
        # corn/Z01 has six fields (a proper cohort); corn/Z02 has two, so they are compared with all eight corn fields:
        # 0.50 0.78 0.79 0.80 0.80 0.80 0.81 0.82 -> median 0.80, MAD 0.01, z(Q) = -0.30 / 0.034826 = -8.61
        props = corn("Z01", *"ABCDEF") + corn("Z02", "P", "Q")
        series = flat(2, {"A": 0.80, "B": 0.78, "C": 0.82, "D": 0.79, "E": 0.81, "F": 0.80, "P": 0.80, "Q": 0.50})
        out = anomaly.analyze(data(fields(props), ts(TWO, series)), CONFIG)
        q = out["fields"]["Q"]
        assert q["cohort"] == "corn/all" and q["latest_z"] == pytest.approx(-8.61, abs=0.01)
        assert q["anomaly_type"] == "chronic_low" and out["fields"]["A"]["cohort"] == "corn/Z01"
        c = out["landscape"]["cohorts"]
        assert c["corn/all"]["n"] == 8 and c["corn/all"]["flagged"] == 1 and c["corn/all"]["share"] == 0.125
        assert c["corn/all"]["scope"] == "isolated" and q["event_scope"] == "isolated"          # 1 of 8, not 1 of 2
        assert c["corn/Z01"]["n"] == 6 and c["corn/Z01"]["flagged"] == 0

    def test_crop_other_with_too_few_peers_anywhere_gets_no_verdict(self):
        props = corn("Z01", *"ABCDE") + [("G", "Z01", "other"), ("H", "Z02", "other")]
        series = flat(2, {**FLAT, "G": 0.30, "H": 0.35})
        out = anomaly.analyze(data(fields(props), ts(TWO, series)), CONFIG)
        assert out["fields"]["G"] is None and out["fields"]["H"] is None
        assert any("G" in n and "H" in n for n in out["landscape"]["notes"])
        assert out["fields"]["E"]["anomaly_type"] == "chronic_low"
        assert out["landscape"]["counts"]["no_verdict"] == 2


class TestHarvested:
    FOUR = ["2026-10-01", "2026-10-06", "2026-10-11", "2026-10-16"]

    def test_harvested_field_is_compared_only_before_its_harvest(self):
        series = {"A": [0.80] * 4, "B": [0.78] * 4, "C": [0.82] * 4, "D": [0.79] * 4, "H": [0.80, 0.80, 0.20, 0.15]}
        st = status([{"field_id": "H", "stage": "harvested", "harvest_date": "2026-10-08"}])
        out = anomaly.analyze(data(fields(corn("Z01", "A", "B", "C", "D", "H")), ts(self.FOUR, series), st), CONFIG)
        h = out["fields"]["H"]
        assert h["n_passes"] == 2 and h["latest_pass"] == "2026-10-06"
        assert h["latest_z"] == pytest.approx(0.0, abs=0.01)
        assert h["anomaly_type"] == "none"
        assert h["rank_in_zone"] == 5                                        # the rank still uses what the field looks like now

    def test_harvested_passes_do_not_drag_the_cohort_median(self):
        # three fields harvested on 2026-10-08 and two standing: without the cut the last pass would read
        # median 0.15, MAD 0 and z = (0.80 - 0.15) / 0.02 = 32 for every standing field
        series = {"H1": [0.80, 0.80, 0.15, 0.15], "H2": [0.78, 0.78, 0.14, 0.14], "H3": [0.82, 0.82, 0.16, 0.16],
                  "S1": [0.79, 0.79, 0.80, 0.80], "S2": [0.81, 0.81, 0.79, 0.79]}
        st = status([{"field_id": h, "stage": "harvested", "harvest_date": "2026-10-08"} for h in ("H1", "H2", "H3")])
        out = anomaly.analyze(data(fields(corn("Z01", "H1", "H2", "H3", "S1", "S2")), ts(self.FOUR, series), st), CONFIG)
        s1 = out["fields"]["S1"]
        assert s1["latest_pass"] == "2026-10-06"                              # the last pass with five comparable values
        assert s1["outlier_high"] is False and s1["anomaly_type"] == "none"

    def test_harvested_field_speaks_in_the_past_and_stands_behind_standing_fields(self):
        # passes 1-2 (6 fields): 0.50 0.62 0.78 0.79 0.80 0.82 -> median 0.785, MAD 0.025, scale 0.057065:
        #   z(H) = -0.285 / 0.057065 = -4.99, z(S) = -0.165 / 0.057065 = -2.89
        # H is harvested on 2026-10-08; passes 3-4 (5 fields): 0.62 0.78 0.79 0.80 0.82 -> median 0.79, MAD 0.01,
        #   scale 0.034826: z(S) = -0.17 / 0.034826 = -4.88
        series = {"A": [0.80] * 4, "B": [0.78] * 4, "C": [0.82] * 4, "D": [0.79] * 4,
                  "H": [0.50, 0.50, 0.20, 0.15], "S": [0.62] * 4}
        st = status([{"field_id": "H", "stage": "harvested", "harvest_date": "2026-10-08"}])
        out = anomaly.analyze(data(fields(corn("Z01", "A", "B", "C", "D", "H", "S")), ts(self.FOUR, series), st), CONFIG)
        h, s_, a = out["fields"]["H"], out["fields"]["S"], out["fields"]["A"]
        assert h["harvested"] is True and h["harvest_date"] == "2026-10-08"
        assert s_["harvested"] is False and s_["harvest_date"] is None and a["harvested"] is False
        assert h["anomaly_type"] == "chronic_low" and h["latest_z"] == pytest.approx(-4.99, abs=0.01)
        assert h["note"].startswith("Before its harvest on 2026-10-08") and "2026-10-06" in h["note"]
        assert "check" not in h["note"] and "walk" not in h["note"]
        assert s_["anomaly_type"] == "chronic_low" and "check" in s_["note"]
        # the standing field leads the landscape list even though the harvested one has the bigger z
        top = out["landscape"]["top"]
        assert [t["field_id"] for t in top[:2]] == ["S", "H"]
        assert top[0]["harvested"] is False and top[1]["harvested"] is True
        assert out["landscape"]["n_flagged"] == 2 and out["landscape"]["n_flagged_standing"] == 1

    def test_harvested_before_any_pass_is_none_with_a_note(self):
        series = {"A": [0.80] * 2, "B": [0.78] * 2, "C": [0.82] * 2, "D": [0.79] * 2, "H": [0.20, 0.15]}
        st = status([{"field_id": "H", "stage": "harvested", "harvest_date": "2026-09-30"}])
        out = anomaly.analyze(data(fields(corn("Z01", "A", "B", "C", "D", "H")), ts(TWO, series), st), CONFIG)
        assert out["fields"]["H"] is None
        assert any("H" in n for n in out["landscape"]["notes"])


class TestEdges:
    def test_field_absent_from_timeseries_is_none_with_a_note(self):
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDEX")), ts(TWO, flat(2))), CONFIG)
        assert out["fields"]["X"] is None
        assert any("X" in n for n in out["landscape"]["notes"])
        assert out["fields"]["E"]["anomaly_type"] == "chronic_low"

    def test_all_null_series_is_none(self):
        series = {**flat(2), "X": [None, None]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDEX")), ts(TWO, series)), CONFIG)
        assert out["fields"]["X"] is None
        assert out["fields"]["X"] is None and out["landscape"]["counts"]["no_verdict"] == 1

    def test_one_clear_pass_only_is_none(self):
        series = {**flat(2), "X": [None, 0.80]}
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDEX")), ts(TWO, series)), CONFIG)
        assert out["fields"]["X"] is None
        assert any("X" in n for n in out["landscape"]["notes"])

    def test_no_timeseries_at_all_returns_every_field_none(self):
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), None), CONFIG)
        assert out["key"] == "anomaly"
        assert set(out["fields"]) == set("ABCDE") and all(v is None for v in out["fields"].values())
        assert out["landscape"]["notes"]
        assert out["landscape"]["counts"]["chronic_low"] == 0

    def test_missing_optional_inputs_and_empty_config_do_not_matter(self):
        out = anomaly.analyze({"fields": fields(corn("Z01", *"ABCDE")), "timeseries": ts(THREE, flat(3))}, {}, prior=None)
        assert out["fields"]["E"]["anomaly_type"] == "chronic_low"

    def test_garbage_input_does_not_raise(self):
        out = anomaly.analyze({"fields": 7, "timeseries": {"dates": ["bad", 3], "fields": {"A": {"ndvi": "x"}}}}, None)
        assert out["key"] == "anomaly" and isinstance(out["fields"], dict)
        assert out["landscape"]["notes"]

    def test_output_shape_and_json(self):
        out = anomaly.analyze(data(fields(corn("Z01", *"ABCDE")), ts(THREE, flat(3))), CONFIG)
        assert set(out) == {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert isinstance(out["method"], str) and len(out["method"]) > 50
        assert all(isinstance(v, str) for v in out["assumptions"].values())
        json.dumps(out, allow_nan=False)
        assert len(json.dumps(out["fields"]["E"])) < 1024


class TestMock:
    def test_mock_shapes_and_sanity(self):
        d = ROOT / "data" / "mock"
        if not (d / "timeseries.json").exists():
            pytest.skip("no mock data")

        def load(n):
            p = d / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        dat = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
               "status": load("status.json"), "weather": load("weather.json"), "fires": load("fires.json"),
               "insights": load("insights.json"), "plans": load("plans.json"), "route": load("route.json"),
               "truth": load("truth.json"), "usda": None, "chcs": None}
        config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
        out = anomaly.analyze(dat, config)
        json.dumps(out, allow_nan=False)
        ids = {f["properties"]["field_id"] for f in dat["fields"]["features"]}
        zones = {f["properties"]["zone_id"] for f in dat["zones"]["features"]}
        dates = set(dat["timeseries"]["dates"])
        assert out["key"] == "anomaly" and set(out["fields"]) == ids
        verdicts = {k: v for k, v in out["fields"].items() if v is not None}
        assert len(verdicts) >= 0.8 * len(ids)
        types = {"none", "chronic_low", "sudden_drop", "early_senescence", "outlier_high"}
        for fid, v in verdicts.items():
            assert v["anomaly_type"] in types
            assert v["latest_pass"] in dates and -50 <= v["latest_z"] <= 50
            assert 1 <= v["rank_in_zone"] <= v["zone_crop_n"]
            assert v["event_scope"] in (None, "isolated", "zone_wide")
            assert (v["event_scope"] is None) == (v["anomaly_type"] == "none")
            assert isinstance(v["harvested"], bool) and (v["harvest_date"] is not None) == v["harvested"]
            if v["harvested"]:                                        # no instructions for a crop that is already in
                assert v["note"].startswith("Before its harvest") and "walk" not in v["note"] and "check" not in v["note"]
            if v["sudden_drop"]:
                assert v["ndvi_change"] <= -anomaly.MIN_OWN_FALL_NDVI and v["latest_z"] <= 0
            assert len(json.dumps(v)) < 1024
        land = out["landscape"]
        assert sum(land["counts"][t] for t in types) == len(verdicts)
        assert land["counts"]["no_verdict"] == len(ids) - len(verdicts)
        assert 0 < len(land["top"]) <= 10 and {"field_id", "z", "type", "harvested"} <= set(land["top"][0])
        assert land["n_flagged_standing"] <= land["n_flagged"]
        for key, c in land["cohorts"].items():
            assert c["flagged"] <= c["n"] and (c["n"] == 0 or c["share"] == pytest.approx(c["flagged"] / c["n"], abs=0.001))
        assert set(out["zones"]) == zones
        for z in out["zones"].values():
            assert 0.0 <= z["share"] <= 1.0 and z["flagged"] <= z["compared"]
