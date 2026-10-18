"""Photon 2.0 direct REST provider (users -> spaces -> messages), webhook parsing and signatures, and the
inbound webhook endpoint. Fake HTTP only."""
import base64
import hashlib
import hmac
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import inbound_server as srv
import photon_poll
import providers as pv
import queue_morning as q
import tools
from test_inbound import ROSTER_CSV, plan

PROJECT = "pho_prj_00000000000000000000000001"
LINE_USER = "pho_usr_00000000000000000000000001"
PEER_USER = "pho_usr_00000000000000000000000002"
SPACE = "pho_spc_00000000000000000000000001"


class FakePhoton:
    """Answers the Photon endpoints a send needs; records every request."""

    def __init__(self, fail_message=None):
        self.calls, self.fail_message = [], fail_message

    def __call__(self, method, url, headers, body, timeout):
        doc = json.loads(body) if body else {}
        self.calls.append({"method": method, "url": url, "headers": headers, "body": doc})
        if url.endswith("/users"):
            uid = LINE_USER if doc["handle"] == "+15550001111" else PEER_USER
            return 201, json.dumps({"id": uid, "handle": doc["handle"], "platform": doc["platform"], "kind": "agent" if uid == LINE_USER else "human"}).encode()
        if url.endswith("/spaces"):
            return 201, json.dumps({"id": SPACE, "platform": "sms", "participantIds": doc["participantIds"]}).encode()
        if url.endswith("/messages"):
            if self.fail_message:
                return 422, json.dumps({"code": "VALIDATION_FAILED", "title": self.fail_message}).encode()
            return 202, json.dumps({"id": "pho_msg_00000000000000000000000001", "status": "accepted"}).encode()
        return 404, b"{}"


class TestPhotonSMS:
    def test_send_creates_user_space_then_message_with_plain_text(self):
        http = FakePhoton()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http)
        assert p.send("+919876500002", "Harvest before 2026-10-19.", "pa") == "photon pho_msg_00000000000000000000000001 (accepted)"
        urls = [c["url"] for c in http.calls]
        assert urls == [f"https://api.photon.codes/v1/projects/{PROJECT}/users",      # our line
                        f"https://api.photon.codes/v1/projects/{PROJECT}/users",      # the farmer
                        f"https://api.photon.codes/v1/projects/{PROJECT}/spaces",
                        f"https://api.photon.codes/v1/projects/{PROJECT}/messages"]
        assert all(c["headers"]["Authorization"] == "Bearer pho_sk_test" for c in http.calls)
        assert http.calls[0]["body"] == {"handle": "+15550001111", "platform": "sms"}
        assert http.calls[1]["body"] == {"handle": "+919876500002", "platform": "sms"}
        space = http.calls[2]
        assert space["body"] == {"participantIds": [LINE_USER, PEER_USER], "platform": "sms"}
        assert space["headers"]["Idempotency-Key"]
        msg = http.calls[3]
        assert msg["body"] == {"spaceId": SPACE, "senderId": LINE_USER, "content": {"type": "text", "format": "plain", "text": "Harvest before 2026-10-19."}}
        assert msg["headers"]["Idempotency-Key"]

    def test_user_and_space_lookups_are_cached_per_recipient(self):
        http = FakePhoton()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http)
        p.send("+919876500002", "one", "pa")
        p.send("+919876500002", "two", "pa")
        assert [c["url"].rsplit("/", 1)[1] for c in http.calls] == ["users", "users", "spaces", "messages", "messages"]
        keys = [c["headers"]["Idempotency-Key"] for c in http.calls if c["url"].endswith("/messages")]
        assert keys[0] != keys[1]                                           # two sends, two keys

    def test_api_problem_raises_provider_error_with_the_problem_title(self):
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=FakePhoton(fail_message="Campaign eligibility required"))
        with pytest.raises(pv.ProviderError, match="Campaign eligibility required"):
            p.send("+919876500002", "x", "pa")

    def test_reply_into_an_existing_space_skips_lookups(self):
        http = FakePhoton()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http)
        p.send_to_space(SPACE, "reply text")
        assert [c["url"].rsplit("/", 1)[1] for c in http.calls] == ["users", "messages"]   # only our own line id was needed


class TestFromEnvPhoton:
    def test_api_key_and_project_build_the_direct_provider(self):
        env = {"PHOTON_API_KEY": "pho_sk_x", "PHOTON_PROJECT_ID": PROJECT, "PHOTON_FROM": "+15550001111"}
        p = pv.from_env(env, http=FakePhoton())["photon_sms"]
        assert isinstance(p, pv.PhotonSMS) and p.project_id == PROJECT

    def test_bridge_is_still_accepted_when_only_the_bridge_is_configured(self):
        env = {"PHOTON_BRIDGE_URL": "https://localhost:8787/send", "PHOTON_BRIDGE_TOKEN": "s"}
        assert isinstance(pv.from_env(env, http=FakePhoton())["photon_sms"], pv.PhotonBridgeSMS)

    def test_missing_configuration_names_the_direct_variables(self):
        p = pv.from_env({}, http=FakePhoton())["photon_sms"]
        with pytest.raises(NotImplementedError, match="PHOTON_API_KEY"):
            p.send("+91", "x", "pa")


def beta_event(handle, text, kind="human", space=SPACE, mid="pho_msg_00000000000000000000000009"):
    return {"id": "pho_evt_1", "type": "message.received", "apiVersion": "2026-07-01", "source": "sms.photon",
            "timestamp": "2026-10-17T12:00:00Z",
            "data": {"projectId": PROJECT, "object": {"id": mid, "platform": "sms", "status": "received",
                                                       "sentBy": {"id": PEER_USER, "handle": handle, "kind": kind, "platform": "sms"},
                                                       "space": {"id": space, "platform": "sms"},
                                                       "content": {"type": "text", "format": "plain", "text": text}}}}


class TestWebhookParsing:
    def test_beta_envelope(self):
        got = pv.parse_photon_webhook(beta_event("+919876500001", "route please"))
        assert got == {"handle": "+919876500001", "text": "route please", "space_id": SPACE, "message_id": "pho_msg_00000000000000000000000009", "from_agent": False}

    def test_legacy_spectrum_envelope(self):
        doc = {"event": "messages", "space": {"id": "spc_1"}, "message": {"id": "m1", "sender": {"handle": "+919876500001"},
                                                                        "content": {"type": "text", "text": "hi"}}}
        got = pv.parse_photon_webhook(doc)
        assert got["handle"] == "+919876500001" and got["text"] == "hi" and got["space_id"] == "spc_1"

    def test_non_text_and_agent_messages_are_flagged(self):
        ev = beta_event("+15550001111", "echo", kind="agent")
        assert pv.parse_photon_webhook(ev)["from_agent"] is True
        ev["data"]["object"]["content"] = {"type": "reaction", "reaction": "❤️", "action": "add"}
        assert pv.parse_photon_webhook(ev) is None

    def test_signature_accepts_hex_or_base64_hmac_of_the_body(self):
        body = b'{"a":1}'
        mac = hmac.new(b"secret", body, hashlib.sha256).digest()
        assert pv.photon_signature_ok(body, "v0=" + mac.hex(), "secret")
        assert pv.photon_signature_ok(body, base64.b64encode(mac).decode(), "secret")
        assert not pv.photon_signature_ok(body, "v0=deadbeef", "secret")
        assert not pv.photon_signature_ok(body, "", "secret")


class Spy:
    def __init__(self):
        self.replies = []

    def send_to_space(self, space_id, text):
        self.replies.append((space_id, text))
        return "photon ok"


@pytest.fixture
def service(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    spy = Spy()
    app = srv.make_handler(tmp_path, tmp_path / "roster.csv", photon=spy, photon_webhook_secret="whsec")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", spy
    httpd.shutdown()


def post(url, doc, secret="whsec"):
    body = json.dumps(doc).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Spectrum-Signature"] = "v0=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class TestPhotonWebhookEndpoint:
    def test_officer_text_is_answered_in_the_same_space(self, service):
        base, spy = service
        status, doc = post(base + "/photon/webhook", beta_event("+919876500001", "route"))
        assert status == 200 and doc["handled"] is True
        assert spy.replies and spy.replies[0][0] == SPACE
        assert q.pack("pa")["route_head"].split("{")[0] in spy.replies[0][1] and "F0002" in spy.replies[0][1]

    def test_bad_signature_is_rejected(self, service):
        base, spy = service
        status, _ = post(base + "/photon/webhook", beta_event("+919876500001", "route"), secret="wrong")
        assert status == 401 and spy.replies == []

    def test_our_own_messages_and_duplicates_are_ignored(self, service):
        base, spy = service
        status, doc = post(base + "/photon/webhook", beta_event("+15550001111", "echo", kind="agent"))
        assert status == 200 and doc["handled"] is False
        post(base + "/photon/webhook", beta_event("+919876500001", "route", mid="pho_msg_dup"))
        post(base + "/photon/webhook", beta_event("+919876500001", "route", mid="pho_msg_dup"))
        assert len(spy.replies) == 1

    def test_unknown_texter_gets_only_the_no_farm_message(self, service):
        base, spy = service
        post(base + "/photon/webhook", beta_event("+15550000000", "plan for F0001"))
        assert spy.replies[-1][1] == tools.UNKNOWN_CALLER_MESSAGE


def _msg(handle, text, space, mid, created, kind="human"):
    return {"id": mid, "platform": "imessage", "status": "received", "createdAt": created,
            "sentBy": {"id": PEER_USER, "handle": handle, "kind": kind, "platform": "imessage"},
            "receivedBy": {"id": LINE_USER, "handle": "+15550001111", "kind": "agent", "platform": "imessage"},
            "space": {"id": space, "kind": "direct", "platform": "imessage"},
            "content": {"type": "text", "format": "plain", "text": text}}


class Recording:
    """Records every call and serves a list of received-message pages, then accepts sends."""

    def __init__(self, pages=None, sticky=False):
        self.pages = [list(p) for p in (pages or [])]
        self.sticky = sticky
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        doc = json.loads(body) if body else {}
        self.calls.append({"method": method, "url": url, "headers": headers, "body": doc})
        if method == "GET" and "/messages" in url:
            if self.sticky:
                items = list(self.pages[0]) if self.pages else []
                return 200, json.dumps({"items": items}).encode()
            items = self.pages.pop(0) if self.pages else []
            payload = {"items": items}
            if self.pages:
                payload["nextPageToken"] = "next.page"
            return 200, json.dumps(payload).encode()
        if method == "POST" and url.endswith("/messages"):
            return 202, json.dumps({"id": "pho_msg_out", "status": "accepted"}).encode()
        return 404, json.dumps({"title": "not used"}).encode()


def _paths(http):
    return [(c["method"], c["url"].split("/projects/")[-1]) for c in http.calls]


class TestImessageSend:
    def test_cached_space_is_reused_and_users_and_spaces_are_not_created(self):
        http = Recording()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage")
        p.remember_space("+91 98765 00002", SPACE)
        p.remember_line_sender(LINE_USER)
        assert p.send("+919876500002", "Harvest before 2026-10-19.", "pa") == "photon pho_msg_out (accepted)"
        assert _paths(http) == [("POST", f"{PROJECT}/messages")]
        assert http.calls[0]["body"] == {"spaceId": SPACE, "senderId": LINE_USER,
                                          "content": {"type": "text", "format": "plain", "text": "Harvest before 2026-10-19."}}
        assert http.calls[0]["headers"]["Idempotency-Key"]

    def test_latest_received_direct_chat_is_found_without_creating_users_or_spaces(self):
        other = _msg("+919876500099", "elsewhere", "pho_spc_other", "m0", "2026-10-18T13:00:00.000Z")
        newer = _msg("+919876500002", "new", SPACE, "m2", "2026-10-18T12:00:00.000Z")
        older = _msg("+919876500002", "old", "pho_spc_old", "m1", "2026-10-18T11:00:00.000Z")
        http = Recording(pages=[[other], [newer, older]])
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage")
        p.send("+919876500002", "hi", "en")
        gets = [c for c in http.calls if c["method"] == "GET"]
        posts = [c for c in http.calls if c["method"] == "POST"]
        assert len(gets) == 2 and "pageToken=next.page" in gets[1]["url"]
        assert "platform=imessage" in gets[0]["url"] and "direction=received" in gets[0]["url"]
        assert "order=desc" in gets[0]["url"] and "orderBy=created_at" in gets[0]["url"]
        assert len(posts) == 1 and posts[0]["url"].endswith("/messages")
        assert posts[0]["body"]["spaceId"] == SPACE and posts[0]["body"]["senderId"] == LINE_USER
        assert not any(c["url"].rstrip("/").endswith("/users") or c["url"].rstrip("/").endswith("/spaces") for c in http.calls)

    def test_no_prior_chat_raises_a_clear_error(self):
        http = Recording(pages=[[_msg("+919876500099", "hi", "pho_spc_other", "m0", "2026-10-18T12:00:00.000Z")]])
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage")
        with pytest.raises(pv.ProviderError, match="no iMessage chat yet: the person must text the Overfield line first"):
            p.send("+919876500002", "hi", "en")
        assert all(c["method"] == "GET" for c in http.calls)

    def test_rate_limit_waits_for_retry_after(self):
        waited = []

        class Once:
            def __init__(self):
                self.n = 0

            def __call__(self, method, url, headers, body, timeout):
                self.n += 1
                if self.n == 1:
                    return 429, json.dumps({"title": "Too Many Requests", "code": "RATE_LIMITED"}).encode(), {"Retry-After": "2"}
                return 200, json.dumps({"items": []}).encode()

        http = Once()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage", sleep=waited.append)
        assert p.list_received() == []
        assert waited == [2.0] and http.n == 2


class TestPhotonPlatformEnv:
    def test_unset_platform_stays_sms_and_imessage_is_opt_in(self):
        env = {"PHOTON_API_KEY": "pho_sk_x", "PHOTON_PROJECT_ID": PROJECT, "PHOTON_FROM": "+15550001111"}
        assert pv.photon_from_env(dict(env), http=FakePhoton()).platform == "sms"
        env["PHOTON_PLATFORM"] = "imessage"
        assert pv.photon_from_env(dict(env), http=FakePhoton()).platform == "imessage"
        env["PHOTON_PLATFORM"] = "telegram"
        with pytest.raises(pv.ProviderError, match="PHOTON_PLATFORM"):
            pv.photon_from_env(env, http=FakePhoton())


def _season(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002", "watch")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-17", "stops": [
        {"farm_id": "F0002", "action": "call", "why": "Field cut between 2026-10-11 and 2026-10-16"}]}))
    (tmp_path / "roster.csv").write_text(ROSTER_CSV)
    return tmp_path, tools.load_roster(tmp_path / "roster.csv")


class TestPhotonPoll:
    def _client(self, items):
        http = Recording(pages=[items], sticky=True)   # every poll sees the same page; the cursor skips ones already answered
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage")
        return p, http

    def test_farmer_officer_and_unknown_are_scoped_like_the_webhook(self, tmp_path):
        data, roster = _season(tmp_path)
        farmer = _msg("+919876500002", "tell me about F0002", "pho_spc_farmer", "mf", "2026-10-18T12:00:01.000Z")
        officer = _msg("+919876500001", "plan for F0001", "pho_spc_officer", "mo", "2026-10-18T12:00:02.000Z")
        unknown = _msg("+15550000000", "plan for F0001", "pho_spc_unknown", "mu", "2026-10-18T12:00:03.000Z")
        photon, http = self._client([farmer, officer, unknown])
        cursor = {}
        photon_poll.poll_once(photon, data, roster, cursor)
        sent = {c["body"]["spaceId"]: c["body"]["content"]["text"] for c in http.calls if c["method"] == "POST"}
        assert sent["pho_spc_farmer"] == "I can only tell you about your own farm."
        assert "F0001" in sent["pho_spc_officer"] and "own farm" not in sent["pho_spc_officer"].lower()
        assert sent["pho_spc_unknown"] == tools.UNKNOWN_CALLER_MESSAGE
        assert not any(c["url"].rstrip("/").endswith("/users") or c["url"].rstrip("/").endswith("/spaces") for c in http.calls)
        assert cursor["created_since"] == "2026-10-18T12:00:03.000Z"
        photon_poll.poll_once(photon, data, roster, cursor)
        assert sum(1 for c in http.calls if c["method"] == "POST") == 3
        assert any("createdSince=" in c["url"] for c in http.calls if c["method"] == "GET")

    def test_dry_run_prints_the_reply_and_sends_nothing(self, tmp_path):
        data, roster = _season(tmp_path)
        farmer = _msg("+919876500002", "tell me about F0002", "pho_spc_farmer", "mf", "2026-10-18T12:00:01.000Z")
        photon, http = self._client([farmer])
        lines = []
        photon_poll.poll_once(photon, data, roster, {}, dry_run=True, log=lines.append)
        assert lines == ["dry-run +919876500002: I can only tell you about your own farm."]
        assert not any(c["method"] == "POST" for c in http.calls)

    def test_received_by_is_parsed_and_remembered(self):
        ev = beta_event("+919876500002", "hi")
        ev["data"]["object"]["receivedBy"] = {"id": LINE_USER, "handle": "+15550001111"}
        parsed = pv.parse_photon_webhook(ev)
        assert parsed["received_by_id"] == LINE_USER
        http = Recording()
        p = pv.PhotonSMS("pho_sk_test", PROJECT, "+15550001111", http=http, platform="imessage")
        photon_poll.handle_inbound(parsed, ".", [], photon=p, dry_run=True, log=lambda *_: None)
        assert p._imessage_spaces[tools.normalize_phone("+919876500002")] == SPACE
        assert p._line_sender_id == LINE_USER
        assert http.calls == []
