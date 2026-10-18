"""voice_guard.py: the output gate for the morning words. Every banned category is caught, clean text passes as the
same object, a regenerate hook gets one chance and its clean result is used, a second failure is scrubbed as the
module docstring says, and the queue's render functions go through the gate without changing the dry run."""
import json
import logging
from pathlib import Path

import pytest

import queue_morning as q
import tools
import voice_agent as va
import voice_guard as vg
from test_queue import ROSTER_CSV, TODAY, Spy, plan

ROOT = Path(__file__).resolve().parents[2]

BAD = [  # (text, the category that must catch it)
    ("Harvest today — the rain comes at 15:00.", "dashes"),
    ("Rain 12–18 mm on 2026-10-18.", "dashes"),
    ("Fire · flood · stress", "glyphs"),
    ("Stress → act now", "glyphs"),
    ("Greenness ↗ since 2026-10-11", "glyphs"),
    ("The field is drying… cut on Monday.", "glyphs"),
    ('He said "wait..." and left.', "glyphs"),
    ("Harvest today \U0001F33E", "emoji"),
    ("Fire inside the field \U0001F525\U0001F525", "emoji"),
    ("Cut the beans today!", "exclamation"),
    ("Fire on F0012! Call the officer.", "exclamation"),
    ("Unlock a seamless harvest with data-driven insight.", "copy-words"),
    ("A robust and powerful plan for your landscape.", "copy-words"),
    ("Let's get started with today's plan.", "copy-frames"),
    ("Simply call the officer. Hope this helps.", "copy-frames"),
    ("Oops, something went wrong.", "copy-frames"),
    ("Your AI field inspector read the pass.", "ai-word"),
    ("An A.I. agent and an LLM wrote this.", "ai-word"),
    ("Built on machine learning and artificial intelligence.", "ai-word"),
    ("An agentic morning.", "ai-word"),
]
GOOD = [
    "Overfield morning plan for farm F0012, from the satellite pass of 2026-10-16. Flag: act now.",
    "Rain 12 to 18 mm on 2026-10-18 (NWS forecast).",
    "Harvest 3-5 t/ha; the window is 2026-10-17 to 2026-10-19.",
    "Simplify nothing: the plan names the field, the operation, the amount and the time.",
    "Rain and the main road are both fine.",
    "RAIN: 4 mm (Daymet).",
    "© 2026 Overfield™",
    "सभी आठ जाँचें ठीक हैं।",
    "Call the officer, not the farm.",
    "The harvest and the hardest field.",
    "",
]
SCRUBS = [  # (dirty, scrubbed)
    ("Harvest today — the rain comes at 15:00.", "Harvest today, the rain comes at 15:00."),
    ("Harvest today —", "Harvest today."),
    ("Harvest today –\nRain tomorrow", "Harvest today.\nRain tomorrow"),
    ("Harvest today. — Rain tomorrow.", "Harvest today. Rain tomorrow."),
    ("— Harvest today", "Harvest today"),
    ("Fire · flood", "Fire / flood"),
    ("Drying… cut on Monday.", "Drying. cut on Monday."),
    ('Say "wait..." now', 'Say "wait." now'),
    ("Stress → act now", "Stress to act now"),
    ("Harvest today \U0001F33E", "Harvest today"),
    ("\U0001F525 Fire inside the field", "Fire inside the field"),
    ("Fire \U0001F525️ inside the field", "Fire inside the field"),
    ("Cut the beans today!", "Cut the beans today."),
    ("Fire on F0012! Call the officer.", "Fire on F0012. Call the officer."),
    ("Fire on F0012!! Call the officer.", "Fire on F0012. Call the officer."),
]


class TestViolations:
    @pytest.mark.parametrize("text,category", BAD)
    def test_every_bad_fixture_is_caught_by_its_category(self, text, category):
        assert category in {c for c, _ in vg.violations(text)}

    def test_the_bad_fixtures_cover_every_category(self):
        assert {c for _, c in BAD} == set(vg.CATEGORY_NAMES)

    @pytest.mark.parametrize("text", GOOD)
    def test_clean_fixtures_pass(self, text):
        assert vg.violations(text) == []

    def test_matches_say_what_was_found(self):
        found = vg.violations("Cut the beans today! Unlock the gate — now.")
        assert ("exclamation", "y!") in found
        assert ("copy-words", "Unlock") in found
        assert ("dashes", "—") in found
        assert vg.categories_of(found) == ["dashes", "exclamation", "copy-words"]

    def test_the_voice_section_and_the_doc_obey_their_own_rules(self):
        assert vg.violations(vg.VOICE_OF_THE_MORNING) == []
        doc = (ROOT / "docs" / "agent-voice.md").read_text(encoding="utf-8")
        assert "Voice of the morning messages" in doc
        assert vg.violations(doc) == []


class TestEnforce:
    @pytest.mark.parametrize("text", GOOD)
    def test_clean_text_comes_back_as_the_same_object(self, text):
        logs = []
        assert vg.enforce(text, log=logs.append) is text
        assert logs == []

    def test_the_rendered_plan_is_clean_and_passes_untouched(self):
        text = q.render_plan(plan("F0002", "act_now"), "en")
        assert vg.violations(text) == []
        assert vg.enforce(text) is text

    def test_non_text_passes_through(self):
        assert vg.enforce(None) is None

    def test_regenerate_is_called_once_with_the_category_names_and_its_clean_result_is_used(self):
        calls, logs = [], []

        def regenerate(categories):
            calls.append(categories)
            return "Cut the beans today."

        out = vg.enforce("Cut the beans today! \U0001F33E", regenerate=regenerate, log=logs.append)
        assert out == "Cut the beans today."
        assert calls == [["emoji", "exclamation"]]
        assert any("voice guard" in line and "emoji" in line and "exclamation" in line for line in logs)
        assert any("regenerated text is clean" in line for line in logs)
        assert not any("scrubbed" in line for line in logs)

    def test_a_second_failure_is_scrubbed(self):
        calls, logs = [], []

        def regenerate(categories):
            calls.append(categories)
            return "Rain at 15:00 — cut the beans now!"

        out = vg.enforce("Cut the beans now!", regenerate=regenerate, log=logs.append)
        assert out == "Rain at 15:00, cut the beans now."
        assert calls == [["exclamation"]]
        assert any("still fails" in line and "dashes" in line for line in logs)
        assert any("scrubbed dashes, exclamation" in line for line in logs)

    def test_a_regenerate_hook_that_returns_nothing_falls_through_to_the_scrub(self):
        out = vg.enforce("Fire · flood", regenerate=lambda cats: None, log=lambda line: None)
        assert out == "Fire / flood"

    @pytest.mark.parametrize("dirty,clean", SCRUBS)
    def test_scrub_rules(self, dirty, clean):
        assert vg.scrub(dirty) == clean
        assert vg.enforce(dirty, log=lambda line: None) == clean

    def test_banned_words_and_frames_are_left_in_place_and_logged(self):
        logs = []
        out = vg.enforce("Unlock a seamless harvest — today. Hope this helps!", log=logs.append)
        assert out == "Unlock a seamless harvest, today. Hope this helps."
        left = [line for line in logs if "left in place" in line]
        assert len(left) == 1 and "copy-words" in left[0] and "copy-frames" in left[0]

    def test_the_default_log_is_the_module_logger(self, caplog):
        with caplog.at_level(logging.WARNING, logger="voice_guard"):
            assert vg.enforce("Cut now!") == "Cut now."
        assert "voice guard" in caplog.text and "exclamation" in caplog.text


class TestQueueWiring:
    def dirty_plan(self):
        p = plan("F0001", "act_now")
        p["today"] = "Check the field for fire now — call the officer if it is burning!"
        p["checks"][4]["detail"] = "Hotspot on 2026-10-16 \U0001F525 inside the field"
        p["risks"] = [p["checks"][4]["detail"]]
        return p

    def test_render_plan_goes_through_the_gate_and_prints_the_violations(self, capsys):
        text = q.render_plan(self.dirty_plan(), "en")
        assert "—" not in text and "\U0001F525" not in text and "!" not in text
        assert "fire now, call the officer if it is burning." in text
        assert "Hotspot on 2026-10-16 inside the field" in text
        out = capsys.readouterr().out
        assert "voice guard" in out and "dashes" in out and "emoji" in out and "exclamation" in out and "scrubbed" in out

    def test_render_route_goes_through_the_gate(self, capsys):
        route = {"as_of": "2026-10-17", "stops": [{"farm_id": "F0002", "action": "visit", "why": "Hotspot → act now!"}]}
        text = q.render_route(route, "en")
        assert text.endswith("visit. Hotspot to act now.")
        assert "voice guard" in capsys.readouterr().out

    def test_dry_run_is_unchanged_with_dirty_words(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [self.dirty_plan(), plan("F0002")]}),
                                             encoding="utf-8")
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
            {"farm_id": "F0001", "action": "visit", "why": "Hotspot — act now!"}]}), encoding="utf-8")
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        spy = Spy()
        doc = q.run(tmp_path, roster_path=tmp_path / "roster.csv", dry_run=True, today=TODAY,
                    providers={"grok_voice": spy, "photon_sms": spy})
        assert spy.calls == []
        assert len(doc["deliveries"]) == 10
        assert sum(r["status"] == "pending" for r in doc["deliveries"]) == 6

    def test_every_channel_gets_the_guarded_words(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [self.dirty_plan()]}), encoding="utf-8")
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}), encoding="utf-8")
        (tmp_path / "roster.csv").write_text("roster_id,farm_id,name,phone,email,language,role\n"
                                             "R002,F0001,Gurpreet Singh,+919876500002,g@example.org,en,farmer\n")
        seen = {}

        class Keep:
            def __init__(self, channel):
                self.channel = channel

            def send(self, to, text, language):
                seen[self.channel] = text
                return "ok"

        q.run(tmp_path, roster_path=tmp_path / "roster.csv", dry_run=False, today=TODAY,
              providers={c: Keep(c) for c in q.CHANNELS})
        assert set(seen) == {"grok_voice", "photon_sms", "email"}
        assert all(vg.violations(text) == [] for text in seen.values())
        assert len({seen[c] for c in seen}) == 1


class TestVoiceSession:
    def test_the_grok_voice_instructions_carry_the_voice_section(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0002", "act_now")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        roster = tools.load_roster(tmp_path / "roster.csv")
        for handle in ("+919876500003", "+919876500001"):
            ins = va.session_config(handle, tmp_path, roster)["session"]["instructions"]
            assert "Voice of the morning messages" in ins
            assert "name the field, the operation, the amount and the time" in ins
