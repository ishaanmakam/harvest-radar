"""validate_usda: the satellite percent-harvested curve for the AOI against USDA NASS Crop Progress (Iowa Central
district, corn and soybeans). No network anywhere: a fake HTTP transport answers QuickStats queries from a
recorded-shape sample payload, and the cache makes reruns need no transport at all."""
import json
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import validate_usda as vu

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


# ----------------------------------------------------------------------------- fixtures / builders
def field(fid, stage, harvest_date=None):
    return {"field_id": fid, "zone_id": "Z01", "stage": stage, "peak_date": "2026-09-01", "peak_ndvi": 0.7,
            "harvest_date": harvest_date, "predicted_harvest_start": None, "predicted_harvest_end": None,
            "sowing_deadline": None, "burn_date": None, "burn_evidence": "none", "confidence": 0.9, "notes": ""}


def status_doc(*fields):
    return {"as_of": "2026-10-17", "peak_to_harvest_days": 32, "fields": list(fields)}


def feature(fid, area_ha, crop="corn"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
            "properties": {"field_id": fid, "zone_id": "Z01", "area_ha": area_ha, "crop": crop, "source": "mock"}}


def fields_doc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


STATUS = status_doc(field("F1", "harvested", "2026-10-01"), field("F2", "harvested", "2026-10-05"),
                    field("F3", "maturing"), field("F4", "unknown"))
FIELDS = fields_doc(feature("F1", 2.0), feature("F2", 1.0, "soybeans"), feature("F3", 1.0), feature("F4", 10.0))

# A QuickStats record as the API returns it (all columns), so the parser is exercised on the real shape.
RECORD = {"source_desc": "SURVEY", "sector_desc": "CROPS", "group_desc": "FIELD CROPS", "commodity_desc": "CORN",
          "class_desc": "ALL CLASSES", "prodn_practice_desc": "ALL PRODUCTION PRACTICES",
          "util_practice_desc": "ALL UTILIZATION PRACTICES", "statisticcat_desc": "PROGRESS",
          "unit_desc": "PCT HARVESTED", "short_desc": "CORN - PROGRESS, MEASURED IN PCT HARVESTED",
          "domain_desc": "TOTAL", "domaincat_desc": "NOT SPECIFIED", "agg_level_desc": "AGRICULTURAL DISTRICT",
          "state_ansi": "19", "state_fips_code": "19", "state_alpha": "IA", "state_name": "IOWA", "asd_code": "50",
          "asd_desc": "CENTRAL", "county_ansi": "", "county_code": "", "county_name": "", "region_desc": "",
          "zip_5": "", "watershed_code": "00000000", "watershed_desc": "", "congr_district_code": "",
          "country_code": "9000", "country_name": "UNITED STATES", "location_desc": "IOWA, CENTRAL",
          "year": 2025, "freq_desc": "WEEKLY", "begin_code": "41", "end_code": "41",
          "reference_period_desc": "WEEK #41", "week_ending": "2025-10-12",
          "load_time": "2025-10-14 12:00:00", "Value": "38", "CV (%)": ""}


def record(week_ending, value, commodity="CORN", district="CENTRAL", year=None):
    we = date.fromisoformat(week_ending)
    return dict(RECORD, commodity_desc=commodity, asd_desc=district, week_ending=week_ending, Value=value,
                year=year or we.year, location_desc=f"IOWA, {district}",
                short_desc=f"{commodity} - PROGRESS, MEASURED IN PCT HARVESTED")


def payload(*records):
    return {"data": list(records)}


class FakeHttp:
    """Records requests; answers from a queue of (status, body) tuples (the last one repeats)."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, payload())]
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, doc = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return status, json.dumps(doc).encode()


def query_of(call):
    return {k: v[0] for k, v in parse_qs(urlparse(call["url"]).query).items()}


# ----------------------------------------------------------------------------- 1. satellite curve
class TestSatelliteCurve:
    def test_share_of_fields_and_area_harvested_on_or_before_each_date(self):
        curve = vu.satellite_curve(STATUS, FIELDS, ["2026-09-30", "2026-10-01", "2026-10-06"])
        assert [p["date"] for p in curve] == ["2026-09-30", "2026-10-01", "2026-10-06"]
        assert [p["pct_fields"] for p in curve] == [0.0, 33.3, 66.7]      # 3 cropped fields (F4 unknown excluded)
        assert [p["pct_area"] for p in curve] == [0.0, 50.0, 75.0]        # 4.0 ha cropped: 2.0 then 3.0 harvested

    def test_fields_with_no_crop_signal_are_not_in_the_denominator(self):
        summary = vu.population(STATUS, FIELDS)
        assert summary["n_fields"] == 4 and summary["n_cropped"] == 3 and summary["area_ha"] == 4.0
        assert summary["n_excluded_unknown"] == 1

    def test_crop_filter_keeps_only_matching_fields(self):
        curve = vu.satellite_curve(STATUS, FIELDS, ["2026-10-06"], crop="soybeans")
        assert curve[0]["pct_fields"] == 100.0 and curve[0]["pct_area"] == 100.0
        assert vu.population(STATUS, FIELDS, crop="soybeans")["n_cropped"] == 1

    def test_fields_missing_from_the_geojson_count_by_number_but_not_by_area(self):
        curve = vu.satellite_curve(STATUS, fields_doc(feature("F1", 2.0)), ["2026-10-06"])
        assert curve[0]["pct_fields"] == 66.7 and curve[0]["pct_area"] == 100.0

    def test_weekly_curve_lands_on_the_sundays_inside_the_season_window(self):
        dates = ["2026-09-24", "2026-09-29", "2026-10-04", "2026-10-09", "2026-10-14"]
        weekly = vu.weekly_curve(STATUS, FIELDS, dates)
        assert [p["week_ending"] for p in weekly] == ["2026-09-27", "2026-10-04", "2026-10-11"]
        assert all(date.fromisoformat(p["week_ending"]).weekday() == 6 for p in weekly)
        assert [p["pct_fields"] for p in weekly] == [0.0, 33.3, 66.7]

    def test_week_endings_are_every_sunday_between_two_dates_inclusive(self):
        assert vu.week_endings(date(2026, 10, 11), date(2026, 10, 25)) == [date(2026, 10, 11), date(2026, 10, 18), date(2026, 10, 25)]
        assert vu.week_endings(date(2026, 10, 12), date(2026, 10, 17)) == []


# ----------------------------------------------------------------------------- 2. QuickStats query + parsing
class TestQuickStatsQuery:
    def test_params_describe_district_level_percent_harvested_for_one_commodity_and_year(self):
        p = vu.quickstats_params("SOYBEANS", 2026, key="k123")
        assert p["key"] == "k123" and p["format"] == "JSON"
        assert p["source_desc"] == "SURVEY" and p["sector_desc"] == "CROPS"
        assert p["commodity_desc"] == "SOYBEANS" and p["statisticcat_desc"] == "PROGRESS"
        assert p["unit_desc"] == "PCT HARVESTED" and p["agg_level_desc"] == "AGRICULTURAL DISTRICT"
        assert p["state_alpha"] == "IA" and p["asd_desc"] == "CENTRAL" and p["year"] == "2026"

    def test_year_range_query_uses_the_quickstats_ge_operator(self):
        p = vu.quickstats_params("CORN", 2021, key="k", year_op="GE")
        assert "year" not in p and p["year__GE"] == "2021"

    def test_url_is_a_get_on_api_get_with_the_params_encoded(self):
        url = vu.quickstats_url({"key": "k", "unit_desc": "PCT HARVESTED"})
        assert url.startswith("https://quickstats.nass.usda.gov/api/api_GET/?")
        assert query_of({"url": url}) == {"key": "k", "unit_desc": "PCT HARVESTED"}


class TestParseProgress:
    def test_recorded_payload_becomes_sorted_weekly_rows(self):
        rows = vu.parse_progress(payload(record("2025-10-12", "38"), record("2025-10-05", "19"), record("2025-10-19", "61")))
        assert rows == [{"week_ending": "2025-10-05", "year": 2025, "pct_harvested": 19.0},
                        {"week_ending": "2025-10-12", "year": 2025, "pct_harvested": 38.0},
                        {"week_ending": "2025-10-19", "year": 2025, "pct_harvested": 61.0}]

    def test_other_districts_and_non_numeric_values_are_skipped(self):
        rows = vu.parse_progress(payload(record("2025-10-12", "38"), record("2025-10-12", "44", district="NORTH CENTRAL"),
                                         record("2025-10-19", "(D)"), record("2025-10-26", " (NA)")), district="CENTRAL")
        assert rows == [{"week_ending": "2025-10-12", "year": 2025, "pct_harvested": 38.0}]

    def test_values_with_thousands_separators_and_decimals_parse(self):
        rows = vu.parse_progress(payload(record("2025-10-12", "1,0"), record("2025-10-19", "61.5")))
        assert [r["pct_harvested"] for r in rows] == [10.0, 61.5]

    def test_empty_payload_gives_no_rows(self):
        assert vu.parse_progress(payload()) == []
        assert vu.parse_progress({}) == []


# ----------------------------------------------------------------------------- 2b. client with fallback year
class TestNassClient:
    def test_fetches_the_requested_year_when_it_has_data(self):
        http = FakeHttp((200, payload(record("2026-10-04", "12"), record("2026-10-11", "30"))))
        series = vu.NassClient("k", http=http).fetch("CORN", 2026)
        assert series["year"] == 2026 and series["requested_year"] == 2026
        assert [r["pct_harvested"] for r in series["rows"]] == [12.0, 30.0]
        assert len(http.calls) == 1 and http.calls[0]["method"] == "GET"
        q = query_of(http.calls[0])
        assert q["commodity_desc"] == "CORN" and q["year"] == "2026" and q["key"] == "k"
        assert "key" not in series["query"]                          # the cache never records the key

    def test_falls_back_to_the_latest_year_with_data(self):
        http = FakeHttp((200, payload()),
                        (200, payload(record("2024-10-13", "40"), record("2025-10-12", "38"), record("2025-10-19", "61"))))
        series = vu.NassClient("k", http=http).fetch("CORN", 2026)
        assert series["year"] == 2025 and series["requested_year"] == 2026
        assert [r["week_ending"] for r in series["rows"]] == ["2025-10-12", "2025-10-19"]
        assert query_of(http.calls[1])["year__GE"] == "2021" and "year" not in query_of(http.calls[1])

    def test_no_data_error_from_quickstats_counts_as_empty(self):
        http = FakeHttp((400, {"error": ["bad request - no data"]}), (200, payload(record("2025-10-12", "38"))))
        assert vu.NassClient("k", http=http).fetch("CORN", 2026)["year"] == 2025

    def test_other_http_errors_raise_with_the_api_message(self):
        http = FakeHttp((401, {"error": ["unauthorized"]}))
        with pytest.raises(vu.NassError, match="unauthorized"):
            vu.NassClient("bad", http=http).fetch("CORN", 2026)

    def test_no_data_in_any_recent_year_raises(self):
        http = FakeHttp((200, payload()))
        with pytest.raises(vu.NassError, match="no PCT HARVESTED"):
            vu.NassClient("k", http=http).fetch("CORN", 2026)


# ----------------------------------------------------------------------------- 2c. cache
class TestCache:
    def test_first_run_fetches_and_writes_the_cache(self, tmp_path):
        http = FakeHttp((200, payload(record("2026-10-11", "30"))))
        doc = vu.load_or_fetch(tmp_path, 2026, client=vu.NassClient("k", http=http))
        assert set(doc["series"]) == {"CORN", "SOYBEANS"} and doc["requested_year"] == 2026
        assert doc["state"] == "IA" and doc["district"] == "CENTRAL" and doc["source"] == "nass_quickstats"
        assert len(http.calls) == 2
        cached = json.loads((tmp_path / "usda_crop_progress.json").read_text())
        assert cached == doc and "key" not in json.dumps(cached)

    def test_second_run_uses_the_cache_without_touching_the_transport(self, tmp_path):
        http = FakeHttp((200, payload(record("2026-10-11", "30"))))
        first = vu.load_or_fetch(tmp_path, 2026, client=vu.NassClient("k", http=http))
        again = vu.load_or_fetch(tmp_path, 2026, client=None)
        assert again == first and len(http.calls) == 2

    def test_refresh_refetches_even_with_a_cache(self, tmp_path):
        http = FakeHttp((200, payload(record("2026-10-11", "30"))))
        client = vu.NassClient("k", http=http)
        vu.load_or_fetch(tmp_path, 2026, client=client)
        vu.load_or_fetch(tmp_path, 2026, client=client, refresh=True)
        assert len(http.calls) == 4

    def test_cache_for_another_district_or_missing_commodity_is_not_reused(self, tmp_path):
        http = FakeHttp((200, payload(record("2026-10-11", "30"))))
        vu.load_or_fetch(tmp_path, 2026, client=vu.NassClient("k", http=http), commodities=("CORN",))
        vu.load_or_fetch(tmp_path, 2026, client=vu.NassClient("k", http=http))     # needs SOYBEANS too
        assert len(http.calls) == 3

    def test_no_cache_and_no_client_is_a_clear_error(self, tmp_path):
        with pytest.raises(vu.NassError, match="NASS_API_KEY"):
            vu.load_or_fetch(tmp_path, 2026, client=None)

    def test_sample_payload_has_the_quickstats_shape_and_seeds_the_cache(self, tmp_path):
        rows = vu.parse_progress(vu.sample_payload("SOYBEANS"))
        assert len(rows) >= 5 and rows[0]["pct_harvested"] < rows[-1]["pct_harvested"]
        doc = vu.load_or_fetch(tmp_path, 2026, client=vu.NassClient("", http=vu.sample_http))
        assert doc["series"]["CORN"]["rows"] and doc["series"]["SOYBEANS"]["rows"]
        assert doc["source"] == "sample"


# ----------------------------------------------------------------------------- 3. alignment + metrics
class TestAlignment:
    def test_same_year_rows_align_on_their_own_week_ending(self):
        rows = [{"week_ending": "2026-10-04", "year": 2026, "pct_harvested": 20.0},
                {"week_ending": "2026-10-11", "year": 2026, "pct_harvested": 50.0}]
        out = vu.align(STATUS, FIELDS, rows, season_year=2026, first=date(2026, 9, 24), last=date(2026, 10, 14))
        assert [(r["week_ending"], r["usda_week_ending"]) for r in out] == [("2026-10-04", "2026-10-04"), ("2026-10-11", "2026-10-11")]
        assert [r["usda_pct"] for r in out] == [20.0, 50.0]
        assert [r["satellite_pct_fields"] for r in out] == [33.3, 66.7]
        assert [r["satellite_pct_area"] for r in out] == [50.0, 75.0]

    def test_rows_from_a_fallback_year_map_to_the_same_week_number_in_the_season_year(self):
        # 2025-10-12 is NASS week 41 of 2025; week 41 of 2026 ends on Sunday 2026-10-11
        rows = [{"week_ending": "2025-10-12", "year": 2025, "pct_harvested": 38.0}]
        out = vu.align(STATUS, FIELDS, rows, season_year=2026, first=date(2026, 9, 24), last=date(2026, 10, 14))
        assert out[0]["week_ending"] == "2026-10-11" and out[0]["usda_week_ending"] == "2025-10-12"
        assert vu.season_week_ending(date(2025, 10, 12), 2026) == date(2026, 10, 11)
        assert vu.season_week_ending(date(2024, 10, 13), 2026) == date(2026, 10, 11)

    def test_weeks_outside_the_satellite_window_are_dropped(self):
        rows = [{"week_ending": "2026-09-20", "year": 2026, "pct_harvested": 5.0},
                {"week_ending": "2026-10-11", "year": 2026, "pct_harvested": 50.0},
                {"week_ending": "2026-10-18", "year": 2026, "pct_harvested": 80.0}]
        out = vu.align(STATUS, FIELDS, rows, season_year=2026, first=date(2026, 9, 24), last=date(2026, 10, 14))
        assert [r["week_ending"] for r in out] == ["2026-10-11"]


class TestMetrics:
    SAT = [(date(2026, 9, 27), 10.0), (date(2026, 10, 4), 30.0), (date(2026, 10, 11), 100.0)]
    USDA = [(date(2026, 9, 27), 0.0), (date(2026, 10, 4), 20.0), (date(2026, 10, 11), 50.0), (date(2026, 10, 18), 80.0)]

    def test_crossing_date_interpolates_linearly_between_weekly_points(self):
        assert vu.crossing_date(self.SAT, 50.0) == date(2026, 10, 6)        # 30 -> 100 over 7 days: 50 reached after 2 days
        assert vu.crossing_date(self.USDA, 50.0) == date(2026, 10, 11)      # hits 50 exactly on a point
        assert vu.crossing_date([(date(2026, 10, 4), 60.0)], 50.0) == date(2026, 10, 4)   # already past on the first point

    def test_crossing_date_is_none_when_the_curve_never_reaches_the_level(self):
        assert vu.crossing_date(self.USDA[:2], 50.0) is None
        assert vu.crossing_date([], 50.0) is None

    def test_mean_abs_diff_and_lag_from_aligned_rows(self):
        aligned = [{"week_ending": "2026-10-04", "usda_pct": 20.0, "satellite_pct_fields": 30.0, "satellite_pct_area": 25.0},
                   {"week_ending": "2026-10-11", "usda_pct": 50.0, "satellite_pct_fields": 100.0, "satellite_pct_area": 90.0}]
        curve = [{"date": "2026-09-27", "pct_fields": 10.0, "pct_area": 5.0},        # the satellite curve on its
                 {"date": "2026-10-04", "pct_fields": 30.0, "pct_area": 25.0},       # observation dates places the
                 {"date": "2026-10-11", "pct_fields": 100.0, "pct_area": 90.0}]      # crossing, not the Sunday sample
        m = vu.metrics(aligned, curve, [{"week_ending": w.isoformat(), "pct_harvested": v} for w, v in self.USDA])
        assert m["weeks_compared"] == 2
        assert m["mean_abs_diff_pp_fields"] == 30.0 and m["mean_abs_diff_pp_area"] == 22.5
        assert m["satellite_50pct_date"] == "2026-10-06" and m["usda_50pct_date"] == "2026-10-11"
        assert m["lag_days_50pct"] == -5                                   # satellite crosses 50% five days earlier

    def test_metrics_with_nothing_to_compare_are_null_not_errors(self):
        m = vu.metrics([], [], [])
        assert m["weeks_compared"] == 0 and m["mean_abs_diff_pp_fields"] is None and m["lag_days_50pct"] is None


# ----------------------------------------------------------------------------- 4. end to end on the mock season
class TestRunOnMock:
    @pytest.fixture
    def mock_dir(self, tmp_path):
        for name in ("status.json", "fields.geojson", "timeseries.json"):
            shutil.copy(MOCK / name, tmp_path / name)
        return tmp_path

    def test_run_writes_validation_and_cache_and_prints_a_report(self, mock_dir, capsys):
        # 2026 has no district data yet -> fall back to 2025 for both commodities
        http = FakeHttp((200, payload()),
                        (200, payload(record("2025-09-28", "4"), record("2025-10-05", "12"), record("2025-10-12", "38"),
                                      record("2025-10-19", "61"), record("2025-10-26", "80"))),
                        (200, payload()),
                        (200, payload(record("2025-09-28", "10", "SOYBEANS"), record("2025-10-05", "35", "SOYBEANS"),
                                      record("2025-10-12", "66", "SOYBEANS"), record("2025-10-19", "86", "SOYBEANS"))))
        doc = vu.run(mock_dir, client=vu.NassClient("k", http=http))
        assert (mock_dir / "validation_usda.json").exists() and (mock_dir / "usda_crop_progress.json").exists()
        assert doc["season_year"] == 2026 and doc["as_of"] == "2026-10-17"
        assert doc["satellite"]["n_cropped"] > 0 and len(doc["satellite"]["curve"]) == 28
        assert doc["satellite"]["weekly"][0]["week_ending"] == "2026-06-07" and doc["satellite"]["weekly"][-1]["week_ending"] == "2026-10-11"
        assert doc["satellite"]["weekly"][-1]["pct_fields"] > doc["satellite"]["weekly"][0]["pct_fields"] == 0.0
        assert doc["usda"]["series"]["CORN"]["year"] == 2025 and doc["usda"]["series"]["SOYBEANS"]["year"] == 2025
        # 2025 weeks 39..41 (Sep 28, Oct 5, Oct 12) fall inside the 2026 window as Sep 27, Oct 4, Oct 11
        assert [r["week_ending"] for r in doc["aligned"]["CORN"]] == ["2026-09-27", "2026-10-04", "2026-10-11"]
        for c in ("CORN", "SOYBEANS"):
            m = doc["metrics"][c]
            assert m["weeks_compared"] == 3 and m["mean_abs_diff_pp_fields"] is not None
            assert m["satellite_50pct_date"] is not None
        text = vu.report(doc)
        assert "CORN" in text and "SOYBEANS" in text and "50%" in text
        assert "validation_usda.json" in capsys.readouterr().out

    def test_rerun_without_a_client_reads_the_cache(self, mock_dir):
        http = FakeHttp((200, payload(record("2026-10-04", "20"), record("2026-10-11", "50"))))
        first = vu.run(mock_dir, client=vu.NassClient("k", http=http))
        second = vu.run(mock_dir, client=None)
        assert second["metrics"] == first["metrics"] and len(http.calls) == 2

    def test_main_runs_offline_with_the_sample_flag(self, mock_dir, capsys):
        vu.main(["--data", str(mock_dir), "--sample"])
        out = capsys.readouterr().out
        assert "CORN" in out and "SOYBEANS" in out
        cached = json.loads((mock_dir / "usda_crop_progress.json").read_text())
        assert cached["source"] == "sample"
