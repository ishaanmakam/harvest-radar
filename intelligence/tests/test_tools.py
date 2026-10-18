"""Roster boundary tests (spec Testing section): a farmer tool cannot read another farm, an unknown caller gets no
farm data, the officer can read the route and any farm."""
import json
from pathlib import Path

import pytest

import tools

ROSTER_CSV = """roster_id,farm_id,name,phone,language,role
R001,,Officer Kaur,+91 98765 00001,pa,officer
R002,F0001,Gurpreet Singh,+91-98765-00002,pa,farmer
R003,F0002,Harjit Singh,+919876500003,hi,farmer
"""


def plan(farm_id, flag="fine"):
    checks = [{"kind": k, "status": "clear", "detail": f"{k} clear", "where": "", "window": {}} for k in tools.KINDS]
    if flag != "fine":
        checks[7] = {"kind": "visible_change", "status": "watch", "detail": "Field cut between 2026-10-11 and 2026-10-16",
                     "where": "", "window": {"start": "2026-10-11", "end": "2026-10-16"}}
    return {"farm_id": farm_id, "as_of": "2026-10-16", "flag": flag, "source": "vegetation_index",
            "today": "x", "checks": checks, "risks": [c["detail"] for c in checks if c["status"] != "clear"]}


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path


class TestRoster:
    def test_loads_rows_and_normalises_phones(self, data):
        roster = tools.load_roster(data / "roster.csv")
        assert [r["roster_id"] for r in roster] == ["R001", "R002", "R003"]
        assert roster[1]["phone"] == "+919876500002"

    def test_language_is_required_on_every_row(self, tmp_path):
        (tmp_path / "r.csv").write_text("roster_id,farm_id,name,phone,language,role\nR1,F0001,A,+911111111111,,farmer\n")
        with pytest.raises(ValueError):
            tools.load_roster(tmp_path / "r.csv")

    def test_farmer_rows_need_a_farm_and_roles_are_checked(self, tmp_path):
        (tmp_path / "r.csv").write_text("roster_id,farm_id,name,phone,language,role\nR1,,A,+911111111111,pa,farmer\n")
        with pytest.raises(ValueError):
            tools.load_roster(tmp_path / "r.csv")
        (tmp_path / "r2.csv").write_text("roster_id,farm_id,name,phone,language,role\nR1,F0001,A,+911111111111,pa,boss\n")
        with pytest.raises(ValueError):
            tools.load_roster(tmp_path / "r2.csv")

    def test_resolve_caller_by_phone_in_any_format(self, data):
        roster = tools.load_roster(data / "roster.csv")
        assert tools.resolve_caller("+91 98765 00002", roster)["roster_id"] == "R002"
        assert tools.resolve_caller("919876500003", roster)["roster_id"] == "R003"
        assert tools.resolve_caller("+1 555 000 0000", roster) is None


class TestFarmerScope:
    def test_farmer_reads_own_plan_only(self, data):
        roster = tools.load_roster(data / "roster.csv")
        t = tools.Tools(data, tools.resolve_caller("+919876500002", roster))
        assert t.get_plan("F0001")["farm_id"] == "F0001"
        with pytest.raises(PermissionError):
            t.get_plan("F0002")
        with pytest.raises(PermissionError):
            t.get_change("F0002")
        with pytest.raises(PermissionError):
            t.get_route()

    def test_farmer_default_farm_is_their_own(self, data):
        roster = tools.load_roster(data / "roster.csv")
        t = tools.Tools(data, tools.resolve_caller("+919876500003", roster))
        assert t.get_plan()["farm_id"] == "F0002"
        assert t.get_change()["status"] == "watch"


class TestOfficerScope:
    def test_officer_reads_route_and_any_farm(self, data):
        roster = tools.load_roster(data / "roster.csv")
        t = tools.Tools(data, tools.resolve_caller("+919876500001", roster))
        assert [s["farm_id"] for s in t.get_route()["stops"]] == ["F0002"]
        assert t.get_plan("F0001")["flag"] == "fine"
        ch = t.get_change("F0002")
        assert ch["farm_id"] == "F0002" and ch["window"] == {"start": "2026-10-11", "end": "2026-10-16"}
        assert "cut" in ch["detail"].lower()

    def test_unknown_farm_raises_key_error(self, data):
        roster = tools.load_roster(data / "roster.csv")
        t = tools.Tools(data, tools.resolve_caller("+919876500001", roster))
        with pytest.raises(KeyError):
            t.get_plan("F9999")


class TestUnknownCaller:
    def test_no_caller_means_no_farm_data(self, data):
        t = tools.Tools(data, None)
        with pytest.raises(PermissionError):
            t.get_plan("F0001")
        with pytest.raises(PermissionError):
            t.get_route()
        assert "no farm is registered" in tools.UNKNOWN_CALLER_MESSAGE.lower()
