"""The inbound HTTP service: Twilio voice webhook (caller hears their plan, can ask by speech), Photon inbound
webhook (officer iMessage), the tools endpoint for a voice agent, health, and Twilio request signatures."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import inbound_server as srv
import queue_morning as q
import tools
from test_inbound import ROSTER_CSV, plan


@pytest.fixture
def service(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    app = srv.make_handler(tmp_path, tmp_path / "roster.csv", bridge_token="shh", twilio_auth_token=None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post_form(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode()


def post_json(url, data, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class TestHealth:
    def test_health_reports_files(self, service):
        with urllib.request.urlopen(service + "/health", timeout=5) as r:
            doc = json.loads(r.read())
        assert doc["ok"] is True and doc["plans"] == 2 and doc["route_stops"] == 1


class TestTwilioVoice:
    def test_farmer_hears_their_plan_then_can_ask(self, service):
        status, ctype, body = post_form(service + "/twilio/voice", {"From": "+919876500003", "To": "+15550001111", "CallSid": "CA1"})
        assert status == 200 and ctype.startswith("text/xml")
        assert body.startswith("<?xml") and "<Response>" in body
        assert 'language="hi-IN"' in body                                  # roster language of R003
        assert "F0002" in body and "Field cut between" in body
        assert '<Gather input="speech"' in body and 'action="/twilio/voice"' in body

    def test_speech_question_is_answered(self, service):
        _, _, body = post_form(service + "/twilio/voice", {"From": "+919876500003", "SpeechResult": "what changed on my field"})
        assert "cut between 2026-10-11 and 2026-10-16" in body
        assert "<Gather" in body                                            # the caller can keep asking

    def test_unknown_caller_is_told_once_and_hung_up(self, service):
        _, _, body = post_form(service + "/twilio/voice", {"From": "+15550000000"})
        assert tools.UNKNOWN_CALLER_MESSAGE in body and "<Hangup/>" in body and "<Gather" not in body
        assert "F000" not in body

    def test_officer_hears_the_route(self, service):
        _, _, body = post_form(service + "/twilio/voice", {"From": "+919876500001"})
        assert q.pack("pa")["route_head"].split("{")[0] in body and "F0002" in body

    def test_text_is_xml_escaped(self, service, tmp_path):
        plans = json.loads((tmp_path / "plans.json").read_text())
        plans["plans"][0]["today"] = "Rain & wind <tonight>"
        (tmp_path / "plans.json").write_text(json.dumps(plans))
        _, _, body = post_form(service + "/twilio/voice", {"From": "+919876500002"})
        assert "Rain &amp; wind &lt;tonight&gt;" in body


class TestStreamTwiml:
    def test_stream_twiml_points_at_the_bridge_with_the_person_on_the_line(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        app = srv.make_handler(tmp_path, tmp_path / "roster.csv", public_ws_url="wss://bridge.example")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            _, ctype, body = post_form(base + "/twilio/stream", {"From": "+919876500002"})          # inbound call
            assert ctype.startswith("text/xml")
            assert '<Stream url="wss://bridge.example/media-stream">' in body
            assert '<Parameter name="from" value="+919876500002"/>' in body
            _, _, body = post_form(base + "/twilio/stream?to=%2B919876500003&language=hi", {"CallSid": "CA2"})   # outbound call
            assert 'value="+919876500003"' in body
        finally:
            httpd.shutdown()

    def test_without_a_bridge_url_the_call_is_told_and_hung_up(self, service):
        with pytest.raises(urllib.error.HTTPError) as e:
            post_form(service + "/twilio/stream", {"From": "+919876500002"})
        assert e.value.code == 503


class TestPhotonInbound:
    def test_officer_imessage_gets_a_reply_json(self, service):
        status, doc = post_json(service + "/photon/inbound", {"from": "+919876500001", "text": "plan for F0001"},
                                headers={"Authorization": "Bearer shh"})
        assert status == 200 and doc["to"] == "+919876500001" and "F0001" in doc["reply"]

    def test_alternate_field_names_are_accepted(self, service):
        status, doc = post_json(service + "/photon/inbound", {"sender": "+919876500001", "message": "route"},
                                headers={"Authorization": "Bearer shh"})
        assert status == 200 and "F0002" in doc["reply"]

    def test_wrong_token_is_rejected_without_data(self, service):
        status, doc = post_json(service + "/photon/inbound", {"from": "+919876500001", "text": "plan for F0001"},
                                headers={"Authorization": "Bearer nope"})
        assert status == 401 and "F0001" not in json.dumps(doc)

    def test_unknown_texter_gets_only_the_no_farm_message(self, service):
        status, doc = post_json(service + "/photon/inbound", {"from": "+15550000000", "text": "plan for F0001"},
                                headers={"Authorization": "Bearer shh"})
        assert status == 200 and doc["reply"] == tools.UNKNOWN_CALLER_MESSAGE


class TestToolsEndpoint:
    def test_voice_agent_tool_calls_are_scoped(self, service):
        status, doc = post_json(service + "/tools/get_plan", {"handle": "+919876500002", "args": {"farm_id": "F0001"}},
                                headers={"Authorization": "Bearer shh"})
        assert status == 200 and doc["farm_id"] == "F0001"
        status, doc = post_json(service + "/tools/get_route", {"handle": "+919876500002", "args": {}},
                                headers={"Authorization": "Bearer shh"})
        assert status == 403 and doc["error"]
        status, doc = post_json(service + "/tools/get_route", {"handle": "+919876500001", "args": {}},
                                headers={"Authorization": "Bearer shh"})
        assert status == 200 and doc["stops"][0]["farm_id"] == "F0002"


class TestTwilioSignature:
    def test_signature_algorithm_matches_twilio_docs(self):
        # Twilio: HMAC-SHA1 over the full URL followed by the POST params sorted by key, key+value concatenated
        # the worked example in Twilio's "Validating requests are coming from Twilio" docs
        url = "https://example.com/myapp.php?foo=1&bar=2"
        params = {"Digits": "1234", "To": "+18005551212", "From": "+14158675310", "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE"}
        sig = srv.twilio_signature(url, params, "12345")
        assert sig == "L/OH5YylLD5NRKLltdqwSvS0BnU="
        assert srv.twilio_signature_ok(url, params, sig, "12345")
        assert not srv.twilio_signature_ok(url, params, "bad", "12345")

    def test_server_rejects_unsigned_twilio_requests_when_token_is_set(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        app = srv.make_handler(tmp_path, tmp_path / "roster.csv", bridge_token=None, twilio_auth_token="tok",
                               public_url="https://example.test")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            body = urllib.parse.urlencode({"From": "+919876500002"}).encode()
            req = urllib.request.Request(base + "/twilio/voice", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            assert e.value.code == 403
            sig = srv.twilio_signature("https://example.test/twilio/voice", {"From": "+919876500002"}, "tok")
            req = urllib.request.Request(base + "/twilio/voice", data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": sig})
            with urllib.request.urlopen(req, timeout=5) as r:
                assert r.status == 200
        finally:
            httpd.shutdown()
