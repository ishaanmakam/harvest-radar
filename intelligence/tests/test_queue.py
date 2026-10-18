"""Queue tests (spec Testing section): a dry run writes delivery rows and places no calls or texts; a real run records
voice and SMS separately; restarting does not send the same channel twice on the same morning; a missing number is
failed and skipped; every check (clear ones included) is in the rendered words."""
import json
from datetime import date
from pathlib import Path

import pytest

import queue_morning as q
import tools

TODAY = date(2026, 10, 17)
ROSTER_CSV = """roster_id,farm_id,name,phone,language,role
R001,,Officer Kaur,+919876500001,pa,officer
R002,F0001,Gurpreet Singh,+919876500002,pa,farmer
R003,F0002,Harjit Singh,+919876500003,hi,farmer
R004,F0002,No Phone Singh,,pa,farmer
R005,F9999,Ghost Farm,+919876500005,pa,farmer
"""


def plan(farm_id, flag="fine"):
    checks = [{"kind": k, "status": "clear", "detail": f"{k} is clear", "where": "", "window": {}} for k in tools.KINDS]
    today = "Nothing needs action today."
    if flag == "act_now":
        checks[4] = {"kind": "fire", "status": "act_now", "detail": "Hotspot on 2026-10-16 inside the field", "where": "",
                     "window": {"start": "2026-10-16", "end": "2026-10-16"}}
        today = "Check the field for fire now and call the officer if it is burning."
    return {"farm_id": farm_id, "as_of": "2026-10-16", "flag": flag, "source": "vegetation_index", "today": today,
            "checks": checks, "risks": [c["detail"] for c in checks if c["status"] != "clear"]}


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "act_now")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "visit", "why": "Hotspot on 2026-10-16 inside the field"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path


class Spy:
    """A provider that must never be called in a dry run."""

    def __init__(self):
        self.calls = []

    def send(self, phone, text, language):
        self.calls.append((phone, language))
        raise AssertionError("provider called during a dry run")


class Flaky:
    """Fails `fail_first` times, then succeeds."""

    def __init__(self, fail_first):
        self.fail_first, self.calls = fail_first, []

    def send(self, phone, text, language):
        self.calls.append(phone)
        if len(self.calls) <= self.fail_first:
            raise RuntimeError(f"provider error #{len(self.calls)}")
        return f"ok after {len(self.calls)} attempts"


def rows(doc, roster_id=None):
    return [r for r in doc["deliveries"] if roster_id is None or r["roster_id"] == roster_id]


class TestDryRun:
    def test_writes_pending_rows_per_channel_and_places_nothing(self, data):
        spy = Spy()
        doc = q.run(data, roster_path=data / "roster.csv", dry_run=True, today=TODAY,
                    providers={"grok_voice": spy, "photon_sms": spy})
        assert spy.calls == []
        assert (data / "deliveries.json").exists()
        assert len(doc["deliveries"]) == 10                                   # 5 roster rows x 2 channels
        for rid in ("R001", "R002", "R003"):
            assert sorted(r["channel"] for r in rows(doc, rid)) == ["grok_voice", "photon_sms"]
            assert all(r["status"] == "pending" and r["date"] == "2026-10-17" for r in rows(doc, rid))
        assert all(r["status"] == "failed" and "phone" in r["detail"].lower() for r in rows(doc, "R004"))
        assert all(r["status"] == "failed" and "plan" in r["detail"].lower() for r in rows(doc, "R005"))
        assert all(set(r) >= {"date", "roster_id", "channel", "status", "detail"} for r in doc["deliveries"])

    def test_rerun_does_not_duplicate_rows(self, data):
        kw = dict(roster_path=data / "roster.csv", dry_run=True, today=TODAY)
        q.run(data, **kw)
        doc = q.run(data, **kw)
        assert len(doc["deliveries"]) == 10


class TestRealRun:
    def test_voice_and_sms_are_separate_rows_with_two_retries(self, data):
        voice, sms = Flaky(fail_first=2), Flaky(fail_first=3)
        doc = q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY,
                    providers={"grok_voice": voice, "photon_sms": sms}, only=["R002"])
        v = next(r for r in rows(doc, "R002") if r["channel"] == "grok_voice")
        s = next(r for r in rows(doc, "R002") if r["channel"] == "photon_sms")
        assert v["status"] == "sent" and "3 attempts" in v["detail"]
        assert s["status"] == "failed"
        assert len(voice.calls) == 3 and len(sms.calls) == 3                 # first attempt + two retries each

    def test_restart_never_sends_the_same_channel_twice(self, data):
        voice, sms = Flaky(0), Flaky(0)
        q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY,
              providers={"grok_voice": voice, "photon_sms": sms}, only=["R002"])
        voice2, sms2 = Flaky(0), Flaky(0)
        doc = q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY,
                    providers={"grok_voice": voice2, "photon_sms": sms2}, only=["R002"])
        assert voice2.calls == [] and sms2.calls == []
        assert [r["status"] for r in rows(doc, "R002")] == ["sent", "sent"]
        assert len(rows(doc, "R002")) == 2

    def test_restart_continues_failed_rows(self, data):
        q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY,
              providers={"grok_voice": Flaky(99), "photon_sms": Flaky(0)}, only=["R003"])
        doc = q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY,
                    providers={"grok_voice": Flaky(0), "photon_sms": Flaky(0)}, only=["R003"])
        assert sorted((r["channel"], r["status"]) for r in rows(doc, "R003")) == [("grok_voice", "sent"), ("photon_sms", "sent")]
        assert len(rows(doc, "R003")) == 2

    def test_providers_default_to_unverified_stubs_in_real_mode(self, data):
        with pytest.raises(NotImplementedError):
            q.run(data, roster_path=data / "roster.csv", dry_run=False, today=TODAY, only=["R002"])


class TestRendering:
    def test_farmer_words_include_every_check_and_the_clear_ones(self):
        text = q.render_plan(plan("F0002", "act_now"), "pa")
        for label in ("harvest", "rain", "establish", "burn", "fire", "flood", "stress", "change"):
            assert label in text.lower()
        assert "clear" in text.lower() and "act now" in text.lower()
        assert "check the field for fire" in text.lower()

    def test_officer_words_list_the_route_in_order(self):
        route = {"as_of": "2026-10-17", "stops": [{"farm_id": "F0002", "action": "visit", "why": "Hotspot"},
                                                  {"farm_id": "F0001", "action": "call", "why": "Cut"}]}
        text = q.render_route(route, "en")
        assert text.lower().index("f0002") < text.lower().index("f0001")
        assert "visit" in text.lower() and "call" in text.lower()

    def test_empty_route_still_says_so(self):
        assert "no stops" in q.render_route({"as_of": "2026-10-17", "stops": []}, "en").lower()
