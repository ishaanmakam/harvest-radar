"""accounts_api.py: the HTTP routes of Overfield's own accounts, served by inbound_server. Sign-up and sign-in with
email and password, bearer sessions, the profile, terms and developer keys, the admin pages, and the key-gated /v1
developer API over the published plan files. Pure dispatch over (method, path, headers, body); no sockets here."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

import accounts as ac
import accounts_api as api

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("plans.json", "route.json", "analytics.json", "fields.geojson", "run.json"):
        src = ROOT / "data" / "mock" / name
        if src.exists():
            shutil.copy(src, data / name)
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"generated": "2026-10-18", "sources": [{"name": "Sentinel-2", "kind": "imagery"}]}), encoding="utf-8")
    db = ac.Accounts(tmp_path / "accounts.sqlite", now=lambda: T0)
    return api.Context(accounts=db, data_dir=data, sources_path=sources, now=lambda: T0)


def call(ctx, method, path, body=None, token=None, ip="203.0.113.5", headers=None):
    h = {"Content-Type": "application/json", "User-Agent": "pytest"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode("utf-8") if body is not None else b"")
    status, out_headers, out = api.dispatch(method, path, h, raw, ctx, client_ip=ip)
    text = out.decode("utf-8") if isinstance(out, bytes) else out
    doc = json.loads(text) if out_headers.get("Content-Type", "").startswith("application/json") else text
    return status, doc, out_headers


def signed_up(ctx, email="ana@example.com", password="corn-and-soy-2026", name="Ana Lopez"):
    status, doc, _ = call(ctx, "POST", "/auth/signup", {"email": email, "password": password, "name": name})
    assert status == 200, doc
    return doc["token"], doc["account"]


class TestAuth:
    def test_preflight_and_cors_on_every_answer(self, ctx):
        status, _, headers = call(ctx, "OPTIONS", "/auth/login")
        assert status == 204 and headers["Access-Control-Allow-Origin"] == "*"
        assert "authorization" in headers["Access-Control-Allow-Headers"].lower() and "PATCH" in headers["Access-Control-Allow-Methods"]
        status, _, headers = call(ctx, "GET", "/auth/me")
        assert status == 401 and headers["Access-Control-Allow-Origin"] == "*"

    def test_signup_login_me_logout(self, ctx):
        token, account = signed_up(ctx)
        assert account["email"] == "ana@example.com" and account["roster_id"] == "R0001" and "password_hash" not in account
        status, doc, _ = call(ctx, "GET", "/auth/me", token=token)
        assert status == 200 and doc["account"]["id"] == account["id"]
        status, doc, _ = call(ctx, "POST", "/auth/login", {"email": "ANA@example.com", "password": "corn-and-soy-2026"})
        assert status == 200 and doc["token"].startswith("ovs_")
        status, doc, _ = call(ctx, "POST", "/auth/login", {"email": "ana@example.com", "password": "nope-nope-nope"})
        assert status == 401 and doc["error"] == "wrong email or password"
        status, _, _ = call(ctx, "POST", "/auth/logout", token=token)
        assert status == 200
        assert call(ctx, "GET", "/auth/me", token=token)[0] == 401

    def test_signup_errors_are_sentences(self, ctx):
        signed_up(ctx)
        status, doc, _ = call(ctx, "POST", "/auth/signup", {"email": "ana@example.com", "password": "corn-and-soy-2026"})
        assert status == 409 and "already has an account" in doc["error"]
        status, doc, _ = call(ctx, "POST", "/auth/signup", {"email": "bo@example.com", "password": "short"})
        assert status == 400 and "at least 8" in doc["error"]
        status, doc, _ = call(ctx, "POST", "/auth/signup", b"not json", headers={"Content-Type": "application/json"})
        assert status == 400

    def test_too_many_signups_from_one_address(self, ctx):
        for i in range(api.SIGNUPS_PER_IP):
            assert call(ctx, "POST", "/auth/signup", {"email": f"p{i}@example.com", "password": "corn-and-soy-2026"})[0] == 200
        status, doc, _ = call(ctx, "POST", "/auth/signup", {"email": "late@example.com", "password": "corn-and-soy-2026"})
        assert status == 429 and "too many" in doc["error"]


class TestProfile:
    def test_patch_me_matches_the_farm_from_the_point(self, ctx):
        token, _ = signed_up(ctx)
        fields = json.loads((ROOT / "data" / "mock" / "fields.geojson").read_text(encoding="utf-8"))
        import signup
        lon, lat = signup.polygon_centroid(fields["features"][0]["geometry"]) if hasattr(signup, "polygon_centroid") else api.centroid(fields["features"][0]["geometry"])
        status, doc, _ = call(ctx, "PATCH", "/me", {"name": "Ana L.", "address": "near the field", "lon": lon, "lat": lat,
                                                     "phone": "+15155550101", "send_time": "06:45", "sms_consent": True}, token=token)
        assert status == 200, doc
        acc = doc["account"]
        assert acc["farm_id"] == fields["features"][0]["properties"]["field_id"] and acc["send_time"] == "06:45"
        assert acc["sms_consent_at"] and doc["field"]["distance_km"] == 0
        status, doc, _ = call(ctx, "PATCH", "/me", {"lon": 0.0, "lat": 0.0}, token=token)
        assert status == 400 and "does not cover" in doc["error"]
        status, doc, _ = call(ctx, "PATCH", "/me", {"role": "admin"}, token=token)
        assert status == 400 and "admin" in doc["error"]

    def test_my_deliveries_and_delete(self, ctx):
        token, acc = signed_up(ctx)
        ctx.accounts.push_deliveries({"date": "2026-10-18", "deliveries": [{"date": "2026-10-18", "roster_id": acc["roster_id"], "role": "farmer",
                                                                            "channel": "sms", "status": "pending", "detail": "dry run"}]}, dry_run=True)
        status, doc, _ = call(ctx, "GET", "/me/deliveries", token=token)
        assert status == 200 and doc["deliveries"][0]["channel"] == "sms" and doc["deliveries"][0]["dry_run"] is True
        status, _, _ = call(ctx, "POST", "/me/delete", token=token)
        assert status == 200 and call(ctx, "GET", "/auth/me", token=token)[0] == 401


class TestKeys:
    def test_terms_then_key_shown_once_then_listed_and_revoked(self, ctx):
        token, _ = signed_up(ctx)
        status, doc, _ = call(ctx, "GET", "/terms", token=token)
        assert status == 200 and doc["accepted"] is None and doc["version"] == ac.TERMS_VERSION
        status, doc, _ = call(ctx, "POST", "/keys", {"name": "first"}, token=token)
        assert status == 400 and "accept the developer terms" in doc["error"]
        status, doc, _ = call(ctx, "POST", "/terms/accept", {}, token=token)
        assert status == 200 and doc["accepted_at"] == "2026-10-18T09:00Z"
        status, doc, _ = call(ctx, "POST", "/keys", {"name": "first"}, token=token)
        assert status == 200 and doc["key"].startswith("ovf_")
        key = doc["key"]
        status, doc, _ = call(ctx, "GET", "/keys", token=token)
        assert status == 200 and doc["keys"][0]["key_prefix"] == key[:12] and doc["keys"][0]["usage_7d"] == 0 and "key_hash" not in doc["keys"][0]
        status, doc, _ = call(ctx, "DELETE", f"/keys/{doc['keys'][0]['id']}", token=token)
        assert status == 200 and doc["revoked"] is True
        assert call(ctx, "GET", "/v1/plans", headers={"Authorization": f"Bearer {key}"})[0] == 401


class TestDeveloperApi:
    def keyed(self, ctx):
        token, _ = signed_up(ctx)
        call(ctx, "POST", "/terms/accept", {}, token=token)
        return call(ctx, "POST", "/keys", {"name": "k"}, token=token)[1]["key"]

    def test_health_needs_no_key_and_lists_endpoints(self, ctx):
        status, doc, _ = call(ctx, "GET", "/v1/health")
        assert status == 200 and doc["ok"] is True and "/v1/plans" in doc["endpoints"]

    def test_plans_farm_route_landscape_sources_with_envelope(self, ctx):
        key = self.keyed(ctx)
        h = {"Authorization": f"Bearer {key}"}
        status, doc, _ = call(ctx, "GET", "/v1/plans", headers=h)
        assert status == 200 and set(doc) >= {"served_at", "as_of", "attribution", "licence", "data"} and doc["data"]["plans"]
        fid = doc["data"]["plans"][0]["farm_id"]
        status, one, _ = call(ctx, "GET", f"/v1/farms/{fid}/plan?key={key}")
        assert status == 200 and one["data"]["farm_id"] == fid
        assert call(ctx, "GET", "/v1/farms/NOPE/plan", headers=h)[0] == 404
        assert call(ctx, "GET", "/v1/route", headers=h)[1]["data"]["stops"] is not None
        land = call(ctx, "GET", "/v1/landscape", headers=h)[1]["data"]
        assert "landscape" in land
        assert call(ctx, "GET", "/v1/sources", headers=h)[1]["data"]["sources"][0]["name"] == "Sentinel-2"
        assert call(ctx, "GET", "/v1/nothing", headers=h)[0] == 404
        usage = ctx.accounts.usage_last_days(1)
        assert sum(u["total"] for u in usage.values()) == 7      # every call with a live key is logged, 404s included

    def test_missing_or_bad_key_and_daily_limit(self, ctx):
        assert call(ctx, "GET", "/v1/plans")[0] == 401
        assert call(ctx, "GET", "/v1/plans", headers={"Authorization": "Bearer ovf_" + "0" * 40})[0] == 401
        key = self.keyed(ctx)
        kid = ctx.accounts.verify_api_key(key)["key_id"]
        ctx.accounts.set_daily_limit(kid, 2)
        h = {"Authorization": f"Bearer {key}"}
        assert call(ctx, "GET", "/v1/plans", headers=h)[0] == 200 and call(ctx, "GET", "/v1/plans", headers=h)[0] == 200
        status, doc, _ = call(ctx, "GET", "/v1/plans", headers=h)
        assert status == 429 and "daily limit" in doc["error"]


class TestAdmin:
    def test_roles_gate_the_admin_routes(self, ctx):
        farmer, _ = signed_up(ctx)
        off_token, off = signed_up(ctx, email="off@example.com")
        adm_token, adm = signed_up(ctx, email="adm@example.com")
        ctx.accounts.set_role_by_email("off@example.com", "officer")
        ctx.accounts.set_role_by_email("adm@example.com", "admin")
        assert call(ctx, "GET", "/admin/overview", token=farmer)[0] == 403
        status, doc, _ = call(ctx, "GET", "/admin/overview", token=off_token)
        assert status == 200 and doc["people"] == 3 and doc["officers"] == 1
        status, doc, _ = call(ctx, "GET", "/admin/accounts", token=off_token)
        assert status == 200 and len(doc["accounts"]) == 3 and "password_hash" not in doc["accounts"][0]
        target = doc["accounts"][0]["id"]
        assert call(ctx, "PATCH", f"/admin/accounts/{target}", {"paused": True}, token=off_token)[0] == 403
        status, doc, _ = call(ctx, "PATCH", f"/admin/accounts/{target}", {"paused": True, "role": "officer"}, token=adm_token)
        assert status == 200 and doc["account"]["paused"] is True and doc["account"]["role"] == "officer"
        assert call(ctx, "PATCH", f"/admin/accounts/{target}", {"role": "king"}, token=adm_token)[0] == 400

    def test_deliveries_keys_and_export(self, ctx):
        adm_token, adm = signed_up(ctx, email="adm@example.com")
        ctx.accounts.set_role_by_email("adm@example.com", "admin")
        ctx.accounts.update_profile(adm["id"], {"phone": "+15155550199"})
        ctx.accounts.push_deliveries({"date": "2026-10-18", "deliveries": [{"date": "2026-10-18", "roster_id": adm["roster_id"], "role": "admin",
                                                                            "channel": "sms", "status": "sent", "detail": "ok"}]}, dry_run=False)
        status, doc, _ = call(ctx, "GET", "/admin/deliveries", token=adm_token)
        assert status == 200 and doc["runs"][0]["run_date"] == "2026-10-18" and doc["rows"][0]["status"] == "sent"
        status, doc, _ = call(ctx, "GET", "/admin/deliveries?run_date=2026-01-01", token=adm_token)
        assert status == 200 and doc["rows"] == []
        call(ctx, "POST", "/terms/accept", {}, token=adm_token)
        call(ctx, "POST", "/keys", {"name": "k"}, token=adm_token)
        status, doc, _ = call(ctx, "GET", "/admin/keys", token=adm_token)
        assert status == 200 and doc["keys"][0]["email"] == "adm@example.com" and doc["keys"][0]["usage_7d"] == 0
        status, text, headers = call(ctx, "GET", "/admin/export.csv", token=adm_token)
        assert status == 200 and headers["Content-Type"].startswith("text/csv") and text.splitlines()[0].startswith("roster_id,farm_id")


class TestDemoText:
    """POST /demo/text: the morning text for one farm, sent now to one phone. The demo of the 08:00 send."""

    class _Photon:
        def __init__(self):
            self.sent = []

        def send(self, phone, text, language):
            self.sent.append((phone, text, language))
            return {"id": "msg_1", "status": "queued"}

    def test_needs_a_session(self, ctx):
        status, doc, _ = call(ctx, "POST", "/demo/text", {"phone": "+15155550100", "farm": "F0001"})
        assert status == 401

    def test_post_only(self, ctx):
        token, _ = signed_up(ctx)
        status, _, _ = call(ctx, "GET", "/demo/text", token=token)
        assert status == 405

    def test_no_phone_is_a_400(self, ctx):
        token, _ = signed_up(ctx)
        status, doc, _ = call(ctx, "POST", "/demo/text", {"farm": "F0001", "dry_run": True}, token=token)
        assert status == 400 and "phone" in doc["error"]

    def test_dry_run_renders_the_plan_and_sends_nothing(self, ctx, monkeypatch):
        import providers
        photon = self._Photon()
        monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: photon)
        token, _ = signed_up(ctx)
        status, doc, _ = call(ctx, "POST", "/demo/text", {"phone": "+15155550100", "farm": "F0001", "dry_run": True}, token=token)
        assert status == 200, doc
        assert doc["sent"] is False and doc["dry_run"] is True and doc["farm"] == "F0001" and doc["configured"] is True
        assert doc["preview"].startswith("Overfield morning plan for farm F0001")
        assert photon.sent == []
        status, doc, _ = call(ctx, "GET", "/me/deliveries", token=token)
        assert status == 200 and doc["deliveries"] == []

    def test_live_send_goes_through_photon_and_is_logged(self, ctx, monkeypatch):
        import providers
        photon = self._Photon()
        monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: photon)
        token, _ = signed_up(ctx)
        call(ctx, "PATCH", "/me", {"phone": "+15155550100", "farm_id": "F0002"}, token=token)
        status, doc, _ = call(ctx, "POST", "/demo/text", {}, token=token)
        assert status == 200, doc
        assert doc["sent"] is True and doc["to"] == "+15155550100" and doc["farm"] == "F0002" and doc["channel"] == "photon_sms"
        assert len(photon.sent) == 1 and photon.sent[0][0] == "+15155550100" and photon.sent[0][2] == "en"
        assert photon.sent[0][1].startswith("Overfield morning plan for farm F0002")
        status, doc, _ = call(ctx, "GET", "/me/deliveries", token=token)
        assert status == 200 and len(doc["deliveries"]) == 1
        row = doc["deliveries"][0]
        assert row["farm_id"] == "F0002" and row["channel"] == "photon_sms" and row["status"] == "sent" and not row["dry_run"]

    def test_unconfigured_photon_is_a_503(self, ctx, monkeypatch):
        import providers
        monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: None)
        token, _ = signed_up(ctx)
        status, doc, _ = call(ctx, "POST", "/demo/text", {"phone": "+15155550100", "farm": "F0001"}, token=token)
        assert status == 503 and "Photon" in doc["error"]
