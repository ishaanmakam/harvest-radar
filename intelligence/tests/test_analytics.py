"""analytics.py: runs every analytics module over a data dir in order, isolates failures, writes analytics.json
(additive) and renders short per-farm outlook lines for the tools and the morning words."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import analytics

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


def mock_copy(tmp_path):
    for p in MOCK.iterdir():
        if p.is_file():
            (tmp_path / p.name).write_bytes(p.read_bytes())
    return tmp_path


class TestRunOnMock:
    def test_every_module_runs_and_the_document_validates(self, tmp_path):
        doc = analytics.run(mock_copy(tmp_path))
        assert doc["failed"] == {}, doc["failed"]
        assert [k for _, k in analytics.MODULES] == doc["modules"]
        schema = json.loads((ROOT / "contracts" / "analytics.schema.json").read_text())
        assert not list(Draft202012Validator(schema).iter_errors(doc))
        fields = json.loads((MOCK / "fields.geojson").read_text())["features"]
        assert set(doc["fields"]) >= {f["properties"]["field_id"] for f in fields}
        written = json.loads((tmp_path / "analytics.json").read_text(encoding="utf-8"))
        assert written["modules"] == doc["modules"]
        assert all(doc["method"][k] for k in doc["modules"])                    # every module explains itself

    def test_per_field_entries_are_keyed_by_module(self, tmp_path):
        doc = analytics.run(mock_copy(tmp_path), write=False)
        fid = next(iter(doc["fields"]))
        assert set(doc["fields"][fid]) <= set(doc["modules"])
        assert "coverage" in doc["fields"][fid] and "events" in doc["fields"][fid]

    def test_outlook_lines_are_short_plain_sentences(self, tmp_path):
        doc = analytics.run(mock_copy(tmp_path), write=False)
        status = {r["field_id"]: r for r in json.loads((MOCK / "status.json").read_text())["fields"]}
        ready = next(fid for fid, r in status.items() if r["stage"] == "ready")
        lines = analytics.farm_outlook(doc, ready)
        assert lines and all(line.endswith(".") and len(line) < 260 for line in lines)
        assert analytics.farm_outlook(doc, "NOPE") == []


class TestRealShapes:
    def test_unknown_crop_labels_and_a_field_without_series_do_not_break_any_module(self, tmp_path):
        """The real fields.geojson (pipeline PR #12) labels 74 of 395 fields crop "unknown" and new outlines may have no
        time series yet: every module must degrade, not fail."""
        d = mock_copy(tmp_path)
        fields = json.loads((d / "fields.geojson").read_text())
        for feat in fields["features"][:30]:
            feat["properties"]["crop"] = "unknown"
        fields["features"].append({"type": "Feature", "geometry": fields["features"][0]["geometry"],
                                   "properties": {"field_id": "F9999", "zone_id": "Z01", "area_ha": 9.1, "crop": "corn", "source": "test"}})
        (d / "fields.geojson").write_text(json.dumps(fields))
        doc = analytics.run(d, write=False)
        assert doc["failed"] == {}, doc["failed"]
        assert "F9999" in doc["fields"]


class TestEnsembleOverlay:
    def test_the_multi_model_ensemble_replaces_the_single_feed_for_the_analytics(self, tmp_path):
        d = mock_copy(tmp_path)
        w = json.loads((d / "weather.json").read_text())
        dates = w["forecast"]["dates"]
        days = [{"date": dt, "n_models": 8, "n_precip": 8, "p_rain_5mm": 0.25 if i == 2 else 0.0, "k_rain_5mm": 2 if i == 2 else 0,
                 "precip_median_mm": 2.0 if i == 2 else 0.0, "precip_max_mm": 16.0 if i == 2 else 0.0} for i, dt in enumerate(dates[:5])]
        days[4].update(n_models=2, n_precip=2, p_rain_5mm=0.5, k_rain_5mm=1, precip_median_mm=9.0)   # only two models reach day 5
        (d / "weather_ensemble.json").write_text(json.dumps({"source": "test", "fetched_at": "2026-10-17T06:00Z", "dates": dates[:5],
                                                             "models": ["a"] * 8, "days": days}))
        data = analytics.load_data(d)
        fc = data["weather"]["forecast"]
        assert data["ensemble"]["models"] == ["a"] * 8
        assert fc["precip_prob"][2] == 25 and fc["precip_mm"][2] == 2.0            # ensemble share and median on an overlaid day
        assert fc["precip_mm_single_model"][2] == w["forecast"]["precip_mm"][2]      # the pipeline's value is kept
        assert fc["precip_mm"][4] == w["forecast"]["precip_mm"][4]                  # two models are not an ensemble: feed value kept
        assert fc["precip_mm"][7] == w["forecast"]["precip_mm"][7]                  # days the ensemble does not cover stay as they were
        assert fc["precip_n_models"][2] == 8 and fc["precip_n_models"][4] == 0 and "ensemble" in fc["source"]
        doc = analytics.run(d, write=False)
        assert doc["failed"] == {} and doc["forecast_source"] == "multi-model ensemble (8 models) over the pipeline forecast"
        assert "at least 3 models" in doc["forecast_note"]

    def test_a_stale_file_leaves_the_pipeline_forecast(self, tmp_path):
        d = mock_copy(tmp_path)
        w = json.loads((d / "weather.json").read_text())
        days = [{"date": dt, "n_precip": 8, "p_rain_5mm": 1.0, "precip_median_mm": 30.0} for dt in w["forecast"]["dates"]]
        (d / "weather_ensemble.json").write_text(json.dumps({"fetched_at": "2026-10-01T06:00Z", "days": days, "models": ["a"] * 8}))
        data = analytics.load_data(d)
        assert data["ensemble"] is None and data["weather"]["forecast"]["precip_mm"] == w["forecast"]["precip_mm"]

    def test_without_the_file_nothing_changes(self, tmp_path):
        d = mock_copy(tmp_path)
        data = analytics.load_data(d)
        assert data["ensemble"] is None and "precip_mm_single_model" not in data["weather"]["forecast"]
        assert analytics.run(d, write=False)["forecast_source"] == "pipeline forecast"


class TestIsolation:
    def test_a_broken_module_is_recorded_and_the_others_still_run(self, tmp_path, monkeypatch):
        import types
        import sys
        good = types.ModuleType("good_mod")
        good.analyze = lambda data, config, prior=None: {"key": "good", "fields": {"F0001": {"x": 1.0}}, "landscape": {"n": 1}, "method": "ok"}
        bad = types.ModuleType("bad_mod")

        def boom(data, config, prior=None):
            raise RuntimeError("no weather")
        bad.analyze = boom
        monkeypatch.setitem(sys.modules, "good_mod", good)
        monkeypatch.setitem(sys.modules, "bad_mod", bad)
        doc = analytics.run(mock_copy(tmp_path), modules=[("bad_mod", "bad"), ("good_mod", "good"), ("missing_mod", "gone")], write=False)
        assert doc["modules"] == ["good"]
        assert "RuntimeError" in doc["failed"]["bad"] and "not available" in doc["failed"]["gone"]
        assert doc["fields"]["F0001"]["good"] == {"x": 1.0}

    def test_numpy_and_nan_are_cleaned(self):
        import numpy as np
        out = analytics.clean({"a": np.float64(1.5), "b": float("nan"), "c": np.array([1, 2]), "d": (1, 2), "e": np.int64(3)})
        assert out == {"a": 1.5, "b": None, "c": [1, 2], "d": [1, 2], "e": 3}
        json.dumps(out)


class TestWords:
    def test_outlook_reads_the_module_keys(self):
        doc = {"fields": {"R001": {
            "yield": {"index": 1.1, "band": "high", "estimate": {"bu_per_acre": 212.0}},
            "economics": {"recommendation": "harvest_now", "reason": "drying costs less than another week of field loss", "savings_usd": 412.0},
            "workability": {"words": "92% chance to finish the 0.5 machine-days before the next rain"},
            "water": {"water_status": "dry", "days_since_rain": 9},
            "anomaly": {"anomaly_type": "sudden_drop", "note": "Greenness fell faster than the zone since the last pass"},
            "coverage": {"staleness": "fresh"},
        }}}
        lines = analytics.farm_outlook(doc, "R001")
        text = " ".join(lines)
        assert "high for the zone" in text and "212 bushels" in text and "relative index" in text
        assert "Harvest now: drying costs less" in text and "412 dollars" in text
        assert "92% chance" in text and "Soil is dry, 9 days since the last real rain." in lines
        assert "faster than the zone" in text and "stale" not in text
