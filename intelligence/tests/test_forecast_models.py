"""forecast_models.py: the latest runs of several numerical weather models (ECMWF IFS, GFS with HRRR, ICON, GEM,
Meteo-France, UK Met Office, JMA, NOAA NBM) from the free Open-Meteo API, folded into one ensemble per day: median and
range of rain, the share of models that put a shower, a blocking rain or a soaking on the day, frost and hard-freeze
agreement, gusts. Written to weather_ensemble.json (additive; weather.json stays the pipeline's)."""
import json
from datetime import date

import pytest

import forecast_models as fm

D = date.fromisoformat
DATES = ["2026-10-17", "2026-10-18", "2026-10-19"]


def doc(models=("ecmwf_ifs025", "gfs_seamless", "icon_seamless")):
    """A response shaped like Open-Meteo's multi-model daily block: every variable is suffixed with the model id."""
    per = {
        "ecmwf_ifs025": {"precipitation_sum": [0.0, 20.0, 0.0], "precipitation_probability_max": [0, 80, 5],
                         "temperature_2m_min": [4.0, -1.0, 2.0], "temperature_2m_max": [18.0, 12.0, 15.0], "wind_gusts_10m_max": [36.0, 54.0, 18.0]},
        "gfs_seamless": {"precipitation_sum": [0.0, 3.0, 0.0], "precipitation_probability_max": [0, 40, 0],
                         "temperature_2m_min": [5.0, 0.5, 3.0], "temperature_2m_max": [19.0, 14.0, 16.0], "wind_gusts_10m_max": [30.0, 40.0, 20.0]},
        "icon_seamless": {"precipitation_sum": [1.0, 14.0, None], "precipitation_probability_max": [None, None, None],
                          "temperature_2m_min": [3.0, -3.0, None], "temperature_2m_max": [17.0, 11.0, None], "wind_gusts_10m_max": [None, None, None]},
    }
    daily = {"time": list(DATES)}
    for m in models:
        for var, vals in per[m].items():
            daily[f"{var}_{m}"] = vals
    return {"latitude": 42.0, "longitude": -93.5, "timezone": "America/Chicago", "daily": daily,
            "daily_units": {"precipitation_sum_ecmwf_ifs025": "mm", "wind_gusts_10m_max_ecmwf_ifs025": "km/h"}}


class TestEnsemble:
    def test_rain_agreement_median_and_range(self):
        ens = fm.ensemble(doc())
        assert ens["dates"] == DATES and ens["models"] == ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
        d1 = ens["days"][1]
        assert d1["n_models"] == 3 and d1["precip_median_mm"] == 14.0 and d1["precip_max_mm"] == 20.0 and d1["precip_min_mm"] == 3.0
        assert d1["p_rain_5mm"] == pytest.approx(2 / 3) and d1["p_rain_12mm"] == pytest.approx(2 / 3)
        assert d1["p_rain_15mm"] == pytest.approx(1 / 3) and d1["p_rain_25mm"] == 0.0
        assert d1["prob_mean_pct"] == 60                                       # ICON reports no probability: mean of 80 and 40
        d0 = ens["days"][0]
        assert d0["p_rain_5mm"] == 0.0 and d0["precip_median_mm"] == 0.0 and d0["agreement"] == "dry"
        assert d1["agreement"] == "split"

    def test_missing_values_shrink_the_model_count_for_that_day(self):
        d2 = fm.ensemble(doc())["days"][2]
        assert d2["n_models"] == 2 and d2["precip_median_mm"] == 0.0 and d2["tmin_min_c"] == 2.0

    def test_frost_agreement_and_gusts(self):
        d1 = fm.ensemble(doc())["days"][1]
        assert d1["tmin_min_c"] == -3.0 and d1["tmin_median_c"] == -1.0
        assert d1["p_frost"] == pytest.approx(2 / 3) and d1["p_hard_freeze"] == pytest.approx(1 / 3)
        assert d1["tmax_median_c"] == 12.0
        assert d1["gust_max_ms"] == pytest.approx(54.0 / 3.6, abs=0.01)         # km/h in, m/s out

    def test_summary_names_the_split_day(self):
        ens = fm.ensemble(doc())
        assert "2026-10-18" in ens["summary"] and "2 of 3" in ens["summary"] and ens["summary"].endswith(".")

    def test_arrays_mirror_the_days_for_quick_readers(self):
        ens = fm.ensemble(doc())
        assert ens["p_rain_15mm"] == [0.0, pytest.approx(1 / 3), 0.0] and ens["precip_median_mm"][1] == 14.0 and len(ens["p_frost"]) == 3

    def test_a_single_model_or_no_models_does_not_crash(self):
        one = fm.ensemble(doc(models=("gfs_seamless",)))
        assert one["days"][1]["n_models"] == 1 and one["days"][1]["p_rain_5mm"] == 0.0
        empty = fm.ensemble({"daily": {"time": DATES}})
        assert empty["models"] == [] and all(d["n_models"] == 0 and d["precip_median_mm"] is None for d in empty["days"])


class TestFetch:
    def test_fetch_asks_for_every_model_and_the_daily_variables(self):
        seen = {}

        def http(method, url, headers, body, timeout):
            seen["url"] = url
            return 200, json.dumps(doc()).encode()
        out = fm.fetch(42.0, -93.5, "America/Chicago", http=http)
        assert out["daily"]["time"] == DATES
        assert "api.open-meteo.com/v1/forecast" in seen["url"] and "models=" in seen["url"]
        for m in fm.MODELS:
            assert m in seen["url"]
        for var in ("precipitation_sum", "precipitation_probability_max", "temperature_2m_min", "temperature_2m_max", "wind_gusts_10m_max"):
            assert var in seen["url"]
        assert "forecast_days=14" in seen["url"] and "timezone=America%2FChicago" in seen["url"]

    def test_http_failure_is_a_clear_error(self):
        with pytest.raises(fm.ForecastError):
            fm.fetch(42.0, -93.5, "America/Chicago", http=lambda *a: (503, b"busy"))
        with pytest.raises(fm.ForecastError):
            fm.fetch(42.0, -93.5, "America/Chicago", http=lambda *a: (200, b"not json"))


class TestFile:
    def test_run_writes_weather_ensemble_json_and_load_reads_it_by_date(self, tmp_path):
        out = fm.run(tmp_path, lat=42.0, lon=-93.5, timezone="America/Chicago", http=lambda *a: (200, json.dumps(doc()).encode()),
                     fetched_at="2026-10-17T06:00Z")
        saved = json.loads((tmp_path / "weather_ensemble.json").read_text(encoding="utf-8"))
        assert saved["source"].startswith("open-meteo") and saved["fetched_at"] == "2026-10-17T06:00Z"
        assert saved["dates"] == DATES and saved["models"] == out["models"] and saved["model_names"]["ecmwf_ifs025"]
        by_date = fm.load_ensemble(tmp_path)
        assert by_date[D("2026-10-18")]["p_rain_15mm"] == pytest.approx(1 / 3)
        assert fm.load_ensemble(tmp_path / "nowhere") == {}

    def test_centre_of_the_region_box(self):
        assert fm.box_centre({"bbox": [-93.52, 41.98, -93.42, 42.07]}) == (pytest.approx(42.025), pytest.approx(-93.47))
