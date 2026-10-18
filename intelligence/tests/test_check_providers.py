"""check_providers.py: a read-only preflight for the live loop. It never sends anything; it reports what is
configured, what the providers answer, and what is still missing."""
import json

import check_providers as cp


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        for key, (status, payload) in self.routes.items():
            if key in url:
                return status, json.dumps(payload).encode()
        return 404, b"{}"


class TestReport:
    def test_everything_missing(self, tmp_path):
        rep = cp.report({}, roster_path=None, http=FakeHttp({}))
        assert rep["twilio"]["configured"] is False and "TWILIO_ACCOUNT_SID" in rep["twilio"]["missing"]
        assert rep["photon"]["configured"] is False and "PHOTON_API_KEY" in rep["photon"]["missing"]
        assert rep["xai"]["configured"] is False
        assert rep["roster"]["ok"] is False
        assert rep["ready_for_real_send"] is False

    def test_twilio_numbers_and_photon_line_are_listed_without_sending(self, tmp_path):
        (tmp_path / "roster.csv").write_text("roster_id,farm_id,name,phone,language,role\nR1,,Officer,+919876500001,pa,officer\n")
        env = {"TWILIO_ACCOUNT_SID": "ACx", "TWILIO_AUTH_TOKEN": "t", "TWILIO_FROM": "+15550001111",
               "PHOTON_API_KEY": "pho_sk_x", "PHOTON_PROJECT_ID": "pho_prj_1", "PHOTON_FROM": "+15550002222",
               "XAI_API_KEY": "xai", "PUBLIC_URL": "https://abc.ngrok.app"}
        http = FakeHttp({
            "IncomingPhoneNumbers.json": (200, {"incoming_phone_numbers": [{"phone_number": "+15550001111", "voice_url": "https://abc.ngrok.app/twilio/stream", "capabilities": {"voice": True, "sms": True}}]}),
            "/users": (200, {"id": "pho_usr_1", "handle": "+15550002222", "kind": "agent", "platform": "sms"}),
            "/health": (200, {"ok": True, "plans": 2051}),
        })
        rep = cp.report(env, roster_path=tmp_path / "roster.csv", http=http)
        assert rep["twilio"]["configured"] and rep["twilio"]["numbers"][0]["phone_number"] == "+15550001111"
        assert rep["twilio"]["from_is_owned"] is True and "/twilio/stream" in rep["twilio"]["numbers"][0]["voice_url"]
        assert rep["photon"]["configured"] and rep["photon"]["line_user_id"] == "pho_usr_1" and rep["photon"]["line_kind"] == "agent"
        assert rep["xai"]["configured"] is True
        assert rep["roster"]["ok"] and rep["roster"]["rows"] == 1
        assert rep["public_url"]["health"] == {"ok": True, "plans": 2051}
        assert rep["ready_for_real_send"] is True
        assert all(m == "GET" or url.endswith("/users") for m, url in http.calls)       # read-only (users is create-or-get)

    def test_provider_errors_are_reported_not_raised(self, tmp_path):
        env = {"TWILIO_ACCOUNT_SID": "ACx", "TWILIO_AUTH_TOKEN": "bad", "TWILIO_FROM": "+15550001111"}
        http = FakeHttp({"IncomingPhoneNumbers.json": (401, {"message": "Authenticate"})})
        rep = cp.report(env, roster_path=None, http=http)
        assert rep["twilio"]["configured"] is True and "Authenticate" in rep["twilio"]["error"]
        assert rep["ready_for_real_send"] is False

    def test_render_is_a_checklist(self, tmp_path):
        text = cp.render(cp.report({}, roster_path=None, http=FakeHttp({})))
        assert "Twilio" in text and "Photon" in text and "roster" in text.lower()
