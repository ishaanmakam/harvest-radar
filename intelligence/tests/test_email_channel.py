"""An email channel beside the call and the text: SmtpEmail (stdlib smtplib over STARTTLS), configured from SMTP_* in
.env, and the queue sends each roster row on the channels it has (phone: voice + sms; email: email)."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import providers
import queue_morning as q
from test_queue import plan

ROOT = Path(__file__).resolve().parents[2]


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.log = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.log.append("quit")

    def starttls(self):
        self.log.append("starttls")

    def login(self, u, p):
        self.log.append(("login", u))

    def send_message(self, msg):
        self.log.append(("send", msg["To"], msg["From"], msg["Subject"], msg.get_content()))


class TestSmtpEmail:
    def test_send_logs_in_over_starttls_and_sends_the_plan(self):
        FakeSMTP.instances.clear()
        p = providers.SmtpEmail("smtp.example.com", 587, "bot@example.com", "secret", "Overfield <bot@example.com>", smtp=FakeSMTP)
        result = p.send("ana@example.com", "Overfield morning plan for farm F0001, from the satellite pass of 2026-10-16.", "en")
        s = FakeSMTP.instances[0]
        assert (s.host, s.port) == ("smtp.example.com", 587)
        assert s.log[0] == "starttls" and s.log[1] == ("login", "bot@example.com")
        kind, to, frm, subject, body = s.log[2]
        assert to == "ana@example.com" and "Overfield" in subject and body.startswith("Overfield morning plan")
        assert s.log[-1] == "quit" and "ana@example.com" in result

    def test_failure_is_a_provider_error(self):
        class Boom(FakeSMTP):
            def login(self, u, p):
                raise OSError("auth failed")
        p = providers.SmtpEmail("smtp.example.com", 587, "u", "p", "bot@example.com", smtp=Boom)
        with pytest.raises(providers.ProviderError):
            p.send("ana@example.com", "hi", "en")

    def test_from_env_builds_the_email_channel_only_when_configured(self):
        out = providers.from_env({}, http=lambda *a: (200, b"{}"))
        assert isinstance(out["email"], q.UnverifiedProvider)
        env = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_FROM": "bot@example.com"}
        out = providers.from_env(env, http=lambda *a: (200, b"{}"))
        assert isinstance(out["email"], providers.SmtpEmail) and out["email"].port == 587


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, handle, text, language):
        self.sent.append(handle)
        return "ok"


class TestQueueChannels:
    def test_rows_go_out_on_the_channels_they_have(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002"), plan("F0003")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
        (tmp_path / "roster.csv").write_text(
            "roster_id,farm_id,name,phone,language,role,email\n"
            "R001,F0001,Ana,+15155550101,en,farmer,\n"
            "R002,F0002,Ben,,en,farmer,ben@example.com\n"
            "R003,F0003,Cy,+15155550103,en,farmer,cy@example.com\n"
            "R004,F0001,Dee,,en,farmer,\n")
        chans = {"grok_voice": Recorder(), "photon_sms": Recorder(), "email": Recorder()}
        q.run(tmp_path, tmp_path / "roster.csv", dry_run=False, providers=chans)
        assert chans["grok_voice"].sent == ["+15155550101", "+15155550103"]
        assert chans["photon_sms"].sent == ["+15155550101", "+15155550103"]
        assert chans["email"].sent == ["ben@example.com", "cy@example.com"]
        doc = json.loads((tmp_path / "deliveries.json").read_text())
        by = {(d["roster_id"], d["channel"]): d for d in doc["deliveries"]}
        assert by[("R002", "email")]["status"] == "sent" and ("R002", "photon_sms") not in by
        assert by[("R004", "grok_voice")]["status"] == "failed" and "no phone number or email" in by[("R004", "grok_voice")]["detail"]
        schema = json.loads((ROOT / "contracts" / "deliveries.schema.json").read_text())
        assert not list(Draft202012Validator(schema).iter_errors(doc))

    def test_dry_run_still_covers_an_email_only_row(self, tmp_path):
        (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0002")]}))
        (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
        (tmp_path / "roster.csv").write_text("roster_id,farm_id,name,phone,language,role,email\nR002,F0002,Ben,,en,farmer,ben@example.com\n")
        q.run(tmp_path, tmp_path / "roster.csv", dry_run=True)
        doc = json.loads((tmp_path / "deliveries.json").read_text())
        assert [(d["channel"], d["status"]) for d in doc["deliveries"]] == [("email", "pending")]   # a dry run leaves rows pending
