"""Provider adapters behind the morning queue: an outbound call through Twilio's REST API (text-to-speech now, a
Grok Voice media stream when VOICE_TWIML_URL is set) and an SMS through the Photon bridge contract. Tested with a
fake HTTP transport; nothing here touches the network."""
import base64
import json
from datetime import date
from urllib.parse import parse_qs

import pytest

import providers as pv
import queue_morning as q

TODAY = date(2026, 10, 17)


class FakeHttp:
    """Records requests; answers from a queue of (status, body) tuples."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(201, {"sid": "CA123"})]
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, payload = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return status, json.dumps(payload).encode()


class TestTwilioVoice:
    def test_places_a_call_with_spoken_plan_in_the_roster_language(self):
        http = FakeHttp((201, {"sid": "CA123", "status": "queued"}))
        v = pv.TwilioVoice("ACxxx", "tok", "+15550001111", http=http)
        result = v.send("+919876500002", "Harvest before 2026-10-19: 16 mm & rain.", "hi")
        assert result == "twilio call CA123 (queued)"
        c = http.calls[0]
        assert c["method"] == "POST"
        assert c["url"] == "https://api.twilio.com/2010-04-01/Accounts/ACxxx/Calls.json"
        assert c["headers"]["Authorization"] == "Basic " + base64.b64encode(b"ACxxx:tok").decode()
        form = parse_qs(c["body"].decode())
        assert form["To"] == ["+919876500002"] and form["From"] == ["+15550001111"]
        twiml = form["Twiml"][0]
        assert twiml.startswith("<Response>") and 'language="hi-IN"' in twiml
        assert "&amp;" in twiml and "<Say" in twiml                     # text is XML-escaped inside <Say>

    def test_streams_to_grok_voice_when_a_twiml_url_is_configured(self):
        http = FakeHttp((201, {"sid": "CA9", "status": "queued"}))
        v = pv.TwilioVoice("ACxxx", "tok", "+15550001111", twiml_url="https://bridge.example/twiml", http=http)
        v.send("+919876500002", "plan words", "pa")
        form = parse_qs(http.calls[0]["body"].decode())
        assert "Twiml" not in form
        assert form["Url"][0].startswith("https://bridge.example/twiml?")
        assert "language=pa" in form["Url"][0] and "to=%2B919876500002" in form["Url"][0]

    def test_provider_error_raises_so_the_queue_retries(self):
        http = FakeHttp((400, {"message": "The 'To' number is not a valid phone number"}))
        v = pv.TwilioVoice("ACxxx", "tok", "+15550001111", http=http)
        with pytest.raises(pv.ProviderError, match="not a valid phone number"):
            v.send("+91000", "x", "en")

    def test_punjabi_maps_to_an_indian_voice_locale(self):
        assert pv.say_language("pa") == "pa-IN"
        assert pv.say_language("hi") == "hi-IN"
        assert pv.say_language("en") == "en-IN"
        assert pv.say_language("xx") == "en-IN"


class TestPhotonBridgeSMS:
    def test_posts_json_with_bearer_token_and_returns_the_message_id(self):
        http = FakeHttp((200, {"id": "msg_1", "status": "queued"}))
        s = pv.PhotonBridgeSMS("https://localhost:8787/send", "secret", http=http)
        assert s.send("+919876500002", "plan words", "pa") == "photon msg_1 (queued)"
        c = http.calls[0]
        assert c["url"] == "https://localhost:8787/send"
        assert c["headers"]["Authorization"] == "Bearer secret"
        assert c["headers"]["Content-Type"] == "application/json"
        assert json.loads(c["body"]) == {"to": "+919876500002", "text": "plan words", "language": "pa", "channel": "sms"}

    def test_non_2xx_raises(self):
        s = pv.PhotonBridgeSMS("https://localhost:8787/send", "secret", http=FakeHttp((503, {"error": "line busy"})))
        with pytest.raises(pv.ProviderError, match="line busy"):
            s.send("+919876500002", "x", "pa")


class TestFromEnv:
    def test_missing_configuration_gives_unverified_stubs_naming_the_variables(self):
        env = {}
        p = pv.from_env(env)
        assert isinstance(p["grok_voice"], q.UnverifiedProvider) and isinstance(p["photon_sms"], q.UnverifiedProvider)
        with pytest.raises(NotImplementedError, match="TWILIO_ACCOUNT_SID"):
            p["grok_voice"].send("+91", "x", "pa")
        with pytest.raises(NotImplementedError, match="PHOTON_BRIDGE_URL"):
            p["photon_sms"].send("+91", "x", "pa")

    def test_full_configuration_builds_real_providers(self):
        env = {"TWILIO_ACCOUNT_SID": "ACxxx", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_FROM": "+15550001111",
               "VOICE_TWIML_URL": "https://bridge.example/twiml",
               "PHOTON_BRIDGE_URL": "https://localhost:8787/send", "PHOTON_BRIDGE_TOKEN": "secret"}
        p = pv.from_env(env, http=FakeHttp())
        assert isinstance(p["grok_voice"], pv.TwilioVoice) and p["grok_voice"].twiml_url == "https://bridge.example/twiml"
        assert isinstance(p["photon_sms"], pv.PhotonBridgeSMS)


class TestQueueWithProviders:
    def test_real_run_records_provider_results_per_channel(self, tmp_path):
        from test_queue import ROSTER_CSV, plan   # same fixture data as the queue tests
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "act_now")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": []}))
        (tmp_path / "roster.csv").write_text(ROSTER_CSV)
        voice_http = FakeHttp((201, {"sid": "CA1", "status": "queued"}))
        sms_http = FakeHttp((200, {"id": "m1", "status": "sent"}))
        prov = {"grok_voice": pv.TwilioVoice("AC", "t", "+1555", http=voice_http),
                "photon_sms": pv.PhotonBridgeSMS("https://localhost/send", "s", http=sms_http)}
        doc = q.run(tmp_path, roster_path=tmp_path / "roster.csv", dry_run=False, today=TODAY, providers=prov, only=["R002"])
        rows = {r["channel"]: r for r in doc["deliveries"] if r["roster_id"] == "R002"}
        assert rows["grok_voice"]["status"] == "sent" and "CA1" in rows["grok_voice"]["detail"]
        assert rows["photon_sms"]["status"] == "sent" and "m1" in rows["photon_sms"]["detail"]
        assert len(voice_http.calls) == 1 and len(sms_http.calls) == 1
