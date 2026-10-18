"""The inbound server offers sign-up: GET /signup is a small page anyone can open on a phone, POST /signup registers
(address or lat/lon, phone or email, language, morning time, timezone) and the new person can call or text at once
because the server re-reads the roster when it changes. STOP by text removes the row."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import inbound_server as srv
from test_queue import plan
from tools import load_roster

ROOT = Path(__file__).resolve().parents[2]
FIELDS = json.loads((ROOT / "data" / "mock" / "fields.geojson").read_text())
F0 = FIELDS["features"][0]
FID0 = F0["properties"]["field_id"]
RING = F0["geometry"]["coordinates"][0]
LON, LAT = sum(p[0] for p in RING) / len(RING), sum(p[1] for p in RING) / len(RING)


@pytest.fixture
def server(tmp_path):
    (tmp_path / "fields.geojson").write_bytes((ROOT / "data" / "mock" / "fields.geojson").read_bytes())
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan(FID0)]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
    (tmp_path / "roster.csv").write_text("roster_id,farm_id,name,phone,language,role\nR001,,Officer Kaur,+919876500001,en,officer\n")
    app = srv.make_handler(tmp_path, tmp_path / "roster.csv", bridge_token=None, twilio_auth_token=None,
                           geocode=lambda address: (LON, LAT, "near Ames") if "Ames" in address else None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


def call(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers.get("Content-Type", "")


class TestSignupPage:
    def test_the_page_is_served_without_a_token(self, server):
        base, _ = server
        status, body, ctype = call(base + "/signup")
        assert status == 200 and "text/html" in ctype
        assert "Overfield" in body and 'name="address"' in body and 'name="phone"' in body and 'name="email"' in body
        assert 'name="send_time"' in body and 'name="timezone"' in body and 'name="language"' in body


class TestSignupPost:
    def test_register_with_an_address_then_call_in(self, server):
        base, data = server
        status, body, _ = call(base + "/signup", {"name": "Ana Lopez", "address": "123 County Rd, Ames, IA",
                                                  "phone": "+1 515 555 0101", "language": "en", "send_time": "06:15",
                                                  "timezone": "America/Chicago"})
        out = json.loads(body)
        assert status == 200 and out["ok"] is True and out["farm_id"] == FID0 and out["roster_id"] == "R002"
        assert "06:15" in out["message"] and "Overfield" in out["message"]
        rows = load_roster(data / "roster.csv")
        assert [r["roster_id"] for r in rows] == ["R001", "R002"] and rows[1]["farm_id"] == FID0
        # the new person is a known caller straight away (the server re-reads the roster when it changes)
        status, body, _ = call(base + "/tools/get_plan", {"handle": "+15155550101", "args": {}})
        assert status == 200 and json.loads(body)["farm_id"] == FID0

    def test_lat_lon_and_email_only(self, server):
        base, _ = server
        status, body, _ = call(base + "/signup", {"name": "Ben", "lat": LAT, "lon": LON, "email": "ben@example.com"})
        assert status == 200 and json.loads(body)["farm_id"] == FID0

    def test_form_encoded_bodies_work_too(self, server):
        base, _ = server
        form = f"name=Cy&address=42.0+County+Rd%2C+Ames&phone=%2B15155550102&language=en&send_time=07%3A00&timezone=America%2FChicago"
        req = urllib.request.Request(base + "/signup", data=form.encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["ok"] is True

    def test_bad_requests_are_refused_in_plain_words(self, server):
        base, _ = server
        status, body, _ = call(base + "/signup", {"name": "Dee", "address": "123 County Rd, Ames, IA"})
        assert status == 400 and "phone or an email" in json.loads(body)["error"]
        status, body, _ = call(base + "/signup", {"name": "Dee", "phone": "+15155550103", "address": "Somewhere else"})
        assert status == 400 and "find" in json.loads(body)["error"]
        status, body, _ = call(base + "/signup", {"name": "Dee", "phone": "+15155550103", "lat": 37.8, "lon": -122.4})
        assert status == 400 and "cover" in json.loads(body)["error"]


class TestStopByText:
    def test_stop_removes_the_row_and_confirms(self, server):
        base, data = server
        call(base + "/signup", {"name": "Ana", "lat": LAT, "lon": LON, "phone": "+15155550101"})
        status, body, _ = call(base + "/photon/inbound", {"from": "+15155550101", "text": "STOP"})
        assert status == 200 and "stop" in json.loads(body)["reply"].lower()
        assert [r["roster_id"] for r in load_roster(data / "roster.csv")] == ["R001"]
