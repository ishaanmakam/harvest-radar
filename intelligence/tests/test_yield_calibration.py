"""The relative NDVI yield index becomes a calibrated estimate when yield_model.json (the county model) exists: each
field's bushels per acre is the county estimate times its index, with the county's 90% band scaled the same way; soil
and terrain from field_env.json ride along; the Outlook words say what the number is and where it comes from."""
import json
from pathlib import Path

import analytics
from test_analytics import mock_copy

ROOT = Path(__file__).resolve().parents[2]

YIELD_MODEL = {
    "as_of": "2026-10-18", "fetched_at": "2026-10-18T08:00Z", "sources": ["CropNet USDA Crop Dataset: NASS county yields 2016-2022"],
    "state": "IA", "target_fips": "19169",
    "crops": {
        "corn": {"county": "Story County", "county_fips": "19169", "years": [2016, 2017], "n_train": 14, "features": ["gdd"],
                 "coefficients": {"gdd": 1.0}, "intercept": 190.0, "rmse_bu_per_acre": 9.0, "estimate_bu_per_acre": 204.0,
                 "interval_bu_per_acre": [189.2, 218.8], "hindcast": [{"year": 2022, "actual": 205.5, "predicted": 201.0}],
                 "target": {"year": 2026}, "method": "test"},
        "soybean": {"county": "Story County", "county_fips": "19169", "years": [2016, 2017], "n_train": 14, "features": ["gdd"],
                    "coefficients": {"gdd": 1.0}, "intercept": 55.0, "rmse_bu_per_acre": 3.0, "estimate_bu_per_acre": 61.0,
                    "interval_bu_per_acre": [56.1, 65.9], "hindcast": [], "target": {"year": 2026}, "method": "test"},
    },
}


def seed(tmp_path, with_env=True):
    d = mock_copy(tmp_path)
    (d / "yield_model.json").write_text(json.dumps(YIELD_MODEL), encoding="utf-8")
    if with_env:
        fields = json.loads((d / "fields.geojson").read_text())["features"]
        env = {"fetched_at": "2026-10-18T07:00Z", "sources": ["test"], "fields": {}, "landscape": {}, "zones": {}}
        for i, f in enumerate(fields):
            p = f["properties"]
            env["fields"][p["field_id"]] = {"zone_id": p["zone_id"], "lon": 0.0, "lat": 0.0, "soc_g_kg": 16.0 + (i % 5), "clay_pct": 29.0,
                                            "sand_pct": 31.0, "ph": 6.5, "texture": "clay loam", "elevation_m": 300.0, "slope_pct": 1.2,
                                            "aspect": "west", "note": "Clay loam; typical for the zone" if i % 5 else "Clay loam; organic carbon below the zone (16 vs 19 g/kg)"}
        (d / "field_env.json").write_text(json.dumps(env), encoding="utf-8")
    return d


class TestCalibration:
    def test_fields_are_scaled_to_the_county_estimate(self, tmp_path):
        doc = analytics.run(seed(tmp_path), write=False)
        assert doc["failed"] == {}
        y = doc["landscape"]["yield"]
        assert y["calibration"]["corn"]["county_estimate_bu_per_acre"] == 204.0
        assert y["calibration"]["corn"]["interval_bu_per_acre"] == [189.2, 218.8] and y["calibration"]["corn"]["rmse_bu_per_acre"] == 9.0
        scored = [(fid, f["yield"]) for fid, f in doc["fields"].items() if (f.get("yield") or {}).get("index") is not None]
        assert scored
        for fid, f in scored:
            county = YIELD_MODEL["crops"][f["crop"]]
            assert f["bu_per_acre_calibrated"] == round(county["estimate_bu_per_acre"] * f["index"], 1)
            lo, hi = f["interval_bu_per_acre"]
            assert lo == round(county["interval_bu_per_acre"][0] * f["index"], 1) and hi == round(county["interval_bu_per_acre"][1] * f["index"], 1)
            assert f["calibration"] == "county model"
            assert f["soil"]["texture"] == "clay loam" and f["terrain"]["slope_pct"] == 1.2
        corn = [f for _, f in scored if f["crop"] == "corn"]
        mean = sum(f["bu_per_acre_calibrated"] for f in corn) / len(corn)
        assert abs(mean - 204.0) < 204.0 * 0.08                                   # the landscape mean sits near the county number

    def test_without_the_county_model_the_index_stays_reference_only(self, tmp_path):
        d = mock_copy(tmp_path)
        doc = analytics.run(d, write=False)
        f = next(f["yield"] for f in doc["fields"].values() if (f.get("yield") or {}).get("index") is not None)
        assert f["calibration"] == "reference only" and "bu_per_acre_calibrated" not in f
        assert doc["landscape"]["yield"].get("calibration") in (None, {})

    def test_outlook_words_say_estimate_band_and_source(self, tmp_path):
        doc = analytics.run(seed(tmp_path), write=False)
        fid = next(fid for fid, f in doc["fields"].items() if (f.get("yield") or {}).get("index") is not None and f["yield"]["crop"] == "corn")
        lines = analytics.farm_outlook(doc, fid)
        text = " ".join(lines)
        f = doc["fields"][fid]["yield"]
        assert f"about {f['bu_per_acre_calibrated']:.0f} bushels an acre" in text and "county model" in text
        assert f"{f['interval_bu_per_acre'][0]:.0f} to {f['interval_bu_per_acre'][1]:.0f}" in text and "estimate" in text.lower()
        assert "relative index" not in text
        assert any(line.startswith("Soil and terrain:") and "Clay loam" in line for line in lines)

    def test_a_field_without_soil_data_has_no_soil_line(self, tmp_path):
        doc = analytics.run(seed(tmp_path, with_env=False), write=False)
        fid = next(fid for fid, f in doc["fields"].items() if (f.get("yield") or {}).get("index") is not None)
        assert not any(line.startswith("Soil and terrain:") for line in analytics.farm_outlook(doc, fid))
        assert "soil" not in doc["fields"][fid]["yield"] or doc["fields"][fid]["yield"]["soil"] is None
