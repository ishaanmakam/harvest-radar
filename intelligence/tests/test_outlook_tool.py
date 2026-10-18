"""The analytics outlook reaches the farmer and the officer: get_outlook (same scope as get_plan), the inbound tool
dispatcher, the Grok Voice session, and an additive Outlook line in the morning words. The product name is one switch."""
import json

import pytest

import inbound
import morning
import queue_morning as q
import voice_agent
from common import product_name
from tools import Tools, load_roster

ROSTER_CSV = """roster_id,farm_id,name,phone,language,role
R001,,Officer Kaur,+919876500001,en,officer
R002,F0001,Gurpreet Singh,+919876500002,en,farmer
R003,F0002,Harjit Kaur,+919876500003,hi,farmer
"""


def plan(farm_id):
    checks = [{"kind": k, "status": "clear", "detail": "fine", "where": "", "window": {}} for k in
              ("harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change")]
    return {"farm_id": farm_id, "as_of": "2026-10-16", "flag": "fine", "source": "vegetation_index",
            "today": "Nothing needs action today.", "checks": checks, "risks": []}


ANALYTICS = {
    "as_of": "2026-10-16", "source": "intelligence/analytics.py", "modules": ["yield", "economics", "workability", "water"],
    "landscape": {}, "zones": {},
    "fields": {"F0001": {
        "yield": {"index": 1.08, "band": "high", "estimate": {"bu_per_acre": 211.0}},
        "economics": {"recommendation": "harvest_now", "reason": "drying costs less than another week standing", "savings_usd": 380.0},
        "workability": {"words": "88% chance to finish the 0.5 machine-days before the next rain"},
        "water": {"water_status": "dry", "days_since_rain": 11},
    }},
}


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    (tmp_path / "analytics.json").write_text(json.dumps(ANALYTICS), encoding="utf-8")
    return tmp_path


def roster(data):
    return load_roster(data / "roster.csv")


class TestGetOutlook:
    def test_farmer_gets_own_farm_only(self, data):
        rows = {r["roster_id"]: r for r in roster(data)}
        out = Tools(data, rows["R002"]).get_outlook()
        assert out["farm_id"] == "F0001" and out["as_of"] == "2026-10-16"
        assert any("high for the zone" in line for line in out["lines"]) and any("Harvest now" in line for line in out["lines"])
        assert out["detail"]["yield"]["band"] == "high"
        with pytest.raises(PermissionError):
            Tools(data, rows["R002"]).get_outlook("F0002")

    def test_officer_asks_any_farm_and_hears_when_there_is_nothing(self, data):
        rows = {r["roster_id"]: r for r in roster(data)}
        assert Tools(data, rows["R001"]).get_outlook("f0001")["farm_id"] == "F0001"
        out = Tools(data, rows["R001"]).get_outlook("F0002")
        assert out["lines"] == [] and "nothing beyond" in out["note"]
        with pytest.raises(ValueError):
            Tools(data, rows["R001"]).get_outlook()

    def test_unknown_caller_is_refused(self, data):
        with pytest.raises(PermissionError):
            Tools(data, None).get_outlook("F0001")

    def test_no_analytics_file_means_an_empty_outlook_not_an_error(self, data):
        (data / "analytics.json").unlink()
        rows = {r["roster_id"]: r for r in roster(data)}
        out = Tools(data, rows["R002"]).get_outlook()
        assert out == {"farm_id": "F0001", "as_of": None, "lines": [], "note": "no analytics for this season yet"}


class TestInboundAndVoice:
    def test_call_tool_dispatches_get_outlook(self, data):
        out = inbound.call_tool("get_outlook", {}, "+919876500002", data, roster(data))
        assert out["farm_id"] == "F0001" and out["lines"]
        assert "error" in inbound.call_tool("get_outlook", {"farm_id": "F0002"}, "+919876500002", data, roster(data))

    def test_voice_session_offers_the_tool_and_reads_the_outlook(self, data):
        cfg = voice_agent.session_config("+919876500002", data, roster(data))
        names = [t["name"] for t in cfg["session"]["tools"]]
        assert names == ["get_plan", "get_change", "get_outlook"]
        assert "Outlook: Yield outlook: high for the zone" in cfg["session"]["instructions"]
        officer = voice_agent.session_config("+919876500001", data, roster(data))
        assert "get_outlook" in [t["name"] for t in officer["session"]["tools"]]

    def test_voice_session_without_analytics_still_builds(self, data):
        (data / "analytics.json").unlink()
        cfg = voice_agent.session_config("+919876500002", data, roster(data))
        assert "Outlook:" not in cfg["session"]["instructions"] and cfg["session"]["tools"]


class TestWords:
    def test_outlook_line_is_additive(self):
        base = q.render_plan(plan("F0001"), "en")
        with_outlook = q.render_plan(plan("F0001"), "en", None, ["Yield outlook: high for the zone.", "Soil is dry."])
        assert "Outlook:" not in base
        assert with_outlook.startswith(base.rsplit("\n", 1)[0])                     # same words up to the risks line
        assert "Outlook: Yield outlook: high for the zone. Soil is dry." in with_outlook

    def test_hindi_and_punjabi_have_an_outlook_heading(self):
        for lang, head in (("hi", "आगे का अनुमान"), ("pa", "ਅੱਗੇ ਦਾ ਅੰਦਾਜ਼ਾ")):
            text = q.render_plan(plan("F0001"), lang, None, ["Soil is dry."])
            assert f"{head}: Soil is dry." in text

    def test_preview_and_queue_carry_the_outlook(self, data):
        plans = {p["farm_id"]: p for p in json.loads((data / "plans.json").read_text())["plans"]}
        route = json.loads((data / "route.json").read_text())
        msgs = morning.preview_messages(plans, route, roster(data), outlook_doc=ANALYTICS)
        by = {m["roster_id"]: m["text"] for m in msgs}
        assert "Outlook:" in by["R002"] and "Outlook:" not in by["R003"]
        out = morning.run(data, data / "roster.csv", preview=True, dry_run=True)
        texts = {m["roster_id"]: m["text"] for m in json.loads((data / "messages.json").read_text(encoding="utf-8"))["messages"]}
        assert "88% chance to finish" in texts["R002"]


class TestProductName:
    def test_default_and_override(self, monkeypatch):
        monkeypatch.delenv("PRODUCT_NAME", raising=False)
        assert product_name() == "Harvest Radar"
        assert q.render_plan(plan("F0001"), "en").startswith("Harvest Radar morning plan for farm F0001")
        monkeypatch.setenv("PRODUCT_NAME", "Overfield")
        assert q.render_plan(plan("F0001"), "en").startswith("Overfield morning plan for farm F0001")
        assert q.render_route({"as_of": "2026-10-16", "stops": []}, "en").startswith("Overfield officer route")

    def test_voice_uses_the_switch(self, data, monkeypatch):
        monkeypatch.setenv("PRODUCT_NAME", "Overfield")
        cfg = voice_agent.session_config("+919876500002", data, roster(data))
        assert "You are Overfield's voice assistant" in cfg["session"]["instructions"]
