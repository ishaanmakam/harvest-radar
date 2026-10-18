"""yield_train.py: a county yield model trained on measured yields (CropNet's USDA county tables 2016-2022, and NASS
QuickStats when a key is present) against NASA POWER growing-season weather per county, with leave-one-year-out
validation and an honest interval. The Story County 2026 estimate is the anchor the field index is calibrated to."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

import yield_train as yt

ROOT = Path(__file__).resolve().parents[2]

CROPNET_CSV = """commodity_desc,reference_period_desc,year,state_ansi,state_name,county_ansi,county_name,asd_code,asd_desc,domain_desc,source_desc,agg_level_desc,"PRODUCTION, MEASURED IN BU","YIELD, MEASURED IN BU / ACRE"
CORN,YEAR,2022,01,ALABAMA,003,BALDWIN,50,COASTAL PLAINS & GULF COAST,TOTAL,SURVEY,COUNTY,1020000.0,139.0
CORN,YEAR,2022,19,IOWA,169,STORY,50,CENTRAL,TOTAL,SURVEY,COUNTY,32000000.0,205.5
CORN,YEAR,2022,19,IOWA,015,BOONE,50,CENTRAL,TOTAL,SURVEY,COUNTY,30000000.0,198.0
CORN,YEAR,2022,19,IOWA,999,OTHER COUNTIES,50,CENTRAL,TOTAL,SURVEY,COUNTY,1000.0,180.0
"""
GAZ = "USPS\tGEOID\tANSICODE\tNAME\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n" \
      "IA\t19169\t00465273\tStory County\t1482882549\t2862354\t572.544\t1.105\t42.037538\t-93.466093     \n" \
      "IA\t19015\t00465195\tBoone County\t1482882549\t2862354\t572.544\t1.105\t42.036000\t-93.931000     \n" \
      "AL\t01003\t00161526\tBaldwin County\t4117656514\t1133109800\t1589.836\t437.496\t30.727901\t-87.722303     \n"


def power_doc(year, tmax_add=0.0, rain_mul=1.0, missing_tail=0):
    """A NASA POWER daily document for one year: warm summer, cool shoulders; -999 for the last `missing_tail` days."""
    d0 = date(year, 1, 1)
    tmax, tmin, pr = {}, {}, {}
    n = (date(year, 12, 31) - d0).days + 1
    for i in range(n):
        d = d0 + timedelta(days=i)
        key = d.strftime("%Y%m%d")
        season = math.sin(math.pi * (i - 100) / 200) if 100 <= i <= 300 else -0.5
        hi = 10 + 22 * max(season, 0) + tmax_add
        tmax[key] = round(hi, 1)
        tmin[key] = round(hi - 11, 1)
        pr[key] = round((3.0 if i % 4 == 0 else 0.0) * rain_mul, 1)
    for key in sorted(tmax)[n - missing_tail:]:
        tmax[key] = tmin[key] = pr[key] = -999.0
    return {"header": {"fill_value": -999.0}, "properties": {"parameter": {"T2M_MAX": tmax, "T2M_MIN": tmin, "PRECTOTCORR": pr}}}


class TestParsers:
    def test_cropnet_rows_for_the_state_only_with_fips(self):
        rows = yt.parse_cropnet_csv(CROPNET_CSV, state_ansi="19")
        assert [r["fips"] for r in rows] == ["19169", "19015"] and rows[0]["county"] == "STORY" and rows[0]["year"] == 2022
        assert rows[0]["yield_bu_ac"] == 205.5 and rows[0]["production_bu"] == 32000000.0

    def test_gazetteer_centroids_for_the_state(self):
        g = yt.parse_gazetteer(GAZ, state="IA")
        assert set(g) == {"19169", "19015"} and g["19169"]["name"] == "Story County"
        assert g["19169"]["lat"] == pytest.approx(42.037538) and g["19169"]["lon"] == pytest.approx(-93.466093)

    def test_power_fill_values_read_as_missing(self):
        daily = yt.parse_power(power_doc(2022, missing_tail=3))
        assert len(daily["dates"]) == 365 and daily["dates"][0] == "2022-01-01"
        assert daily["tmax"][-1] is None and daily["precip"][-1] is None and daily["tmax"][-4] is not None

    def test_nass_rows_from_quickstats(self):
        doc = {"data": [{"year": "2021", "state_ansi": "19", "county_ansi": "169", "county_name": "STORY", "Value": "211.3", "unit_desc": "BU / ACRE"},
                        {"year": "2021", "state_ansi": "19", "county_ansi": "", "county_name": "OTHER COUNTIES", "Value": "190", "unit_desc": "BU / ACRE"}]}
        rows = yt.parse_nass(doc)
        assert rows == [{"year": 2021, "fips": "19169", "county": "STORY", "yield_bu_ac": 211.3, "production_bu": None}]


class TestFeatures:
    def test_season_features_by_hand(self):
        daily = yt.parse_power(power_doc(2022))
        f = yt.season_features(daily, 2022)
        # every day's GDD is the mean of the capped extremes minus 10, summed May 1 to Sep 30
        dates = [date.fromisoformat(x) for x in daily["dates"]]
        gdd = sum(max(0.0, (min(max(hi, 10), 30) + min(max(lo, 10), 30)) / 2 - 10) for d, hi, lo in zip(dates, daily["tmax"], daily["tmin"])
                  if date(2022, 5, 1) <= d <= date(2022, 9, 30))
        assert f["gdd"] == pytest.approx(gdd, abs=0.1)
        assert f["heat32"] == sum(1 for d, hi in zip(dates, daily["tmax"]) if d.month in (7, 8) and hi >= 32)
        assert f["p_jul"] == pytest.approx(sum(p for d, p in zip(dates, daily["precip"]) if d.month == 7), abs=0.1)
        assert f["dry_spell"] >= 3 and f["days_observed"] == 153 and f["frost_doy"] is not None

    def test_missing_days_are_filled_from_climatology_and_counted(self):
        years = {y: yt.parse_power(power_doc(y)) for y in (2019, 2020, 2021)}
        clim = yt.climatology(years.values())
        partial = yt.parse_power(power_doc(2022, missing_tail=120))              # observed only to about Sep 2
        filled, n_filled = yt.fill_season(partial, 2022, clim)
        assert n_filled > 0 and all(v is not None for v in filled["tmax"])
        f = yt.season_features(filled, 2022)
        assert f["days_observed"] == 153 and f["days_filled"] == n_filled


class TestModel:
    def test_ridge_recovers_a_linear_rule_and_loyo_reports_per_year(self):
        rows = []
        for year in range(2016, 2023):
            for k, fips in enumerate(("19169", "19015", "19099", "19127")):
                gdd = 1400 + 40 * (year - 2016) + 25 * k
                heat = 3 + (year % 3) + k
                rows.append({"fips": fips, "year": year, "y": 120 + 0.05 * gdd - 1.5 * heat, "gdd": gdd, "heat32": heat, "p_jul": 80.0})
        feats = ["gdd", "heat32", "p_jul"]
        model = yt.fit_ridge(rows, feats, lam=0.01)
        assert yt.predict(model, rows[0]) == pytest.approx(rows[0]["y"], abs=0.5)
        cv = yt.loyo(rows, feats, lams=(0.01, 1.0, 10.0))
        assert [r["year"] for r in cv["years"]] == list(range(2016, 2023)) and cv["rmse"] < 1.0 and cv["r2"] > 0.95
        assert cv["lambda"] in (0.01, 1.0, 10.0)

    def test_interval_widens_with_the_hindcast_error(self):
        lo, hi = yt.interval(200.0, rmse=8.0)
        assert lo == pytest.approx(200 - 1.64 * 8, abs=0.06) and hi == pytest.approx(200 + 1.64 * 8, abs=0.06)   # rounded to 0.1


class TestRun:
    def fake_http(self, calls):
        def http(method, url, headers, body, timeout):
            calls.append(url)
            if "power.larc.nasa.gov" in url:
                year = int(url.split("start=")[1][:4])
                lat = float(url.split("latitude=")[1].split("&")[0])
                return 200, json.dumps(power_doc(year, tmax_add=(lat - 42.0) * 4, rain_mul=1.0 + (year % 2) * 0.3)).encode()
            return 404, b"not here"
        return http

    def seed(self, tmp_path):
        cache = tmp_path / "cache"
        (cache / "cropnet").mkdir(parents=True)
        (cache / "census").mkdir()
        (cache / "census" / "counties.txt").write_text(GAZ)
        for crop, name, base in (("Corn", "Corn", 150.0), ("Soybean", "Soybean", 45.0)):
            for y in range(2016, 2023):
                lines = [CROPNET_CSV.splitlines()[0]]
                for fips, cname, k in (("169", "STORY", 0), ("015", "BOONE", 1)):
                    lines.append(f"{crop.upper()},YEAR,{y},19,IOWA,{fips},{cname},50,CENTRAL,TOTAL,SURVEY,COUNTY,1000.0,{base + 4 * (y - 2016) + 3 * k + (y % 2) * 2:.1f}")
                (cache / "cropnet" / f"USDA_{name}_County_{y}.csv").write_text("\n".join(lines) + "\n")
        data = tmp_path / "data"
        data.mkdir()
        return data, cache

    def test_run_trains_both_crops_and_writes_the_model_file(self, tmp_path):
        data, cache = self.seed(tmp_path)
        calls = []
        doc = yt.run(data, cache_dir=cache, http=self.fake_http(calls), years=range(2016, 2023), target_year=2026,
                     target_fips="19169", today=date(2026, 10, 18), fetched_at="2026-10-18T08:00Z")
        assert set(doc["crops"]) == {"corn", "soybean"}
        c = doc["crops"]["corn"]
        assert c["county"] == "Story County" and c["n_train"] == 14 and c["years"] == list(range(2016, 2023))
        assert c["estimate_bu_per_acre"] > 0 and c["interval_bu_per_acre"][0] < c["estimate_bu_per_acre"] < c["interval_bu_per_acre"][1]
        assert [h["year"] for h in c["hindcast"]] == list(range(2016, 2023)) and all("actual" in h and "predicted" in h for h in c["hindcast"])
        assert c["rmse_bu_per_acre"] >= 0 and set(c["coefficients"]) == set(c["features"])
        assert c["target"]["days_observed"] == 153 and c["target"]["days_filled"] >= 0
        saved = json.loads((data / "yield_model.json").read_text(encoding="utf-8"))
        assert saved["crops"]["corn"]["estimate_bu_per_acre"] == c["estimate_bu_per_acre"] and saved["fetched_at"] == "2026-10-18T08:00Z"
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "contracts" / "yield_model.schema.json").read_text())
        assert not list(Draft202012Validator(schema).iter_errors(saved))
        # weather is cached per county and year range: a second run makes no new requests
        n = len(calls)
        yt.run(data, cache_dir=cache, http=self.fake_http(calls), years=range(2016, 2023), target_year=2026, target_fips="19169", today=date(2026, 10, 18))
        assert len(calls) == n

    def test_without_a_nass_key_the_cropnet_years_are_used_and_said(self, tmp_path):
        data, cache = self.seed(tmp_path)
        doc = yt.run(data, cache_dir=cache, http=self.fake_http([]), years=range(2016, 2023), target_year=2026, target_fips="19169",
                     today=date(2026, 10, 18), nass_key=None)
        assert any("CropNet" in s for s in doc["sources"]) and not any("QuickStats" in s for s in doc["sources"])
