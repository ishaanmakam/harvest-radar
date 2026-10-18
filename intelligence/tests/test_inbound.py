"""Inbound handler tests: caller ID / handle -> roster row; farmer voice reads only that farm; officer voice and
iMessage read the route and any farm; unknown numbers get "no farm registered" and nothing else."""
import json

import pytest

import inbound
import tools

ROSTER_CSV = """roster_id,farm_id,name,phone,language,role
R001,,Officer Kaur,+919876500001,pa,officer
R002,F0001,Gurpreet Singh,+919876500002,pa,farmer
R003,F0002,Harjit Singh,+919876500003,hi,farmer
"""


def plan(farm_id, flag="fine"):
    checks = [{"kind": k, "status": "clear", "detail": f"{k} is clear", "where": "", "window": {}} for k in tools.KINDS]
    today = "Nothing needs action today."
    if flag == "watch":
        checks[7] = {"kind": "visible_change", "status": "watch", "detail": "Field cut between 2026-10-11 and 2026-10-16",
                     "where": "", "window": {"start": "2026-10-11", "end": "2026-10-16"}}
        today = "Something changed since the last pass: confirm what happened in the field."
    return {"farm_id": farm_id, "as_of": "2026-10-16", "flag": flag, "source": "vegetation_index", "today": today,
            "checks": checks, "risks": [c["detail"] for c in checks if c["status"] != "clear"]}


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path


@pytest.fixture
def roster(data):
    return tools.load_roster(data / "roster.csv")


class TestUnknownCaller:
    def test_gets_only_the_no_farm_message(self, data, roster):
        reply = inbound.answer("+1 555 000 0000", "what is my plan", data, roster)
        assert reply == tools.UNKNOWN_CALLER_MESSAGE
        assert "F000" not in reply


class TestFarmer:
    def test_default_question_returns_own_plan_with_every_check(self, data, roster):
        reply = inbound.answer("+91 98765 00003", "what should I do today", data, roster)
        assert "F0002" in reply and "[hi]" in reply
        assert "Field cut between" in reply
        for label in ("Fire", "Flood", "Burn scar", "Establishment"):
            assert label in reply                                     # clear checks are read out too

    def test_cannot_read_another_farm(self, data, roster):
        reply = inbound.answer("+919876500003", "tell me about F0001", data, roster)
        assert "own farm" in reply.lower()
        assert "F0001" not in reply.replace("F0001", "", 0)            # no plan data for the other farm
        assert "clear" not in reply.lower()

    def test_what_changed_gives_the_change(self, data, roster):
        reply = inbound.answer("+919876500003", "what changed on my field?", data, roster)
        assert "cut between 2026-10-11 and 2026-10-16" in reply
        assert "2026-10-16" in reply


class TestOfficer:
    def test_default_question_is_the_route(self, data, roster):
        reply = inbound.answer("+919876500001", "where do I go first", data, roster)
        assert "route" in reply.lower() and "F0002" in reply and "call" in reply.lower()

    def test_named_farm_gives_that_plan(self, data, roster):
        reply = inbound.answer("+919876500001", "plan for F0001 please", data, roster)
        assert "farm F0001" in reply and "All eight checks are clear" in reply

    def test_named_farm_change(self, data, roster):
        reply = inbound.answer("+919876500001", "what changed at f0002", data, roster)
        assert "cut between" in reply.lower()

    def test_unknown_farm_is_said_plainly(self, data, roster):
        reply = inbound.answer("+919876500001", "F9999 status", data, roster)
        assert "F9999" in reply and "no plan" in reply.lower()


class TestChannels:
    def test_imessage_handle_resolves_like_a_phone(self, data, roster):
        assert "route" in inbound.answer("tel:+919876500001", "route", data, roster).lower()

    def test_tools_json_contract_for_voice_agent(self, data, roster):
        """The voice agent calls the same three tools; expose them as a JSON-friendly dispatcher."""
        out = inbound.call_tool("get_plan", {"farm_id": "F0001"}, "+919876500001", data, roster)
        assert out["farm_id"] == "F0001"
        out = inbound.call_tool("get_route", {}, "+919876500001", data, roster)
        assert out["stops"][0]["farm_id"] == "F0002"
        out = inbound.call_tool("get_change", {"farm_id": "F0002"}, "+919876500001", data, roster)
        assert out["status"] == "watch"
        denied = inbound.call_tool("get_route", {}, "+919876500002", data, roster)
        assert denied["error"]
        unknown = inbound.call_tool("get_plan", {"farm_id": "F0001"}, "+15550000000", data, roster)
        assert unknown["error"] == tools.UNKNOWN_CALLER_MESSAGE
