"""Fixture tests for intelligence/events.py: burn severity (dNBR), flood duration and fire proximity.
Every expected number is worked out by hand in a comment next to the assertion."""
import json
import math
from pathlib import Path

import pytest

import events

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-10-17"
# ten passes five days apart, 2026-09-01 .. 2026-10-16
DATES = ["2026-09-01", "2026-09-06", "2026-09-11", "2026-09-16", "2026-09-21",
         "2026-09-26", "2026-10-01", "2026-10-06", "2026-10-11", "2026-10-16"]
CONFIG = {"as_of": AS_OF, "thresholds": {"burn_nbr_max": -0.05, "peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25}}
LON, LAT = -93.47, 42.03


def square(lon=LON, lat=LAT, half=0.005):
    """A box about 0.8 km wide and 1.1 km tall centred on (lon, lat)."""
    return {"type": "Polygon", "coordinates": [[[lon - half, lat - half], [lon + half, lat - half],
                                                [lon + half, lat + half], [lon - half, lat + half],
                                                [lon - half, lat - half]]]}


def feature(fid, zone="Z01", crop="corn", area=20.0, lon=LON, lat=LAT, geometry=None):
    return {"type": "Feature", "geometry": geometry if geometry is not None else square(lon, lat),
            "properties": {"field_id": fid, "zone_id": zone, "area_ha": area, "crop": crop, "source": "test"}}


def series(ndvi, nbr=None):
    nbr = nbr if nbr is not None else [None if v is None else round(v * 0.5, 3) for v in ndvi]
    return {"ndvi": ndvi, "nbr": nbr, "clear_frac": [0.0 if v is None else 0.9 for v in ndvi]}


def rec(fid, **kw):
    base = {"field_id": fid, "zone_id": "Z01", "stage": "harvested", "peak_date": "2026-09-01", "peak_ndvi": 0.80,
            "harvest_date": "2026-09-23", "predicted_harvest_start": None, "predicted_harvest_end": None,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.9, "notes": ""}
    base.update(kw)
    return base


def make_data(features, ts_fields, status_fields=None, detections=None, dates=DATES, weather=None):
    return {
        "fields": {"type": "FeatureCollection", "features": features},
        "zones": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z01", "name": "A", "centroid": [LON, LAT]}},
            {"type": "Feature", "geometry": None, "properties": {"zone_id": "Z02", "name": "B", "centroid": [LON + 0.1, LAT]}}]},
        "timeseries": {"source": "test", "dates": dates, "fields": ts_fields},
        "status": None if status_fields is None else {"as_of": AS_OF, "fields": status_fields},
        "weather": weather if weather is not None else {},
        "fires": {"source": "test", "detections": detections or []},
        "insights": None, "plans": None, "route": None, "truth": None, "usda": None, "chcs": None,
    }


def hotspot(lon, lat, date, field_id=None):
    return {"field_id": field_id, "lon": lon, "lat": lat, "date": date, "satellite": "VIIRS", "confidence": "n"}


# ---------------------------------------------------------------- dNBR classes (USGS thresholds)

class TestClassify:
    @pytest.mark.parametrize("dnbr, cls, scar", [
        (0.05, "unburned", "tillage_or_residue"),
        (0.099, "unburned", "tillage_or_residue"),
        (0.10, "low", "burn_low"),
        (0.20, "low", "burn_low"),
        (0.27, "moderate_low", "burn_moderate"),
        (0.40, "moderate_low", "burn_moderate"),
        (0.44, "moderate_high", "burn_moderate"),
        (0.66, "moderate_high", "burn_moderate"),
        (0.661, "high", "burn_high"),
        (0.90, "high", "burn_high"),
        (-0.10, "unburned", "tillage_or_residue"),
    ])
    def test_thresholds(self, dnbr, cls, scar):
        assert events.classify_dnbr(dnbr) == (cls, scar)

    def test_none_stays_none(self):
        assert events.classify_dnbr(None) == (None, None)
        assert events.classify_dnbr(float("nan")) == (None, None)


# ---------------------------------------------------------------- burn

class TestBurn:
    def test_moderate_scar_after_harvest(self):
        # harvest 09-23; passes on/after it: 09-26 (0.12), 10-01 (-0.20) <= -0.05 -> event 10-01
        # pre = last clear NBR before 10-01 = 0.12; post = min at/after event = -0.25 (10-06); dNBR = 0.37 -> moderate_low
        # recovery: NDVI 0.10 at the event; later 0.09, 0.12, 0.18 -> first rise >= 0.05 is 10-16 (0.18 - 0.10 = 0.08)
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.18, 0.10, 0.09, 0.12, 0.18]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.25, 0.12, -0.20, -0.25, -0.10, 0.00]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is True
        assert b["event_date"] == "2026-10-01"
        assert b["pre_nbr"] == 0.12 and b["pre_date"] == "2026-09-26"
        assert b["post_nbr"] == -0.25 and b["post_date"] == "2026-10-06"
        assert b["dnbr"] == 0.37
        assert b["dnbr_class"] == "moderate_low"
        assert b["scar_type"] == "burn_moderate"
        assert b["evidence"] == "nbr"
        assert b["recovery"] == {"date": "2026-10-16", "ndvi_rise": 0.08}
        assert b["pre_is_standing_crop"] is False           # pre pass 09-26 is after the 09-23 harvest: residue, not crop
        assert out["landscape"]["burned_fields"] == 1
        assert out["landscape"]["burned_area_ha"] == 20.0
        assert out["landscape"]["by_class"]["moderate_low"] == {"fields": 1, "area_ha": 20.0}
        assert out["landscape"]["by_class"]["high"] == {"fields": 0, "area_ha": 0.0}
        assert out["landscape"]["scar_types"]["burn_moderate"] == 1
        assert out["landscape"]["pre_standing_crop_fields"] == 0
        assert out["zones"]["Z01"]["burned"] == 1

    def test_harvest_and_tillage_between_two_passes_is_flagged(self):
        # harvest_date 09-23 lies between the 09-21 pass (standing crop, NBR 0.28) and the 09-26 pass (tilled, NBR -0.09):
        # event 09-26, pre 09-21 = 0.28, post -0.09, dNBR 0.37 -> moderate_low by the table, but the harvest itself is part
        # of the drop, so the class is only an upper bound: pre_is_standing_crop True, scar_type None, no scar_types count
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.08, 0.07, 0.07, 0.08, 0.08]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.28, -0.09, -0.09, -0.08, -0.08, -0.08]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is True and b["event_date"] == "2026-09-26"
        assert b["pre_date"] == "2026-09-21" and b["pre_nbr"] == 0.28 and b["post_nbr"] == -0.09
        assert b["dnbr"] == 0.37 and b["dnbr_class"] == "moderate_low"
        assert b["pre_is_standing_crop"] is True
        assert b["scar_type"] is None
        L = out["landscape"]
        assert L["burned_fields"] == 1 and L["by_class"]["moderate_low"]["fields"] == 1
        assert sum(L["scar_types"].values()) == 0
        assert L["pre_standing_crop_fields"] == 1

    def test_cloudy_gap_at_harvest_is_flagged(self):
        # 09-21 and 09-26 cloudy around the 09-23 harvest; the scar shows on 10-01 (NBR -0.08); pre = last clear pass
        # 09-16 = 0.30 (standing crop): dNBR 0.38 -> moderate_low as an upper bound, pre_is_standing_crop True
        ndvi = [0.80, 0.75, 0.60, 0.45, None, None, 0.07, 0.07, 0.08, 0.08]
        nbr = [0.40, 0.38, 0.35, 0.30, None, None, -0.08, -0.08, -0.08, -0.08]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["event_date"] == "2026-10-01" and b["pre_date"] == "2026-09-16" and b["pre_nbr"] == 0.30
        assert b["dnbr"] == 0.38 and b["dnbr_class"] == "moderate_low"
        assert b["pre_is_standing_crop"] is True and b["scar_type"] is None

    def test_standing_crop_pre_with_a_small_drop_is_still_tillage(self):
        # dry mature crop 09-21 NBR 0.03, tilled 09-26 NBR -0.06: dNBR 0.09 -> unburned even with the harvest inside the
        # drop, so the upper bound settles it: flagged, but scar_type stays tillage_or_residue
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.10, 0.09, 0.09, 0.10, 0.10]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.03, -0.06, -0.06, -0.05, -0.05, -0.05]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["event_date"] == "2026-09-26" and b["dnbr"] == 0.09 and b["dnbr_class"] == "unburned"
        assert b["pre_is_standing_crop"] is True and b["scar_type"] == "tillage_or_residue"
        assert out["landscape"]["scar_types"]["tillage_or_residue"] == 1
        assert out["landscape"]["pre_standing_crop_fields"] == 1

    def test_status_burn_in_the_standing_crop_is_not_flagged(self):
        # hotspot burn_date 09-10, before the 09-23 harvest; NBR has climbed back above -0.05 by 09-26, so no scar is
        # found in the post-harvest window and the status date is the event: pre 09-06 = 0.38 is the crop, post = min
        # from 09-10 on = -0.20 (09-11), dNBR 0.58 -> moderate_high. The harvest is not inside the drop
        # (event < harvest_date), so a real fire in standing crop keeps its class and scar type
        ndvi = [0.80, 0.75, 0.10, 0.08, 0.08, 0.10, 0.12, 0.14, 0.15, 0.16]
        nbr = [0.40, 0.38, -0.20, -0.15, -0.08, -0.03, 0.00, 0.02, 0.03, 0.04]
        st = [rec("F1", stage="burned", burn_date="2026-09-10", burn_evidence="hotspot")]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, st), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["evidence"] == "status"
        assert b["event_date"] == "2026-09-10" and b["pre_date"] == "2026-09-06" and b["pre_nbr"] == 0.38
        assert b["post_date"] == "2026-09-11" and b["dnbr"] == 0.58 and b["dnbr_class"] == "moderate_high"
        assert b["pre_is_standing_crop"] is False and b["scar_type"] == "burn_moderate"

    def test_method_and_assumptions_say_what_tillage_reads_as(self):
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None), CONFIG)
        assert "tillage pass or residue reads as unburned" not in out["method"]
        assert "standing crop" in out["method"]
        assert "standing crop" in out["assumptions"]["pre_fire_nbr"] and "upper bound" in out["assumptions"]["pre_fire_nbr"]

    def test_tillage_reads_unburned(self):
        # after harvest: 0.12, 0.02, -0.06 (first <= -0.05, event 10-06); pre 0.02; post -0.06; dNBR 0.08 -> unburned
        # NDVI after the event rises only 0.02 (0.10 -> 0.12): no recovery yet
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.18, 0.12, 0.10, 0.11, 0.12]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.25, 0.12, 0.02, -0.06, -0.04, -0.02]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is True and b["event_date"] == "2026-10-06"
        assert b["dnbr"] == 0.08 and b["dnbr_class"] == "unburned" and b["scar_type"] == "tillage_or_residue"
        assert b["recovery"] is None
        assert out["landscape"]["by_class"]["unburned"]["fields"] == 1
        assert out["landscape"]["scar_types"]["tillage_or_residue"] == 1

    def test_negative_nbr_before_the_peak_is_not_a_scar(self):
        # growing crop, no harvest: only passes on/after peak_date 09-21 count, and none of those is <= -0.05
        ndvi = [0.20, 0.30, 0.50, 0.70, 0.80, 0.78, 0.75, 0.70, 0.65, 0.60]
        nbr = [-0.10, -0.08, 0.10, 0.20, 0.45, 0.40, 0.38, 0.35, 0.30, 0.28]
        st = [rec("F1", stage="growing", peak_date="2026-09-21", harvest_date=None)]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, st), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is False and b["event_date"] is None and b["dnbr"] is None and b["scar_type"] is None
        assert out["landscape"]["burned_fields"] == 0

    def test_scar_after_peak_without_harvest_date(self):
        # peak 09-21 (NBR 0.45), then 0.40 on 09-26, -0.07 on 10-01 -> event 10-01, pre 0.40, post -0.07, dNBR 0.47 -> moderate_high
        ndvi = [0.20, 0.30, 0.50, 0.70, 0.80, 0.78, 0.05, 0.05, 0.05, 0.05]
        nbr = [-0.10, -0.08, 0.10, 0.20, 0.45, 0.40, -0.07, -0.07, -0.07, -0.07]
        st = [rec("F1", stage="growing", peak_date="2026-09-21", harvest_date=None)]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, st), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["event_date"] == "2026-10-01" and b["pre_nbr"] == 0.40 and b["post_nbr"] == -0.07
        assert b["dnbr"] == 0.47 and b["dnbr_class"] == "moderate_high" and b["scar_type"] == "burn_moderate"

    def test_without_status_the_peak_comes_from_the_series(self):
        # no status.json at all: the NDVI maximum (09-21) starts the window; the 10-01 pass is the scar
        ndvi = [0.20, 0.30, 0.50, 0.70, 0.80, 0.78, 0.05, 0.05, 0.05, 0.05]
        nbr = [-0.10, -0.08, 0.10, 0.20, 0.45, 0.40, -0.07, -0.07, -0.07, -0.07]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, None), CONFIG)
        assert out["fields"]["F1"]["burn"]["event_date"] == "2026-10-01"
        assert out["fields"]["F1"]["burn"]["dnbr"] == 0.47

    def test_status_burn_date_without_nbr_scar(self):
        # hotspot-only burn in status (burn_date 10-03), NBR never <= -0.05:
        # event = 10-03; pre = last clear NBR before it = 0.10 (10-01); post = min from 10-03 on = 0.03 (10-06); dNBR 0.07
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.18, 0.15, 0.12, 0.12, 0.13]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.25, 0.12, 0.10, 0.03, 0.05, 0.06]
        st = [rec("F1", stage="burned", burn_date="2026-10-03", burn_evidence="hotspot")]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, st), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is True and b["evidence"] == "status"
        assert b["event_date"] == "2026-10-03"
        assert b["pre_nbr"] == 0.10 and b["post_nbr"] == 0.03 and b["dnbr"] == 0.07
        assert b["dnbr_class"] == "unburned"

    def test_cloudy_pass_before_the_drop_is_skipped(self):
        # 10-01 is cloudy (null); the scar shows on 10-06; pre is the last clear NBR before it, 0.15 on 09-26
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.18, None, 0.10, 0.10, 0.10]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.25, 0.15, None, -0.10, -0.10, -0.10]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["event_date"] == "2026-10-06" and b["pre_nbr"] == 0.15 and b["pre_date"] == "2026-09-26"
        assert b["dnbr"] == 0.25 and b["dnbr_class"] == "low" and b["scar_type"] == "burn_low"

    def test_scar_on_first_pass_has_no_pre(self):
        # one pass only, already dark: event known, severity unknown (no pass before it)
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.05], [-0.10])}, None, dates=["2026-10-16"]), CONFIG)
        b = out["fields"]["F1"]["burn"]
        assert b["burned"] is True and b["event_date"] == "2026-10-16"
        assert b["pre_nbr"] is None and b["dnbr"] is None and b["dnbr_class"] is None and b["scar_type"] is None
        assert out["landscape"]["by_class"]["unclassified"]["fields"] == 1


# ---------------------------------------------------------------- flood

class TestFlood:
    def test_episode_with_a_cloudy_pass_inside(self):
        # clear passes <= 0.0: 09-11 (-0.05) and 09-21 (-0.02); 09-16 is cloudy so they are consecutive clear passes
        # span 10 days; clear-date gaps 5,5,10,5,5,5,5,5 -> median 5 -> estimate 10 + 2.5 = 12.5; latest pass 0.50: not ongoing
        ndvi = [0.50, 0.40, -0.05, None, -0.02, 0.10, 0.30, 0.40, 0.45, 0.50]
        out = events.analyze(make_data([feature("F1", area=12.5)], {"F1": series(ndvi)}, None), CONFIG)
        f = out["fields"]["F1"]["flood"]
        assert f == {"flood_passes": 2, "flood_first": "2026-09-11", "flood_last": "2026-09-21",
                     "flood_days_estimate": 12.5, "ongoing": False, "flood_episodes": 1}
        assert out["landscape"]["flooded_fields"] == 1 and out["landscape"]["flooded_area_ha"] == 12.5
        assert out["landscape"]["flood_ongoing_fields"] == 0
        assert out["zones"]["Z01"]["flooded"] == 1 and out["zones"]["Z01"]["flood_ongoing"] == 0

    def test_latest_episode_is_reported_and_ongoing(self):
        # two episodes (09-11/09-21 and 10-16); the latest one is reported: 1 pass, span 0 + 2.5
        ndvi = [0.50, 0.40, -0.05, None, -0.02, 0.10, 0.30, 0.40, 0.45, -0.10]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi)}, None), CONFIG)
        f = out["fields"]["F1"]["flood"]
        assert f["flood_passes"] == 1 and f["flood_first"] == "2026-10-16" and f["flood_last"] == "2026-10-16"
        assert f["flood_days_estimate"] == 2.5 and f["ongoing"] is True and f["flood_episodes"] == 2
        assert out["landscape"]["flood_ongoing_fields"] == 1
        assert out["zones"]["Z01"]["flood_ongoing"] == 1

    def test_threshold_from_config(self):
        # with flood_ndvi_max 0.12 the 0.10 pass on 09-26 joins the run: 09-21 .. 09-26 is 2 passes but 09-11 is split by nothing:
        # clear passes <= 0.12 are 09-11, 09-21, 09-26 -> one episode of 3 passes, span 15 + 2.5 = 17.5
        ndvi = [0.50, 0.40, -0.05, None, -0.02, 0.10, 0.30, 0.40, 0.45, 0.50]
        cfg = {"as_of": AS_OF, "thresholds": {"flood_ndvi_max": 0.12}}
        f = events.analyze(make_data([feature("F1")], {"F1": series(ndvi)}, None), cfg)["fields"]["F1"]["flood"]
        assert f["flood_passes"] == 3 and f["flood_days_estimate"] == 17.5

    def test_dry_field(self):
        f = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None), CONFIG)["fields"]["F1"]["flood"]
        assert f == {"flood_passes": 0, "flood_first": None, "flood_last": None,
                     "flood_days_estimate": None, "ongoing": False, "flood_episodes": 0}

    def test_single_flooded_pass_uses_landscape_interval(self):
        # one clear pass only, so the field has no interval of its own; the landscape dates are 5 days apart -> 0 + 2.5
        ndvi = [None] * 9 + [-0.20]
        f = events.analyze(make_data([feature("F1")], {"F1": series(ndvi)}, None), CONFIG)["fields"]["F1"]["flood"]
        assert f["flood_passes"] == 1 and f["flood_days_estimate"] == 2.5 and f["ongoing"] is True


# ---------------------------------------------------------------- fire proximity

class TestFire:
    def test_inside_nearby_and_far(self):
        # F1 at (LON, LAT); hotspots: one at its centroid (inside, 0 km), one 0.018 deg east
        # (6371 km * cos(42.03 deg) * 0.018 deg in radians = 6371 * 0.7428 * 0.00031416 = 1.487 km, nearby),
        # one 0.05 deg east (4.13 km, not within 2 km), one inside but 10 days old (ignored)
        far_lon = LON + 0.05
        dets = [hotspot(LON, LAT, "2026-10-16"), hotspot(LON + 0.018, LAT, "2026-10-15"),
                hotspot(far_lon, LAT, "2026-10-14"), hotspot(LON, LAT, "2026-10-07")]
        feats = [feature("F1"), feature("F2", lon=LON + 0.2, zone="Z02"), feature("F3", lon=LON + 0.4, zone="Z02")]
        ts = {fid: series([0.5] * 10) for fid in ("F1", "F2", "F3")}
        dets.append(hotspot(LON - 0.3, LAT - 0.3, "2026-10-16", field_id="F3"))     # far away but tagged F3
        out = events.analyze(make_data(feats, ts, None, dets), CONFIG)
        f1 = out["fields"]["F1"]["fire"]
        assert f1["fire_exposure"] == "inside"
        assert f1["nearest_hotspot_km"] == 0.0
        assert f1["hotspots_within_2km"] == 2
        assert f1["hotspots_inside"] == 1
        assert f1["latest_hotspot_date"] == "2026-10-16"
        f2 = out["fields"]["F2"]["fire"]
        # F2 is 0.2 deg east of F1: nearest recent hotspot is the 0.05 deg one, 0.15 deg away = 12.39 km
        assert f2["fire_exposure"] == "none" and f2["hotspots_within_2km"] == 0
        assert f2["nearest_hotspot_km"] == pytest.approx(6371 * math.cos(math.radians(LAT)) * math.radians(0.15), abs=0.02)
        f3 = out["fields"]["F3"]["fire"]
        assert f3["fire_exposure"] == "inside" and f3["hotspots_inside"] == 1       # by field_id, not geometry
        assert out["landscape"]["recent_hotspots"] == 4 and out["landscape"]["hotspots_total"] == 5
        assert out["landscape"]["fire_exposed_fields"] == {"inside": 2, "nearby": 0}
        assert out["landscape"]["fire_exposed_area_ha"] == 40.0
        assert out["zones"]["Z01"]["fire_inside"] == 1 and out["zones"]["Z02"]["fire_inside"] == 1

    def test_nearby_only(self):
        dets = [hotspot(LON + 0.018, LAT, "2026-10-15")]
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None, dets), CONFIG)
        f = out["fields"]["F1"]["fire"]
        assert f["fire_exposure"] == "nearby" and f["hotspots_within_2km"] == 1 and f["hotspots_inside"] == 0
        assert f["nearest_hotspot_km"] == pytest.approx(1.487, abs=0.01)
        assert out["landscape"]["fire_exposed_fields"] == {"inside": 0, "nearby": 1}

    def test_lookback_edge(self):
        # exactly 5 days old counts, 6 does not
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None,
                                       [hotspot(LON, LAT, "2026-10-12"), hotspot(LON, LAT, "2026-10-11")]), CONFIG)
        assert out["fields"]["F1"]["fire"]["hotspots_inside"] == 1
        assert out["landscape"]["recent_hotspots"] == 1

    def test_no_detections(self):
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None, []), CONFIG)
        assert out["fields"]["F1"]["fire"] == {"nearest_hotspot_km": None, "hotspots_within_2km": 0, "hotspots_inside": 0,
                                               "fire_exposure": "none", "latest_hotspot_date": None}

    def test_bad_geometry_still_matches_by_field_id(self):
        feat = feature("F1", geometry={"type": "Polygon", "coordinates": []})
        dets = [hotspot(LON, LAT, "2026-10-16", field_id="F1")]
        out = events.analyze(make_data([feat], {"F1": series([0.5] * 10)}, None, dets), CONFIG)
        assert out["fields"]["F1"]["fire"]["fire_exposure"] == "inside"
        assert out["fields"]["F1"]["fire"]["nearest_hotspot_km"] is None


# ---------------------------------------------------------------- degrade, never raise

class TestEdges:
    def test_field_missing_from_timeseries_and_status(self):
        out = events.analyze(make_data([feature("F1"), feature("F9")], {"F1": series([0.5] * 10)}, [rec("F1")]), CONFIG)
        f9 = out["fields"]["F9"]
        assert f9["burn"]["burned"] is False and f9["burn"]["event_date"] is None
        assert f9["flood"]["flood_passes"] == 0 and f9["flood"]["ongoing"] is False
        assert f9["fire"]["fire_exposure"] == "none"
        assert f9["clear_passes"] == 0

    def test_all_null_series(self):
        out = events.analyze(make_data([feature("F1")], {"F1": series([None] * 10)}, [rec("F1")]), CONFIG)
        f = out["fields"]["F1"]
        assert f["burn"]["burned"] is False and f["flood"]["flood_passes"] == 0 and f["clear_passes"] == 0

    def test_crop_other_and_missing_weather(self):
        data = make_data([feature("F1", crop="other")], {"F1": series([0.3] * 10)}, [rec("F1", stage="unknown", harvest_date=None)])
        data["weather"] = None
        out = events.analyze(data, CONFIG)
        assert set(out["fields"]["F1"]) >= {"burn", "flood", "fire"}

    def test_no_fields_at_all(self):
        out = events.analyze({"fields": None, "timeseries": None, "status": None, "fires": None}, CONFIG)
        assert out["key"] == "events" and out["fields"] == {} and out["zones"] == {}
        assert out["landscape"]["notes"]

    def test_no_timeseries_and_no_fires_key(self):
        out = events.analyze({"fields": {"type": "FeatureCollection", "features": [feature("F1")]}}, CONFIG)
        assert out["fields"]["F1"]["burn"]["burned"] is False
        assert out["fields"]["F1"]["fire"]["fire_exposure"] == "none"

    def test_as_of_falls_back_to_status(self):
        cfg = {"thresholds": {}}
        data = make_data([feature("F1")], {"F1": series([0.5] * 10)}, [rec("F1")], [hotspot(LON, LAT, "2026-10-16")])
        out = events.analyze(data, cfg)
        assert out["landscape"]["as_of"] == AS_OF
        assert out["fields"]["F1"]["fire"]["fire_exposure"] == "inside"

    def test_bad_detection_rows_are_skipped(self):
        dets = [{"lon": None, "lat": None, "date": "2026-10-16"}, {"lon": LON, "lat": LAT, "date": "not a date"},
                {"lon": LON, "lat": LAT}, "garbage", hotspot(LON, LAT, "2026-10-16T10:00:00Z")]
        out = events.analyze(make_data([feature("F1")], {"F1": series([0.5] * 10)}, None, dets), CONFIG)
        assert out["fields"]["F1"]["fire"]["hotspots_inside"] == 1
        assert out["landscape"]["recent_hotspots"] == 1

    def test_output_is_plain_json(self):
        ndvi = [0.80, 0.75, 0.60, 0.45, 0.30, 0.18, 0.10, 0.09, 0.12, 0.18]
        nbr = [0.40, 0.38, 0.35, 0.30, 0.25, 0.12, -0.20, -0.25, -0.10, 0.00]
        out = events.analyze(make_data([feature("F1")], {"F1": series(ndvi, nbr)}, [rec("F1")],
                                       [hotspot(LON, LAT, "2026-10-16")]), CONFIG, prior={"other": {}})
        text = json.dumps(out, allow_nan=False)          # numpy scalars and NaN would both fail here
        assert json.loads(text)["key"] == "events"
        assert set(out) == {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert isinstance(out["method"], str) and out["method"]
        assert all(isinstance(v, str) for v in out["assumptions"].values())
        assert len(json.dumps(out["fields"]["F1"])) < 1024


# ---------------------------------------------------------------- the mock landscape, read only

class TestMock:
    def test_mock_shapes_and_ranges(self):
        D = ROOT / "data" / "mock"
        if not (D / "fields.geojson").exists():
            pytest.skip("no mock data")

        def load(n, default=None):
            p = D / n
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

        data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
                "status": load("status.json"), "weather": load("weather.json", {}),
                "fires": load("fires.json", {"detections": []}), "insights": load("insights.json", {}),
                "plans": load("plans.json"), "route": load("route.json"), "truth": load("truth.json"),
                "usda": load("usda_crop_progress.json"), "chcs": None}
        config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
        out = events.analyze(data, config)
        json.dumps(out, allow_nan=False)
        fids = [f["properties"]["field_id"] for f in data["fields"]["features"]]
        assert out["key"] == "events"
        assert set(out["fields"]) == set(fids)
        zones = {f["properties"]["zone_id"] for f in data["fields"]["features"]}
        assert zones <= set(out["zones"])
        burned = flooded = 0
        for fid, f in out["fields"].items():
            assert set(f["burn"]) == {"burned", "event_date", "pre_date", "pre_nbr", "post_date", "post_nbr", "dnbr",
                                      "dnbr_class", "scar_type", "pre_is_standing_crop", "recovery", "evidence"}
            assert f["burn"]["pre_is_standing_crop"] in (True, False)
            if f["burn"]["pre_is_standing_crop"] and f["burn"]["dnbr_class"] not in (None, "unburned"):
                assert f["burn"]["scar_type"] is None
            assert set(f["flood"]) == {"flood_passes", "flood_first", "flood_last", "flood_days_estimate", "ongoing", "flood_episodes"}
            assert set(f["fire"]) == {"nearest_hotspot_km", "hotspots_within_2km", "hotspots_inside", "fire_exposure", "latest_hotspot_date"}
            assert f["fire"]["fire_exposure"] in ("none", "nearby", "inside")
            assert f["burn"]["dnbr_class"] in (None, "unburned", "low", "moderate_low", "moderate_high", "high")
            if f["burn"]["dnbr"] is not None:
                assert -1.0 <= f["burn"]["dnbr"] <= 2.0
            if f["flood"]["flood_days_estimate"] is not None:
                assert 0 <= f["flood"]["flood_days_estimate"] <= 200
            assert 0 <= f["clear_passes"] <= len(data["timeseries"]["dates"])
            assert len(json.dumps(f)) < 1024
            burned += f["burn"]["burned"]
            flooded += f["flood"]["flood_passes"] > 0
        L = out["landscape"]
        assert L["fields"] == len(fids)
        assert L["burned_fields"] == burned and L["flooded_fields"] == flooded
        assert sum(c["fields"] for c in L["by_class"].values()) == burned
        assert L["as_of"] == config["as_of"]
        assert sum(z["fields"] for z in out["zones"].values()) == len(fids)
