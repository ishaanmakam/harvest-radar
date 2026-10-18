"""The accounts routes through the real inbound server: a sign-up over a socket, the preflight the browser sends,
a PATCH, the roster that merges the CSV with the sign-ups, and the /v1 API with a key."""
import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import inbound_server as srv

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def server(tmp_path, monkeypatch):
    data = tmp_path / "data" / "out"
    data.mkdir(parents=True)
    for name in ("plans.json", "route.json", "analytics.json", "fields.geojson", "run.json"):
        src = ROOT / "data" / "mock" / name
        if src.exists():
            shutil.copy(src, data / name)
    (tmp_path / "web" / "public" / "data").mkdir(parents=True)
    (tmp_path / "web" / "public" / "data" / "sources.json").write_text('{"generated": "2026-10-18", "sources": []}', encoding="utf-8")
    roster = tmp_path / "roster.csv"
    roster.write_text("roster_id,farm_id,name,phone,language,role\nR001,,Officer Ola,+15155550199,en,officer\n", encoding="utf-8")
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.sqlite"))       # restored after the test
    app = srv.make_handler(data, roster, accounts_db=tmp_path / "accounts.sqlite")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def req(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json", "Origin": "https://example.github.io"})
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw and "json" in resp.headers.get("Content-Type", "") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers), (json.loads(raw) if raw else None)


def test_signup_patch_roster_and_api_over_a_socket(server):
    status, headers, _ = req(server, "OPTIONS", "/auth/login")
    assert status == 204 and headers["Access-Control-Allow-Origin"] == "*"

    status, _, doc = req(server, "POST", "/auth/signup", {"email": "ana@example.com", "password": "corn-and-soy-2026", "name": "Ana"})
    assert status == 200 and doc["token"].startswith("ovs_")
    token = doc["token"]
    status, _, doc = req(server, "PATCH", "/me", {"phone": "+15155550101", "farm_id": "F0001", "sms_consent": True}, token=token)
    assert status == 200 and doc["account"]["farm_id"] == "F0001"

    status, _, doc = req(server, "GET", "/health")
    assert status == 200 and doc["roster_rows"] == 2                      # the CSV officer plus the sign-up, merged

    status, _, doc = req(server, "GET", "/auth/me", token="ovs_" + "0" * 64)
    assert status == 401

    req(server, "POST", "/terms/accept", {}, token=token)
    status, _, doc = req(server, "POST", "/keys", {"name": "demo"}, token=token)
    assert status == 200
    status, _, doc = req(server, "GET", "/v1/plans", token=doc["key"])
    assert status == 200 and doc["data"]["plans"]
    status, _, doc = req(server, "GET", "/v1/health")
    assert status == 200 and doc["ok"] is True

    status, _, doc = req(server, "POST", "/photon/inbound", {"from": "+15155550101", "text": "STOP"})
    assert status == 200 and doc["reply"].startswith("Stopped")
    status, _, doc = req(server, "GET", "/health")
    assert doc["roster_rows"] == 1                                         # paused: out of the roster at once
