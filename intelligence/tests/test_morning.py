"""morning.py: the one command for the demo morning. Dry run by default, --real sends, --preview writes the exact
words per roster row to messages.json, and the summary says what happened."""
import json
from datetime import date
from pathlib import Path

import pytest

import morning
import queue_morning as q
from test_queue import ROSTER_CSV, plan

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 17)


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "act_now")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "visit", "why": "Hotspot on 2026-10-16 inside the field"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path


class TestPreview:
    def test_messages_json_has_the_exact_words_per_roster_row(self, data):
        out = morning.run(data, roster_path=data / "roster.csv", today=TODAY, dry_run=True, preview=True)
        msgs = json.loads((data / "messages.json").read_text(encoding="utf-8"))
        assert msgs["date"] == "2026-10-17"
        rows = {m["roster_id"]: m for m in msgs["messages"]}
        assert set(rows) == {"R001", "R002", "R003"}                        # rows that can be sent (phone + plan)
        assert rows["R001"]["role"] == "officer" and "F0002" in rows["R001"]["text"]
        assert rows["R003"]["language"] == "hi" and rows["R003"]["text"] == q.render_plan(plan("F0002", "act_now"), "hi")
        assert rows["R002"]["farm_id"] == "F0001"
        assert out["summary"]["messages"] == 3

    def test_dry_run_is_the_default_and_writes_the_checkpoint(self, data):
        out = morning.run(data, roster_path=data / "roster.csv", today=TODAY)
        doc = json.loads((data / "deliveries.json").read_text())
        assert all(r["status"] in ("pending", "failed") for r in doc["deliveries"])
        assert out["summary"]["deliveries"]["pending"] == 6 and out["summary"]["deliveries"]["failed"] == 4
        assert out["summary"]["flags"] == {"act_now": 1, "watch": 0, "fine": 1}
        assert out["summary"]["route_stops"] == 1


class TestRealSend:
    def test_real_mode_uses_the_given_providers(self, data):
        class Ok:
            def __init__(self):
                self.calls = []

            def send(self, phone, text, language):
                self.calls.append((phone, language))
                return "ok"
        voice, sms = Ok(), Ok()
        out = morning.run(data, roster_path=data / "roster.csv", today=TODAY, dry_run=False,
                          providers={"grok_voice": voice, "photon_sms": sms})
        assert len(voice.calls) == 3 and len(sms.calls) == 3
        assert out["summary"]["deliveries"]["sent"] == 6


class TestMockEndToEnd:
    def test_runs_on_the_mock_data_with_the_example_roster(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("plans.json", "route.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        out = morning.run(tmp_path, roster_path=ROOT / "intelligence" / "roster.example.csv", today=TODAY, preview=True)
        assert out["summary"]["messages"] == 3 and (tmp_path / "messages.json").exists()
        assert out["summary"]["flags"]["act_now"] > 0
