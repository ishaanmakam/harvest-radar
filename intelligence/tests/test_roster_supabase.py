"""roster_supabase.py: the roster read from Supabase profiles with the service role, and the morning log written back.
Pure functions over an injected http(method, url, headers, body, timeout); tests never touch the network."""
import json

import pytest

import roster_supabase as rs
import tools

URL = "https://abc.supabase.co"
KEY = "service-key"


def profile(**kw):
    base = {"roster_id": "R0001", "farm_id": "F0001", "name": "Ana Lopez", "phone": "+15155550101", "email": "ana@example.com",
            "language": "es", "role": "farmer", "send_time": "06:30:00", "timezone": "America/Chicago",
            "address": "123 County Rd, Ames, IA", "lon": -93.47123, "lat": 42.03456,
            "created_at": "2026-10-18T07:00:00.123456+00:00"}
    base.update(kw)
    return base


def ok(rows):
    def http(method, url, headers, body, timeout):
        return 200, json.dumps(rows).encode()
    return http


class TestFetch:
    def test_reads_profiles_with_the_service_role_and_maps_to_roster_rows(self):
        calls = []

        def http(method, url, headers, body, timeout):
            calls.append((method, url, headers, body))
            return 200, json.dumps([profile(), profile(roster_id="R0002", farm_id=None, role="officer", email=None,
                                                       send_time="05:45:00")]).encode()
        rows = rs.fetch_roster(URL, KEY, http=http)
        method, url, headers, body = calls[0]
        assert method == "GET" and url.startswith(URL + "/rest/v1/profiles?") and "paused=eq.false" in url and body is None
        assert headers["apikey"] == KEY and headers["Authorization"] == "Bearer " + KEY
        assert [r["roster_id"] for r in rows] == ["R0001", "R0002"]
        first = rows[0]
        assert set(tools.ROSTER_COLUMNS) <= set(first)
        assert first["send_time"] == "06:30" and first["lon"] == "-93.47123" and first["lat"] == "42.03456"
        assert first["created"] == "2026-10-18T07:00Z" and first["language"] == "es" and first["email"] == "ana@example.com"
        officer = rows[1]
        assert officer["farm_id"] == "" and officer["email"] == "" and officer["send_time"] == "05:45" and officer["role"] == "officer"

    def test_rows_that_cannot_receive_or_have_no_farm_yet_are_left_out(self):
        rows = rs.fetch_roster(URL, KEY, http=ok([
            profile(phone="(515) 555-0101", email=None),                       # national format: normalised
            profile(roster_id="R0002", phone=None, email=None),                # nothing to send to
            profile(roster_id="R0003", farm_id=None),                          # a farmer who has not finished the account page
        ]))
        assert [r["roster_id"] for r in rows] == ["R0001"] and rows[0]["phone"] == "5155550101"   # national format, as in the CSV

    def test_missing_optional_values_read_as_empty_strings(self):
        rows = rs.fetch_roster(URL, KEY, http=ok([profile(address=None, lon=None, lat=None, send_time=None, timezone=None,
                                                          created_at=None, language=None)]))
        r = rows[0]
        assert r["address"] == "" and r["lon"] == "" and r["lat"] == "" and r["send_time"] == "" and r["timezone"] == ""
        assert r["created"] == "" and r["language"] == "en"

    def test_errors_are_plain_sentences(self):
        def http(method, url, headers, body, timeout):
            return 401, b'{"message":"Invalid API key"}'
        with pytest.raises(RuntimeError, match=r"Supabase roster read failed \(401\)"):
            rs.fetch_roster(URL, KEY, http=http)


class TestPush:
    def test_posts_the_runs_rows_to_deliveries(self):
        calls = []

        def http(method, url, headers, body, timeout):
            calls.append((method, url, headers, json.loads(body)))
            return 201, b""
        doc = {"date": "2026-10-18", "mode": "dry run", "deliveries": [
            {"date": "2026-10-18", "roster_id": "R0001", "farm_id": "F0001", "role": "farmer", "channel": "sms", "status": "sent",
             "detail": "ok", "sent_at": "2026-10-18T11:30:00Z"},
            {"date": "2026-10-17", "roster_id": "R0001", "farm_id": "F0001", "role": "farmer", "channel": "sms", "status": "sent",
             "detail": "yesterday, already logged"}]}
        n = rs.push_deliveries(URL, KEY, doc, dry_run=True, http=http)
        assert n == 1
        method, url, headers, body = calls[0]
        assert method == "POST" and url == URL + "/rest/v1/deliveries" and headers["Prefer"] == "return=minimal"
        assert body == [{"run_date": "2026-10-18", "roster_id": "R0001", "farm_id": "F0001", "role": "farmer", "channel": "sms",
                         "status": "sent", "detail": "ok", "dry_run": True, "sent_at": "2026-10-18T11:30:00Z"}]

    def test_nothing_to_push_makes_no_call(self):
        def http(*a):
            raise AssertionError("no call expected")
        assert rs.push_deliveries(URL, KEY, {"date": "2026-10-18", "deliveries": []}, dry_run=True, http=http) == 0

    def test_push_failure_is_a_plain_sentence(self):
        def http(method, url, headers, body, timeout):
            return 500, b"boom"
        doc = {"date": "2026-10-18", "deliveries": [{"date": "2026-10-18", "roster_id": "R0001", "role": "officer", "channel": "sms",
                                                     "status": "pending"}]}
        with pytest.raises(RuntimeError, match=r"Supabase delivery log write failed \(500\)"):
            rs.push_deliveries(URL, KEY, doc, dry_run=False, http=http)

    def test_push_if_configured(self, monkeypatch):
        doc = {"date": "2026-10-18", "deliveries": [{"date": "2026-10-18", "roster_id": "R0001", "role": "officer", "channel": "sms",
                                                     "status": "pending"}]}
        assert rs.push_if_configured(doc, dry_run=True, env={}, http=ok([])) is None
        assert rs.push_if_configured(doc, dry_run=True, env={"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": KEY},
                                     http=lambda *a: (201, b"")) == 1


class TestLoadRoster:
    def test_load_roster_uses_supabase_when_configured_and_no_path(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", URL)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
        monkeypatch.delenv("ROSTER_PATH", raising=False)
        rows = tools.load_roster(http=ok([profile(phone="+1 (515) 555-0101")]))
        assert rows[0]["roster_id"] == "R0001" and rows[0]["phone"] == "+15155550101"
        assert tools.roster_source() == "supabase"

    def test_supabase_rows_go_through_the_same_validation(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", URL)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
        monkeypatch.delenv("ROSTER_PATH", raising=False)
        with pytest.raises(ValueError, match="already used by another row"):
            tools.load_roster(http=ok([profile(), profile(roster_id="R0002", email="b@example.com")]))

    def test_an_explicit_path_wins_over_supabase(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUPABASE_URL", URL)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
        csv_path = tmp_path / "roster.csv"
        csv_path.write_text("roster_id,farm_id,name,phone,language,role\nR1,F0001,Ana,+15155550101,en,farmer\n", encoding="utf-8")

        def http(*a):
            raise AssertionError("the CSV should be read, not Supabase")
        rows = tools.load_roster(csv_path, http=http)
        assert rows[0]["roster_id"] == "R1"
        assert tools.roster_source(csv_path) == "csv"

    def test_without_either_the_error_names_both_options(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("ROSTER_PATH", raising=False)
        assert tools.roster_source() == "none"
        with pytest.raises(FileNotFoundError, match="ROSTER_PATH.*SUPABASE_URL"):
            tools.load_roster()
