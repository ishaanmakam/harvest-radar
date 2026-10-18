"""refresh.py: a new clear pass reruns the season model and the decision in a staging folder and replaces
status.json, plans.json and route.json only if everything validates; otherwise the last good files stay."""
import json
from pathlib import Path

import pytest

import refresh

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ("fields.geojson", "zones.geojson", "timeseries.json", "weather.json", "fires.json", "insights.json")


@pytest.fixture
def season(tmp_path):
    data = ROOT / "data" / "mock"
    for name in INPUTS:
        (tmp_path / name).write_bytes((data / name).read_bytes())
    return tmp_path


class TestRefresh:
    def test_first_run_produces_validated_outputs(self, season):
        result = refresh.run(season)
        assert result["replaced"] is True
        for name in ("status.json", "plans.json", "route.json"):
            assert (season / name).exists()
        assert not (season / ".staging").exists()                        # staging is cleaned up
        assert result["as_of"] == "2026-10-16" and result["plans"] > 100

    def test_broken_input_keeps_the_last_good_files(self, season):
        refresh.run(season)
        before = {n: (season / n).read_bytes() for n in ("status.json", "plans.json", "route.json")}
        (season / "timeseries.json").write_text("{not json")
        result = refresh.run(season)
        assert result["replaced"] is False and result["error"]
        for n, b in before.items():
            assert (season / n).read_bytes() == b

    def test_invalid_output_keeps_the_last_good_files(self, season, monkeypatch):
        refresh.run(season)
        before = (season / "plans.json").read_bytes()
        import decide
        monkeypatch.setattr(decide, "build_route", lambda plans, today: {"stops": "not-a-list"})
        result = refresh.run(season)
        assert result["replaced"] is False
        assert (season / "plans.json").read_bytes() == before
