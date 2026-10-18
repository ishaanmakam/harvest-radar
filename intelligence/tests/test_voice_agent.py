"""voice_agent.py: the Grok Voice session for a call (instructions built from the caller's own plan, the three
shared tools as function tools, scoped dispatch) and the pure mapping between Twilio Media Streams frames and
realtime-API events used by the bridge. No network here."""
import base64
import json

import pytest

import tools
import voice_agent as va
from test_inbound import ROSTER_CSV, plan


@pytest.fixture
def world(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path, tools.load_roster(tmp_path / "roster.csv")


class TestSession:
    def test_farmer_session_carries_their_plan_language_and_rules(self, world):
        data, roster = world
        cfg = va.session_config("+919876500003", data, roster)
        assert cfg["type"] == "session.update"
        s = cfg["session"]
        assert s["voice"] in va.VOICES
        assert s["turn_detection"] == {"type": "server_vad"}
        ins = s["instructions"]
        assert "Hindi" in ins and "F0002" in ins and "Field cut between" in ins
        assert "never invent" in ins.lower() and "only" in ins.lower()
        assert "F0001" not in ins                                            # no other farm's facts in a farmer session
        names = [t["name"] for t in s["tools"]]
        assert names == ["get_plan", "get_change", "get_outlook"]                           # a farmer session has no route tool

    def test_officer_session_has_the_route_and_all_four_tools(self, world):
        data, roster = world
        s = va.session_config("+919876500001", data, roster)["session"]
        assert "Punjabi" in s["instructions"] and "F0002" in s["instructions"] and "route" in s["instructions"].lower()
        assert [t["name"] for t in s["tools"]] == ["get_plan", "get_route", "get_change", "get_outlook"]
        assert all(t["type"] == "function" and "parameters" in t for t in s["tools"])

    def test_unknown_caller_session_says_so_and_has_no_tools(self, world):
        data, roster = world
        s = va.session_config("+15550000000", data, roster)["session"]
        assert tools.UNKNOWN_CALLER_MESSAGE in s["instructions"] and s["tools"] == []
        assert "F000" not in s["instructions"]

    def test_audio_format_defaults_to_telephony_mulaw(self, world):
        data, roster = world
        s = va.session_config("+919876500003", data, roster)["session"]
        assert s["audio"]["input"]["format"] == {"type": "audio/pcmu"}
        assert s["audio"]["output"]["format"] == {"type": "audio/pcmu"}


class TestToolDispatch:
    def test_tool_call_results_are_scoped_and_json_strings(self, world):
        data, roster = world
        out = va.run_tool("get_plan", json.dumps({"farm_id": "F0002"}), "+919876500003", data, roster)
        assert json.loads(out)["farm_id"] == "F0002"
        denied = va.run_tool("get_plan", json.dumps({"farm_id": "F0001"}), "+919876500003", data, roster)
        assert "error" in json.loads(denied)
        bad = va.run_tool("get_plan", "{not json", "+919876500003", data, roster)
        assert "error" in json.loads(bad)


class TestTwilioFrames:
    def test_media_frame_becomes_an_audio_append_event(self):
        payload = base64.b64encode(b"\xff\x7f" * 80).decode()
        ev = va.twilio_to_realtime({"event": "media", "media": {"payload": payload}})
        assert ev == {"type": "input_audio_buffer.append", "audio": payload}

    def test_non_media_frames_produce_nothing(self):
        for frame in ({"event": "connected"}, {"event": "start", "start": {"streamSid": "MZ1"}}, {"event": "stop"}, {"event": "mark"}):
            assert va.twilio_to_realtime(frame) is None

    def test_audio_delta_becomes_a_twilio_media_frame(self):
        out = va.realtime_to_twilio({"type": "response.output_audio.delta", "delta": "AAAA"}, "MZ1")
        assert out == [{"event": "media", "streamSid": "MZ1", "media": {"payload": "AAAA"}}]

    def test_speech_started_clears_twilio_playback(self):
        out = va.realtime_to_twilio({"type": "input_audio_buffer.speech_started"}, "MZ1")
        assert out == [{"event": "clear", "streamSid": "MZ1"}]

    def test_function_call_arrives_as_an_output_item_done(self):
        # xAI's example reads tool calls from response.output_item.done with item.type == "function_call"
        ev = {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c1", "name": "get_plan",
                                                            "arguments": "{\"farm_id\": \"F0002\"}"}}
        assert va.function_call_from(ev) == ("c1", "get_plan", "{\"farm_id\": \"F0002\"}")
        assert va.function_call_from({"type": "response.output_item.done", "item": {"type": "message"}}) is None
        assert va.function_call_from({"type": "response.output_audio.delta", "delta": "x"}) is None

    def test_greeting_event_shape(self):
        ev = va.greeting_event()
        assert ev["type"] == "conversation.item.create" and ev["item"]["role"] == "user"
        assert ev["item"]["content"][0]["type"] == "input_text"

    def test_tool_result_events(self):
        evs = va.tool_result_events("c1", "{\"ok\": true}")
        assert evs[0] == {"type": "conversation.item.create",
                          "item": {"type": "function_call_output", "call_id": "c1", "output": "{\"ok\": true}"}}
        assert evs[1] == {"type": "response.create"}
