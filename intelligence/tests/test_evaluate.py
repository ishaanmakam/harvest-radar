"""Tests for intelligence/evaluate.py (harvest model vs ground truth on mock data)."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import evaluate as ev

ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 10, 17)


def truth_field(**kw):
    base = {"crop": "rice", "peak_date": "2026-08-30", "peak_ndvi": 0.78, "harvest_date": "2026-10-05",
            "burned": False, "burn_date": None, "sown": False}
    base.update(kw)
    return base


def status_field(fid, **kw):
    base = {"field_id": fid, "zone_id": "Z01", "stage": "harvested", "peak_date": "2026-08-30", "peak_ndvi": 0.78,
            "harvest_date": "2026-10-05", "predicted_harvest_start": None, "predicted_harvest_end": None,
            "sowing_deadline": "2026-10-20", "burn_date": None, "burn_evidence": "none", "confidence": 0.9, "notes": ""}
    base.update(kw)
    return base


class TestTrueStage:
    def test_non_rice_is_unknown(self):
        assert ev.true_stage(truth_field(crop="other"), AS_OF) == "unknown"

    def test_burned(self):
        assert ev.true_stage(truth_field(burned=True, burn_date="2026-10-08"), AS_OF) == "burned"

    def test_sown(self):
        assert ev.true_stage(truth_field(harvest_date="2026-09-25", sown=True), AS_OF) == "sown"

    def test_harvested_before_as_of(self):
        assert ev.true_stage(truth_field(harvest_date="2026-10-16"), AS_OF) == "harvested"

    def test_harvest_within_3_days_is_ready(self):
        assert ev.true_stage(truth_field(harvest_date="2026-10-17"), AS_OF) == "ready"
        assert ev.true_stage(truth_field(harvest_date="2026-10-20"), AS_OF) == "ready"

    def test_harvest_within_14_days_is_maturing(self):
        assert ev.true_stage(truth_field(harvest_date="2026-10-21"), AS_OF) == "maturing"
        assert ev.true_stage(truth_field(harvest_date="2026-10-31"), AS_OF) == "maturing"

    def test_harvest_beyond_14_days_is_growing(self):
        assert ev.true_stage(truth_field(harvest_date="2026-11-01"), AS_OF) == "growing"


class TestHarvestDateError:
    def test_mae_bias_and_within_tolerance(self):
        truth = {"F0001": truth_field(harvest_date="2026-10-05"), "F0002": truth_field(harvest_date="2026-10-05")}
        status = [status_field("F0001", harvest_date="2026-10-07"),                                   # +2 days
                  status_field("F0002", stage="burned", harvest_date="2026-10-01",                    # -4 days
                               burn_date="2026-10-03", burn_evidence="scar")]
        m = ev.evaluate(status, truth, AS_OF)["harvest_date"]
        assert m["n"] == 2
        assert m["mae_days"] == pytest.approx(3.0)
        assert m["bias_days"] == pytest.approx(-1.0)
        assert m["within_3d_pct"] == pytest.approx(50.0)

    def test_non_rice_fields_are_excluded(self):
        truth = {"F0001": truth_field(crop="other")}
        status = [status_field("F0001")]
        assert ev.evaluate(status, truth, AS_OF)["harvest_date"]["n"] == 0


class TestPredictedHarvest:
    def test_window_coverage_and_midpoint_error(self):
        truth = {"F0001": truth_field(harvest_date="2026-10-22"),
                 "F0002": truth_field(harvest_date="2026-10-30")}
        status = [status_field("F0001", stage="ready", harvest_date=None,
                               predicted_harvest_start="2026-10-18", predicted_harvest_end="2026-10-24"),
                  status_field("F0002", stage="maturing", harvest_date=None,
                               predicted_harvest_start="2026-10-20", predicted_harvest_end="2026-10-26")]
        m = ev.evaluate(status, truth, AS_OF)["predicted_harvest"]
        assert m["n"] == 2
        assert m["in_window_pct"] == pytest.approx(50.0)
        # midpoints: 10-21 vs 10-22 -> -1 ; 10-23 vs 10-30 -> -7
        assert m["mae_days"] == pytest.approx(4.0)

    def test_counts_predictions_for_fields_already_harvested(self):
        truth = {"F0001": truth_field(harvest_date="2026-10-10")}
        status = [status_field("F0001", stage="ready", harvest_date=None,
                               predicted_harvest_start="2026-10-17", predicted_harvest_end="2026-10-20")]
        m = ev.evaluate(status, truth, AS_OF)["predicted_harvest"]
        assert m["n_truth_already_harvested"] == 1


class TestStageAccuracy:
    def test_exact_and_coarse_accuracy_with_confusion(self):
        truth = {"F0001": truth_field(harvest_date="2026-10-05"),              # harvested
                 "F0002": truth_field(harvest_date="2026-10-19"),              # ready
                 "F0003": truth_field(harvest_date="2026-10-25"),              # maturing
                 "F0004": truth_field(crop="other")}                           # unknown
        status = [status_field("F0001"),                                                      # right
                  status_field("F0002", stage="maturing", harvest_date=None),                 # wrong exact, right coarse
                  status_field("F0003", stage="harvested"),                                   # wrong both
                  status_field("F0004", stage="unknown", harvest_date=None, peak_ndvi=0.4)]   # right
        m = ev.evaluate(status, truth, AS_OF)["stage"]
        assert m["n"] == 4
        assert m["accuracy_exact_pct"] == pytest.approx(50.0)
        assert m["accuracy_coarse_pct"] == pytest.approx(75.0)
        assert m["confusion"]["ready"]["maturing"] == 1
        assert m["confusion"]["maturing"]["harvested"] == 1


class TestBurnDetection:
    def test_precision_recall_and_date_error(self):
        truth = {"F0001": truth_field(burned=True, burn_date="2026-10-08"),
                 "F0002": truth_field(burned=True, burn_date="2026-10-09"),
                 "F0003": truth_field()}
        status = [status_field("F0001", stage="burned", burn_date="2026-10-10", burn_evidence="scar"),     # TP, +2 d
                  status_field("F0002"),                                                                    # FN
                  status_field("F0003", stage="burned", burn_date="2026-10-11", burn_evidence="hotspot")]  # FP
        m = ev.evaluate(status, truth, AS_OF)["burn"]
        assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
        assert m["precision_pct"] == pytest.approx(50.0)
        assert m["recall_pct"] == pytest.approx(50.0)
        assert m["date_mae_days"] == pytest.approx(2.0)


class TestMockTruth:
    def test_make_mock_data_writes_truth_json(self):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_mock_data.py")], check=True, cwd=ROOT)
        truth = json.loads((ROOT / "data" / "mock" / "truth.json").read_text())
        assert truth["source"] == "mock"
        f = truth["fields"]["F0001"]
        assert set(f) >= {"crop", "peak_date", "harvest_date", "burned", "burn_date", "sown"}
        fields = json.loads((ROOT / "data" / "mock" / "fields.geojson").read_text())
        assert set(truth["fields"]) == {x["properties"]["field_id"] for x in fields["features"]}

    def test_evaluate_runs_on_mock_and_writes_evaluation_json(self):
        out = ev.run(ROOT / "data" / "mock")
        assert (ROOT / "data" / "mock" / "evaluation.json").exists()
        assert out["harvest_date"]["mae_days"] < 5
