"""Regression tests for the Oct 17 spec-review findings (confirmed by two independent refuters each) plus a few
cheap hardening items from the same review."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

import common
import decide
import harvest_model as hm
import queue_morning as q
import tools
from test_decide import DRY, QUIET_PASSES, check, rec, status_of

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 17)
D = date.fromisoformat


class TestNullCueFields:                                   # finding 1
    def test_null_where_and_detail_become_empty_strings_and_validate(self):
        cue = {"flood": {"kind": "flood", "observed": True, "date": "2026-10-16", "where": None, "detail": None, "confidence": None}}
        plan = decide.farm_plan(rec(), QUIET_PASSES, DRY, [], cue, TODAY)
        assert status_of(plan)["flood"] == "act_now"
        assert check(plan, "flood")["where"] == ""
        assert isinstance(check(plan, "flood")["detail"], str) and check(plan, "flood")["detail"]
        assert decide._validate("plans", {"as_of": "2026-10-16", "plans": [plan]}) == []

    def test_iso_datetime_in_cue_date_is_accepted(self):
        cue = {"flood": {"kind": "flood", "observed": True, "date": "2026-10-16T10:30:00Z", "where": "", "detail": "", "confidence": 0.5}}
        assert status_of(decide.farm_plan(rec(), QUIET_PASSES, DRY, [], cue, TODAY))["flood"] == "act_now"


class TestScarNewerThanIndexPass:                          # finding 2
    def test_perceptron_scar_dated_after_the_last_index_pass_is_new(self):
        burned = rec(stage="harvested", harvest_date="2026-10-04", predicted_harvest_start=None, predicted_harvest_end=None)
        passes = [(D("2026-10-06"), 0.24, 0.08), (D("2026-10-11"), 0.23, 0.07)]        # index null on the Oct 16 frame
        cue = {"burn_scar": {"kind": "burn_scar", "observed": True, "date": "2026-10-16", "where": "", "detail": "", "confidence": 0.9}}
        plan = decide.farm_plan(burned, passes, DRY, [], cue, TODAY)
        assert status_of(plan)["burn_scar"] == "act_now"


class TestEstablishmentWithoutPeak:                        # finding 3
    def test_missing_peak_but_green_passes_is_clear(self):
        r = rec(stage="unknown", peak_ndvi=None, peak_date=None, predicted_harvest_start=None, predicted_harvest_end=None,
                notes="too few clear observations")
        passes = [(D("2026-10-11"), 0.80, 0.45), (D("2026-10-16"), 0.79, 0.44)]
        assert status_of(decide.farm_plan(r, passes, DRY, [], None, TODAY))["establishment"] == "clear"

    def test_no_passes_is_watch_with_its_own_words_and_fallback_as_of(self):
        r = rec(stage="unknown", peak_ndvi=None, peak_date=None, predicted_harvest_start=None, predicted_harvest_end=None)
        plan = decide.farm_plan(r, [], DRY, [], None, TODAY, fallback_as_of=D("2026-10-16"))
        assert status_of(plan)["establishment"] == "watch"
        assert "never greened" not in plan["today"].lower()
        assert plan["as_of"] == "2026-10-16"


class TestAsOfFallback:                                    # findings 4 and 9
    def test_blank_field_takes_the_landscape_latest_clear_pass_not_today(self, tmp_path):
        data = ROOT / "data" / "mock"
        for name in ("status.json", "weather.json", "fires.json", "fields.geojson", "insights.json"):
            (tmp_path / name).write_bytes((data / name).read_bytes())
        ts = json.loads((data / "timeseries.json").read_text())
        n = len(ts["dates"])
        ts["fields"]["F0001"] = {"ndvi": [None] * n, "nbr": [None] * n, "clear_frac": [0.0] * n}
        (tmp_path / "timeseries.json").write_text(json.dumps(ts))
        plans, _ = decide.run(tmp_path)
        by_id = {p["farm_id"]: p for p in plans["plans"]}
        assert by_id["F0001"]["as_of"] == "2026-10-16"
        assert plans["as_of"] == "2026-10-16"


class TestCallerResolution:                                # findings 5 and 8
    roster = [{"roster_id": "R1", "farm_id": "F0001", "name": "a", "phone": "+919876500002", "language": "pa", "role": "farmer"}]

    def test_other_country_code_does_not_match(self):
        assert tools.resolve_caller("+19876500002", self.roster) is None
        assert tools.resolve_caller("+449876500002", self.roster) is None

    def test_exact_number_in_any_formatting_matches(self):
        assert tools.resolve_caller("+91 98765 00002", self.roster)["roster_id"] == "R1"
        assert tools.resolve_caller("919876500002", self.roster)["roster_id"] == "R1"

    def test_national_format_matches_by_tail(self):
        assert tools.resolve_caller("98765 00002", self.roster)["roster_id"] == "R1"

    def test_email_handles_never_match(self):
        assert tools.resolve_caller("user9876500002@icloud.com", self.roster) is None
        assert tools.resolve_caller("gs.9876500002@me.com", self.roster) is None

    def test_ambiguous_tail_returns_none(self):
        two = self.roster + [{"roster_id": "R2", "farm_id": "F0002", "name": "b", "phone": "+449876500002", "language": "en", "role": "farmer"}]
        assert tools.resolve_caller("9876500002", two) is None


class TestRosterValidation:                                # hardening from the review
    def test_duplicate_phone_is_rejected(self, tmp_path):
        (tmp_path / "r.csv").write_text("roster_id,farm_id,name,phone,language,role\n"
                                        "R1,F0001,A,+919876500002,pa,farmer\nR2,F0002,B,+919876500002,pa,farmer\n")
        with pytest.raises(ValueError, match="phone"):
            tools.load_roster(tmp_path / "r.csv")

    def test_two_rows_for_the_same_farm_are_allowed(self, tmp_path):
        # two household members can both receive the plan; the spec only forbids duplicate sends per roster row
        (tmp_path / "r.csv").write_text("roster_id,farm_id,name,phone,language,role\n"
                                        "R1,F0001,A,+919876500002,pa,farmer\nR2,F0001,B,+919876500003,pa,farmer\n")
        assert len(tools.load_roster(tmp_path / "r.csv")) == 2

    def test_unset_roster_path_is_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("ROSTER_PATH", raising=False)
        monkeypatch.setattr(tools, "load_env", lambda: None)
        monkeypatch.setenv("ACCOUNTS_DB", "")  # no sign-ups either: nothing to send to
        with pytest.raises(FileNotFoundError, match="ROSTER_PATH"):
            tools.load_roster(None)


class TestAtomicWrites:                                    # finding 6 (+ the same for plans/route)
    def test_checkpoint_goes_through_a_temp_file_and_replace(self, tmp_path, monkeypatch):
        calls = []
        real = os.replace

        def spy(src, dst):
            calls.append((str(src), str(dst)))
            real(src, dst)
        monkeypatch.setattr(os, "replace", spy)
        q.save_deliveries(tmp_path, {"deliveries": [{"date": "2026-10-17", "roster_id": "R1", "channel": "photon_sms",
                                                     "status": "pending", "detail": ""}]})
        assert calls and calls[-1][0] != calls[-1][1] and calls[-1][1].endswith("deliveries.json")
        assert json.loads((tmp_path / "deliveries.json").read_text())["deliveries"][0]["roster_id"] == "R1"
        assert not list(tmp_path.glob("*.tmp"))

    def test_write_json_is_atomic_too(self, tmp_path, monkeypatch):
        calls = []
        real = os.replace
        monkeypatch.setattr(os, "replace", lambda s, d: (calls.append((str(s), str(d))), real(s, d)))
        common.write_json(tmp_path, "plans.json", {"ok": True})
        assert calls and calls[-1][1].endswith("plans.json")
        assert json.loads((tmp_path / "plans.json").read_text()) == {"ok": True}


class TestOnlyFilter:                                      # hardening from the review
    def test_empty_only_sends_to_nobody(self, tmp_path):
        from test_queue import ROSTER_CSV, plan
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        doc = q.run(tmp_path, roster_path=tmp_path / "roster.csv", dry_run=True, today=TODAY, only=[])
        assert doc["deliveries"] == []


class TestLanguage:                                        # finding 7
    def sample(self):
        from test_queue import plan
        return plan("F0002", "act_now")

    def test_body_has_no_language_tag(self):
        assert "[pa]" not in q.render_plan(self.sample(), "pa") and "[en]" not in q.render_plan(self.sample(), "en")

    def test_punjabi_and_hindi_packs_render_structure_in_script(self):
        pa, hi = q.render_plan(self.sample(), "pa"), q.render_plan(self.sample(), "hi")
        assert any("਀" <= ch <= "੿" for ch in pa)          # Gurmukhi
        assert any("ऀ" <= ch <= "ॿ" for ch in hi)          # Devanagari
        assert "Harvest readiness" not in pa and "Harvest readiness" not in hi
        assert pa.count("\n- ") == 8 and hi.count("\n- ") == 8        # all eight checks still listed

    def test_unknown_language_falls_back_to_english(self):
        assert "Harvest readiness" in q.render_plan(self.sample(), "xx")

    def test_translate_hook_is_applied_to_free_text(self):
        seen = []

        def tr(text, lang):
            seen.append((text, lang))
            return f"<{lang}>{text}</{lang}>"
        out = q.render_plan(self.sample(), "pa", translate=tr)
        assert "<pa>Check the field for fire" in out
        assert all(lang == "pa" for _, lang in seen)
        route = q.render_route({"as_of": "2026-10-17", "stops": [{"farm_id": "F0002", "action": "visit", "why": "Hotspot"}]}, "hi", translate=tr)
        assert "<hi>Hotspot</hi>" in route


class TestPeakValue:                                       # finding 10
    def test_status_peak_is_the_smoothed_value_the_gate_used(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m, d in [(8, 1), (8, 6), (8, 11), (8, 16), (8, 21), (8, 26), (8, 31), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30), (10, 5), (10, 10), (10, 15)]]
        ndvi = [0.20, 0.30, 0.45, 0.58, 0.92, 0.62, 0.60, 0.58, 0.55, 0.50, 0.45, 0.40, 0.22, 0.21, 0.22, 0.21]   # one-sample spike
        nbr = [0.1] * 16
        th = {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.35, "harvest_drop_min": 0.2, "burn_nbr_max": -0.05,
              "sown_ndvi_min": 0.3, "default_peak_to_harvest_days": 40, "peak_plateau_drop": 0.04}
        r, _ = hm.analyze_field("F1", "Z01", dates, ndvi, nbr, {}, th)
        sm = hm.median_smooth(ndvi)
        assert r["peak_ndvi"] == round(max(sm), 3)
        assert r["peak_ndvi"] < 0.92


class TestFarmIdCase:                                      # hardening from the review
    def test_lowercase_own_farm_id_is_accepted(self, tmp_path):
        from test_inbound import ROSTER_CSV, plan
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        roster = tools.load_roster(tmp_path / "roster.csv")
        t = tools.Tools(tmp_path, tools.resolve_caller("+919876500003", roster))
        assert t.get_plan("f0002")["farm_id"] == "F0002"
        officer = tools.Tools(tmp_path, tools.resolve_caller("+919876500001", roster))
        assert officer.get_plan(" f0001 ")["farm_id"] == "F0001"


class TestForecastLengthMismatch:                          # hardening from the review
    def test_short_precip_array_is_padded_not_truncated(self):
        pairs = decide.forecast_pairs(["2026-10-17", "2026-10-18", "2026-10-19"], [0.0, 16.0])
        assert pairs == [(D("2026-10-17"), 0.0), (D("2026-10-18"), 16.0), (D("2026-10-19"), None)]
