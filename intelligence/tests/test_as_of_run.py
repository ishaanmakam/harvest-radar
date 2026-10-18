"""The season date comes from the pipeline's run.json when it exists (live or replay), else from config/region.json.
A replay as of 2026-09-24 must judge its as-issued forecast as fresh and clamp harvest windows to that morning; the
mock, which has no run.json, keeps using the region's as_of."""
import json
from datetime import date
from pathlib import Path

import analytics
import common
import decide
import harvest_model
import refresh

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "mock"


def copy_mock(tmp_path, as_of=None):
    for name in ("fields.geojson", "zones.geojson", "timeseries.json", "fires.json", "insights.json", "weather.json", "status.json"):
        (tmp_path / name).write_bytes((MOCK / name).read_bytes())
    if as_of:
        w = json.loads((tmp_path / "weather.json").read_text())
        n = len(w["forecast"]["dates"])
        start = date.fromisoformat(as_of)
        w["forecast"]["dates"] = [(start.fromordinal(start.toordinal() + i)).isoformat() for i in range(n)]
        (tmp_path / "weather.json").write_text(json.dumps(w))
        (tmp_path / "run.json").write_text(json.dumps({"as_of": as_of, "mode": "replay", "latest_pass": "2026-10-06"}))
    return tmp_path


class TestSeasonAsOf:
    def test_run_json_wins_over_the_region(self, tmp_path):
        assert common.season_as_of(copy_mock(tmp_path, "2026-10-10")) == date(2026, 10, 10)

    def test_without_run_json_the_region_date_is_used(self, tmp_path):
        region = common.load_region()
        assert common.season_as_of(copy_mock(tmp_path)) == date.fromisoformat(region["as_of"])

    def test_a_malformed_run_json_falls_back(self, tmp_path):
        d = copy_mock(tmp_path)
        (d / "run.json").write_text("not json")
        assert common.season_as_of(d) == date.fromisoformat(common.load_region()["as_of"])
        (d / "run.json").write_text(json.dumps({"mode": "live"}))
        assert common.season_as_of(d) == date.fromisoformat(common.load_region()["as_of"])


class TestReplayFlowsThrough:
    def test_model_plans_and_analytics_follow_the_replay_date(self, tmp_path):
        d = copy_mock(tmp_path, "2026-10-10")
        harvest_model.run(d)
        status = json.loads((d / "status.json").read_text())
        assert status["as_of"] == "2026-10-10"
        plans, route = decide.run(d)
        assert route["as_of"] == "2026-10-10"
        standing = [p for p in plans["plans"] if any(c["kind"] == "harvest_ready" and c["status"] == "watch" for c in p["checks"])]
        assert standing and all("harvest_outlook" in p for p in standing)           # the as-issued forecast counts as fresh
        doc = analytics.run(d, write=False)
        assert doc["as_of"] == "2026-10-10" and doc["failed"] == {}

    def test_refresh_carries_run_json_into_staging(self):
        assert "run.json" in refresh.INPUTS
