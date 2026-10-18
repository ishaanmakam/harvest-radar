"""observation_quality.py (key "coverage"): how much the satellite actually saw of each field, and how fresh it is.
Small hand-built fixtures pin the arithmetic; one read-only test runs the module on data/mock."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import observation_quality as oq

D = date.fromisoformat
ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"

CONFIG = {"as_of": "2026-10-17", "season_start": "2026-06-01",
          "thresholds": {"clear_fraction_min": 0.6}, "zones_grid": [3, 3]}


def every(start, n, step=5):
    return [(D(start) + timedelta(days=step * i)).isoformat() for i in range(n)]


def geojson(zone_of):
    """fields.geojson with a dummy square per field."""
    ring = [[-93.5, 42.0], [-93.49, 42.0], [-93.49, 42.01], [-93.5, 42.01], [-93.5, 42.0]]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"field_id": fid, "zone_id": z, "area_ha": 10.0, "crop": "corn", "source": "test"}}
        for fid, z in zone_of.items()]}


def zones(ids):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-93.5, 42.0]},
         "properties": {"zone_id": z, "name": z, "centroid": [-93.5, 42.0]}} for z in ids]}


def series(ndvi, clear_frac=None):
    return {"ndvi": list(ndvi), "nbr": [None if v is None else 0.3 for v in ndvi],
            "clear_frac": list(clear_frac) if clear_frac is not None else [0.9 if v is not None else 0.1 for v in ndvi]}


def bundle(dates, ts_fields, zone_of=None, zone_ids=None, **extra):
    zone_of = zone_of if zone_of is not None else {fid: "Z01" for fid in ts_fields}
    data = {"fields": geojson(zone_of), "zones": zones(zone_ids or sorted(set(zone_of.values()))),
            "timeseries": {"source": "test", "dates": list(dates), "fields": ts_fields},
            "status": {"as_of": CONFIG["as_of"], "fields": []},
            "weather": {}, "fires": {"detections": []}, "insights": None, "plans": None, "route": None,
            "truth": None, "usda": None, "chcs": None}
    data.update(extra)
    return data


# six passes: 2026-09-16, 09-21, 09-26, 10-01, 10-06, 10-11
SIX = every("2026-09-16", 6)
# A: clear on 09-16, 10-01, 10-06 (3 of 6). Gaps: 09-16 -> 10-01 = 15 d, 10-01 -> 10-06 = 5 d, 10-06 -> as_of 10-17 = 11 d.
A = series([0.5, None, None, 0.4, 0.3, None], clear_frac=[0.9, 0.1, 0.3, 0.8, 0.7, 0.2])
# B: clear on every pass (6 of 6); last clear 10-11, 6 days before as_of.
B = series([0.6, 0.6, 0.5, 0.4, 0.3, 0.2], clear_frac=[1.0] * 6)


class TestOneField:
    def test_counts_fraction_and_dates(self):
        out = oq.analyze(bundle(SIX, {"A": A}), CONFIG)
        f = out["fields"]["A"]
        assert out["key"] == "coverage"
        assert f["clear_passes"] == 3 and f["total_passes"] == 6
        assert f["clear_fraction_season"] == pytest.approx(0.5)
        assert f["last_clear_date"] == "2026-10-06"
        assert f["days_since_last_clear"] == 11                      # 10-06 -> 10-17
        assert f["longest_gap_days"] == 15                           # 09-16 -> 10-01
        assert f["mean_clear_frac"] == pytest.approx(0.5)            # (0.9+0.1+0.3+0.8+0.7+0.2)/6

    def test_staleness_and_confidence_bands(self):
        f = oq.analyze(bundle(SIX, {"A": A}), CONFIG)["fields"]["A"]
        assert f["staleness"] == "aging"                             # 11 d: more than 6, at most 12
        assert f["data_confidence"] == "low"                         # 3 clear passes < 8

    def test_next_clear_from_zone_rate(self):
        # A is alone in its zone: zone clear rate 3/6 = 0.5, expected wait = 5 d revisit / 0.5 = 10 d
        f = oq.analyze(bundle(SIX, {"A": A}), CONFIG)["fields"]["A"]
        assert f["p_next_pass_clear"] == pytest.approx(0.5)
        assert f["expected_days_to_next_clear"] == pytest.approx(10.0)

    def test_note_is_short_plain_text(self):
        f = oq.analyze(bundle(SIX, {"A": A}), CONFIG)["fields"]["A"]
        assert isinstance(f["note"], str) and 0 < len(f["note"]) < 200
        assert "2026-10-06" in f["note"]
        assert "—" not in f["note"]

    def test_clear_on_as_of_says_today_not_zero_days_ago(self):
        dates = every("2026-10-02", 4)                                # 10-02, 10-07, 10-12, 10-17 = as_of
        f = oq.analyze(bundle(dates, {"A": series([0.5] * 4)}), CONFIG)["fields"]["A"]
        assert f["days_since_last_clear"] == 0 and f["staleness"] == "fresh"
        assert "today" in f["note"] and "0 days ago" not in f["note"]

    def test_one_day_ago_is_singular(self):
        dates = every("2026-10-01", 4)                                # 10-01, 10-06, 10-11, 10-16: one day before as_of
        f = oq.analyze(bundle(dates, {"A": series([0.5] * 4)}), CONFIG)["fields"]["A"]
        assert f["days_since_last_clear"] == 1
        assert "1 day ago" in f["note"] and "1 days ago" not in f["note"]


class TestZonesPooled:
    def test_zone_rate_pools_field_passes(self):
        # A 3/6 + B 6/6 in the same zone -> 9/12 = 0.75 -> expected wait 5 / 0.75 = 6.67 d
        out = oq.analyze(bundle(SIX, {"A": A, "B": B}), CONFIG)
        assert out["zones"]["Z01"]["clear_rate"] == pytest.approx(0.75)
        assert out["fields"]["A"]["p_next_pass_clear"] == pytest.approx(0.75)
        assert out["fields"]["B"]["expected_days_to_next_clear"] == pytest.approx(6.7, abs=0.05)
        assert out["zones"]["Z01"]["fields"] == 2

    def test_zone_stale_count_and_fresh_field(self):
        out = oq.analyze(bundle(SIX, {"A": A, "B": B}), CONFIG)
        assert out["fields"]["B"]["staleness"] == "fresh"            # 6 d is the fresh limit
        assert out["zones"]["Z01"]["fields_stale"] == 0
        assert out["zones"]["Z01"]["fields_fresh"] == 1

    def test_zone_without_fields_still_listed(self):
        out = oq.analyze(bundle(SIX, {"A": A}, zone_ids=["Z01", "Z09"]), CONFIG)
        assert out["zones"]["Z09"]["clear_rate"] is None
        assert out["zones"]["Z09"]["fields"] == 0


class TestConfidenceRules:
    DATES = every("2026-06-03", 28)                                   # 06-03 .. 10-16, as in the mock

    def test_high_needs_15_passes_and_no_gap_over_15(self):
        f = oq.analyze(bundle(self.DATES, {"F": series([0.5] * 28)}), CONFIG)["fields"]["F"]
        assert f["clear_passes"] == 28 and f["longest_gap_days"] == 5
        assert f["data_confidence"] == "high" and f["staleness"] == "fresh"

    def test_gap_of_20_days_is_medium(self):
        nd = [0.5] * 28
        nd[10] = nd[11] = nd[12] = None                               # 07-23, 07-28, 08-02 cloudy: 07-18 -> 08-07 = 20 d
        f = oq.analyze(bundle(self.DATES, {"F": series(nd)}), CONFIG)["fields"]["F"]
        assert f["clear_passes"] == 25 and f["longest_gap_days"] == 20
        assert f["data_confidence"] == "medium"

    def test_gap_over_30_days_is_low(self):
        nd = [0.5] * 28
        for i in range(10, 16):                                       # 07-23 .. 08-17 cloudy: 07-18 -> 08-22 = 35 d
            nd[i] = None
        f = oq.analyze(bundle(self.DATES, {"F": series(nd)}), CONFIG)["fields"]["F"]
        assert f["longest_gap_days"] == 35 and f["data_confidence"] == "low"

    def test_stale_field_is_low_even_with_many_passes(self):
        nd = [0.5] * 28
        for i in range(24, 28):                                       # nothing clear after 09-26: 21 d before as_of
            nd[i] = None
        f = oq.analyze(bundle(self.DATES, {"F": series(nd)}), CONFIG)["fields"]["F"]
        assert f["clear_passes"] == 24 and f["days_since_last_clear"] == 21
        assert f["staleness"] == "stale" and f["data_confidence"] == "low"

    def test_leading_gap_counts_from_first_scheduled_pass(self):
        nd = [None] * 28
        for i in range(18, 28):                                       # ten clear passes 09-01 .. 10-16; nothing before
            nd[i] = 0.5
        f = oq.analyze(bundle(self.DATES, {"F": series(nd)}), CONFIG)["fields"]["F"]
        assert f["clear_passes"] == 10
        assert f["longest_gap_days"] == 90                            # 06-03 -> 09-01: the satellite never saw it before September
        assert f["data_confidence"] == "low"                          # a gap over 30 d wins

    def test_eight_to_fourteen_passes_with_small_gaps_is_medium(self):
        dates = every("2026-08-28", 10)                               # 08-28 .. 10-12, all clear: 10 passes, gap 5, 5 d old
        f = oq.analyze(bundle(dates, {"F": series([0.5] * 10)}), CONFIG)["fields"]["F"]
        assert f["clear_passes"] == 10 and f["longest_gap_days"] == 5
        assert f["staleness"] == "fresh" and f["data_confidence"] == "medium"


class TestEdges:
    def test_all_null_series(self):
        nd = [None] * 6
        out = oq.analyze(bundle(SIX, {"N": series(nd, clear_frac=[0.1, 0.2, 0.0, 0.3, 0.1, 0.2])}), CONFIG)
        f = out["fields"]["N"]
        assert f["clear_passes"] == 0 and f["total_passes"] == 6
        assert f["clear_fraction_season"] == 0.0
        assert f["last_clear_date"] is None and f["days_since_last_clear"] is None
        assert f["longest_gap_days"] == 31                            # 09-16 -> 10-17: the whole record
        assert f["staleness"] == "stale" and f["data_confidence"] == "low"
        assert f["mean_clear_frac"] == pytest.approx(0.15)
        assert f["expected_days_to_next_clear"] is not None           # the zone rate is floored, never a division by zero

    def test_never_seen_field_keeps_an_honest_none_and_a_printable_note(self):
        # No clear pass means no last clear date, so days_since_last_clear stays None rather than a made-up number.
        # Callers that print a sentence must use the note (or longest_gap_days, the days without a look) instead of
        # interpolating that None.
        out = oq.analyze(bundle(SIX, {"N": series([None] * 6)}), CONFIG)
        f = out["fields"]["N"]
        assert f["last_clear_date"] is None and f["days_since_last_clear"] is None
        assert f["staleness"] == "stale" and f["data_confidence"] == "low"
        assert f["longest_gap_days"] == 31                            # 09-16 -> 10-17: the whole record without a look
        assert f["note"].startswith("Never seen clear") and "None" not in f["note"]
        assert "31 days" in f["note"]                                 # the note carries the number a caller can say out loud

    def test_field_absent_from_timeseries_is_none_with_note(self):
        out = oq.analyze(bundle(SIX, {"A": A}, zone_of={"A": "Z01", "GHOST": "Z01"}), CONFIG)
        assert out["fields"]["GHOST"] is None
        assert any("GHOST" in n or "timeseries" in n for n in out["landscape"]["notes"])
        assert out["zones"]["Z01"]["fields"] == 1                     # only fields with a series count

    def test_one_pass_only(self):
        out = oq.analyze(bundle(["2026-10-11"], {"A": series([0.4])}), CONFIG)
        f = out["fields"]["A"]
        assert f["clear_passes"] == 1 and f["total_passes"] == 1 and f["clear_fraction_season"] == 1.0
        assert f["longest_gap_days"] == 6 and f["staleness"] == "fresh"
        assert f["data_confidence"] == "low"

    def test_missing_weather_fires_insights_and_crop_other(self):
        data = bundle(SIX, {"A": A})
        data["fields"]["features"][0]["properties"]["crop"] = "other"
        del data["weather"]
        data["fires"] = None
        data["status"] = None
        out = oq.analyze(data, CONFIG)
        assert out["fields"]["A"]["clear_passes"] == 3

    def test_no_timeseries_degrades_to_none(self):
        out = oq.analyze(bundle(SIX, {"A": A}, timeseries=None), CONFIG)
        assert out["key"] == "coverage"
        assert out["fields"]["A"] is None
        assert out["landscape"]["notes"]
        assert out["zones"]["Z01"]["clear_rate"] is None

    def test_no_timeseries_landscape_has_every_key(self):
        # A consumer reading landscape keys must find the same key set on an empty season as on a full one.
        full = oq.analyze(bundle(SIX, {"A": A}), CONFIG)["landscape"]
        empty = oq.analyze(bundle(SIX, {"A": A}, timeseries=None), CONFIG)["landscape"]
        assert set(empty) == set(full)
        assert empty["passes_total"] == 0 and empty["fields"] == 0 and empty["fields_without_series"] == 1
        assert empty["clear_rate_season"] is None and empty["latest_clear_date"] is None
        assert empty["as_of"] == CONFIG["as_of"]
        assert empty["by_month"] == {} and empty["clear_share_by_date"] == {}
        assert empty["clear_rate_by_zone"] == {"Z01": None}
        assert empty["staleness_counts"] == {"fresh": 0, "aging": 0, "stale": 0}
        assert empty["staleness_share"] == {"fresh": None, "aging": None, "stale": None}
        assert empty["confidence_counts"] == {"high": 0, "medium": 0, "low": 0}
        json.dumps(empty)

    def test_no_clear_frac_array(self):
        s = {"ndvi": [0.5, None, 0.4, None, 0.3, 0.2], "nbr": [0.3] * 6}
        f = oq.analyze(bundle(SIX, {"A": s}), CONFIG)["fields"]["A"]
        assert f["clear_passes"] == 4 and f["mean_clear_frac"] is None

    def test_short_series_counts_missing_passes_as_not_clear(self):
        s = series([0.5, 0.4])                                        # two values for six dates
        out = oq.analyze(bundle(SIX, {"A": s}), CONFIG)
        f = out["fields"]["A"]
        assert f["total_passes"] == 6 and f["clear_passes"] == 2
        assert any("align" in n or "shorter" in n for n in out["landscape"]["notes"])

    def test_garbage_input_never_raises(self):
        out = oq.analyze({"timeseries": {"dates": ["not a date", 5], "fields": {"A": "nope"}}}, {"as_of": "2026-10-17"})
        assert out["key"] == "coverage" and isinstance(out["landscape"]["notes"], list)
        out = oq.analyze({}, {})
        assert out["key"] == "coverage"
        out = oq.analyze(None, None)
        assert out["key"] == "coverage"

    def test_prior_is_accepted_and_ignored(self):
        a = oq.analyze(bundle(SIX, {"A": A}), CONFIG, prior=None)
        b = oq.analyze(bundle(SIX, {"A": A}), CONFIG, prior={"weather": {"anything": 1}})
        assert a["fields"] == b["fields"]


class TestLandscape:
    def test_by_month_passes_and_rate(self):
        out = oq.analyze(bundle(SIX, {"A": A, "B": B}), CONFIG)
        bm = out["landscape"]["by_month"]
        assert list(bm) == ["2026-09", "2026-10"]
        assert bm["2026-09"]["passes"] == 3 and bm["2026-10"]["passes"] == 3
        assert bm["2026-09"]["clear_rate"] == pytest.approx(4 / 6, abs=1e-3)   # A 1 of 3, B 3 of 3 (rounded to 3 dp)
        assert bm["2026-10"]["clear_rate"] == pytest.approx(5 / 6, abs=1e-3)   # A 2 of 3, B 3 of 3

    def test_latest_clear_and_shares(self):
        out = oq.analyze(bundle(SIX, {"A": A, "B": B}), CONFIG)
        ls = out["landscape"]
        assert ls["latest_clear_date"] == "2026-10-11"                # B was clear on the last pass
        assert ls["passes_total"] == 6 and ls["fields"] == 2
        assert ls["clear_rate_season"] == pytest.approx(0.75)
        sh = ls["staleness_share"]
        assert sh["fresh"] == pytest.approx(0.5) and sh["aging"] == pytest.approx(0.5) and sh["stale"] == 0.0
        assert sum(ls["staleness_counts"].values()) == 2
        assert ls["clear_rate_by_zone"]["Z01"] == pytest.approx(0.75)
        assert ls["clear_share_by_date"]["2026-09-21"] == pytest.approx(0.5)

    def test_output_is_plain_json(self):
        out = oq.analyze(bundle(SIX, {"A": A, "B": B}), CONFIG)
        text = json.dumps(out)                                        # raises on numpy scalars
        assert "NaN" not in text and "Infinity" not in text
        assert set(out) == {"key", "fields", "landscape", "zones", "assumptions", "method"}
        assert isinstance(out["method"], str) and out["assumptions"]
        for v in out["assumptions"].values():
            assert isinstance(v, str)

    def test_assumptions_say_only_what_the_data_shows(self):
        a = oq.ASSUMPTIONS
        # ESA quotes the 5-day combined revisit at the equator; mid-latitudes get overlapping swaths and shorter waits.
        assert "equator" in a["revisit_days"] and "mid-latitudes (ESA" not in a["revisit_days"]
        # The repo has no pipeline step that withholds NDVI below clear_fraction_min; null simply means a cloudy look.
        assert "pipeline withholds" not in a["clear_pass"] and "null" in a["clear_pass"]


@pytest.mark.skipif(not (MOCK / "timeseries.json").exists(), reason="data/mock not generated")
def test_mock_season_shapes_and_ranges():
    def load(n, default=None):
        p = MOCK / n
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    data = {"fields": load("fields.geojson"), "zones": load("zones.geojson"), "timeseries": load("timeseries.json"),
            "status": load("status.json"), "weather": load("weather.json", {}), "fires": load("fires.json", {"detections": []}),
            "insights": load("insights.json", {}), "plans": load("plans.json"), "route": load("route.json"),
            "truth": load("truth.json"), "usda": load("usda_crop_progress.json"),
            "chcs": json.loads((ROOT / "config" / "chcs.json").read_text(encoding="utf-8"))}
    config = json.loads((ROOT / "config" / "region.json").read_text(encoding="utf-8"))
    out = oq.analyze(data, config)
    n_dates = len(data["timeseries"]["dates"])
    ids = [f["properties"]["field_id"] for f in data["fields"]["features"]]

    assert out["key"] == "coverage"
    assert set(ids) <= set(out["fields"])
    done = {k: v for k, v in out["fields"].items() if v is not None}
    assert len(done) >= 0.9 * len(ids)
    for fid, f in done.items():
        assert f["total_passes"] == n_dates
        assert 0 <= f["clear_passes"] <= n_dates
        assert 0.0 <= f["clear_fraction_season"] <= 1.0
        assert f["staleness"] in ("fresh", "aging", "stale")
        assert f["data_confidence"] in ("high", "medium", "low")
        assert f["mean_clear_frac"] is None or 0.0 <= f["mean_clear_frac"] <= 1.0
        assert f["p_next_pass_clear"] is None or 0.0 < f["p_next_pass_clear"] <= 1.0
        assert f["expected_days_to_next_clear"] is None or f["expected_days_to_next_clear"] >= oq.REVISIT_DAYS
        assert f["longest_gap_days"] is None or 0 <= f["longest_gap_days"] <= 200
        if f["last_clear_date"]:
            assert data["timeseries"]["dates"][0] <= f["last_clear_date"] <= config["as_of"]
            assert f["days_since_last_clear"] >= 0
        assert len(json.dumps(f)) < 1024
    ls = out["landscape"]
    assert ls["passes_total"] == n_dates
    assert ls["latest_clear_date"] is not None and ls["latest_clear_date"] <= config["as_of"]
    assert all(k[:4] == "2026" and len(k) == 7 for k in ls["by_month"])
    assert sum(m["passes"] for m in ls["by_month"].values()) == n_dates
    assert abs(sum(ls["staleness_share"].values()) - 1.0) < 1e-6
    assert set(out["zones"]) >= {f["properties"]["zone_id"] for f in data["zones"]["features"]}
    for z in out["zones"].values():
        assert z["clear_rate"] is None or 0.0 <= z["clear_rate"] <= 1.0
        assert z["fields_stale"] >= 0
    json.dumps(out)
