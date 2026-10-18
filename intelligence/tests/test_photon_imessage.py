"""PhotonSMS on Photon's shared iMessage lines: the farmer's handle gets an assignment, the assigned line is the
sender of the Space and the message, and a farmer who has not texted the line yet gets a clear error."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers as pv  # noqa: E402

LINE = "+16283160001"
FARMER = "+15155550100"


class RoutedHttp:
    """A fake Photon: answers by (method, path) and records every call."""

    def __init__(self, inbound_done=True):
        self.calls = []
        self.inbound_done = inbound_done
        self.assignments = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("/v1/projects/pho_prj_1", 1)[1]
        doc = json.loads(body) if body else {}
        self.calls.append((method, path, doc, headers.get("Idempotency-Key")))
        if method == "GET" and path == "/platforms/imessage/assignments":
            return 200, json.dumps({"assignments": self.assignments}).encode()
        if method == "POST" and path == "/platforms/imessage/assignments":
            a = {"assignmentId": "pho_asg_1", "handle": doc["handle"], "assignedPhoneNumber": LINE, "platform": "imessage"}
            self.assignments.append(a)
            return 201, json.dumps(a).encode()
        if method == "POST" and path == "/users":
            kind = "agent" if doc["handle"] == LINE else "human"
            return 200, json.dumps({"id": f"pho_usr_{doc['handle'][-4:]}", "kind": kind, "platform": doc["platform"]}).encode()
        if method == "POST" and path == "/spaces":
            return 201, json.dumps({"id": "pho_spc_1", "participantIds": doc["participantIds"]}).encode()
        if method == "POST" and path == "/messages":
            if not self.inbound_done:
                return 403, json.dumps({"code": "SHARED_LINE_INBOUND_REQUIRED", "status": 403,
                                        "title": "Shared Line Inbound Required"}).encode()
            return 201, json.dumps({"id": "pho_msg_1", "status": "accepted"}).encode()
        return 404, json.dumps({"code": "ROUTE_NOT_FOUND", "title": "Route Not Found"}).encode()


def photon(http):
    return pv.PhotonSMS("pho_sk_x", "pho_prj_1", "+16283167976", http=http, platform="imessage")


def test_assignment_line_is_the_sender_and_the_space_creator():
    http = RoutedHttp()
    out = photon(http).send(FARMER, "Overfield morning plan", "en")
    assert out == "photon pho_msg_1 (accepted)"
    paths = [(m, p) for m, p, _, _ in http.calls]
    assert ("GET", "/platforms/imessage/assignments") in paths and ("POST", "/platforms/imessage/assignments") in paths
    asg = next(d for m, p, d, _ in http.calls if (m, p) == ("POST", "/platforms/imessage/assignments"))
    assert asg == {"handle": FARMER}
    space = next(d for m, p, d, _ in http.calls if (m, p) == ("POST", "/spaces"))
    assert space == {"participantIds": ["pho_usr_0001", "pho_usr_0100"], "platform": "imessage"}   # the line first
    msg = next(d for m, p, d, _ in http.calls if (m, p) == ("POST", "/messages"))
    assert msg["senderId"] == "pho_usr_0001" and msg["spaceId"] == "pho_spc_1" and msg["content"]["text"] == "Overfield morning plan"
    assert all(k for m, p, _, k in http.calls if p in ("/platforms/imessage/assignments", "/spaces") and m == "POST")


def test_an_existing_assignment_is_reused_without_a_new_one():
    http = RoutedHttp()
    http.assignments.append({"assignmentId": "pho_asg_0", "handle": FARMER, "assignedPhoneNumber": LINE, "platform": "imessage"})
    photon(http).send(FARMER, "plan", "en")
    assert ("POST", "/platforms/imessage/assignments") not in [(m, p) for m, p, _, _ in http.calls]


def test_a_farmer_who_has_not_texted_the_line_gets_a_clear_error():
    http = RoutedHttp(inbound_done=False)
    with pytest.raises(pv.ProviderError, match=r"has not texted the line yet.*\+16283160001"):
        photon(http).send(FARMER, "plan", "en")


def test_sms_platform_keeps_the_configured_line():
    http = RoutedHttp()
    p = pv.PhotonSMS("pho_sk_x", "pho_prj_1", "+16283167976", http=http, platform="sms")
    assert p.line_for(FARMER) == "+16283167976"
    assert ("GET", "/platforms/imessage/assignments") not in [(m, p) for m, p, _, _ in http.calls]


def test_env_picks_imessage_unless_told_otherwise():
    env = {"PHOTON_API_KEY": "pho_sk_x", "PHOTON_PROJECT_ID": "pho_prj_1", "PHOTON_FROM": "+16283167976"}
    assert pv.photon_from_env(env, http=RoutedHttp()).platform == "imessage"
    assert pv.photon_from_env({**env, "PHOTON_PLATFORM": "SMS"}, http=RoutedHttp()).platform == "sms"
