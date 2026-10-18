"""Self-serve sign-up: an address (or "lat, lon") plus a phone or an email becomes a roster row tied to the field it
falls in (or the nearest one within a few km), with the person's own morning time and timezone; STOP removes it."""
import csv
import json
from pathlib import Path

import pytest

import signup
from tools import load_roster

ROOT = Path(__file__).resolve().parents[2]
FIELDS = json.loads((ROOT / "data" / "mock" / "fields.geojson").read_text())


def centroid(feat):
    ring = feat["geometry"]["coordinates"][0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


F0 = FIELDS["features"][0]
FID0 = F0["properties"]["field_id"]
C0 = centroid(F0)


class TestLocate:
    def test_lat_lon_text_is_parsed(self):
        assert signup.parse_latlon("42.03, -93.47") == (-93.47, 42.03)
        assert signup.parse_latlon("1234 Main St, Ames") is None
        assert signup.parse_latlon("") is None

    def test_geocode_uses_nominatim_with_a_user_agent(self):
        calls = []

        def http(method, url, headers, body, timeout):
            calls.append((method, url, headers))
            return 200, json.dumps([{"lon": "-93.47", "lat": "42.03", "display_name": "Story County, Iowa"}]).encode()
        out = signup.geocode("Story County, Iowa", http=http)
        assert out == (-93.47, 42.03, "Story County, Iowa")
        assert calls[0][0] == "GET" and "nominatim.openstreetmap.org/search" in calls[0][1] and "Story%20County" in calls[0][1]
        assert "User-Agent" in calls[0][2]

    def test_geocode_failure_is_none(self):
        assert signup.geocode("nowhere", http=lambda *a: (200, b"[]")) is None
        assert signup.geocode("nowhere", http=lambda *a: (503, b"busy")) is None

        def boom(*a):
            raise OSError("offline")
        assert signup.geocode("nowhere", http=boom) is None

    def test_point_inside_a_field_matches_it(self):
        m = signup.match_field(C0[0], C0[1], FIELDS)
        assert m["field_id"] == FID0 and m["inside"] is True and m["distance_km"] == 0.0

    def test_point_near_the_landscape_matches_the_nearest_field(self):
        m = signup.match_field(C0[0] + 0.004, C0[1], FIELDS)            # ~330 m east
        assert m is not None and m["distance_km"] < 1.0

    def test_point_far_away_matches_nothing(self):
        assert signup.match_field(-122.4, 37.8, FIELDS) is None          # San Francisco


class TestRegister:
    def test_first_signup_creates_the_roster_with_the_optional_columns(self, tmp_path):
        roster = tmp_path / "roster.csv"
        out = signup.register(FIELDS, roster, name="Ana Lopez", phone="+1 515 555 0101", lon=C0[0], lat=C0[1],
                              language="en", send_time="06:30", timezone="America/Chicago")
        assert out["created"] is True and out["row"]["roster_id"] == "R001" and out["row"]["farm_id"] == FID0
        rows = load_roster(roster)
        assert rows[0]["phone"] == "+15155550101" and rows[0]["send_time"] == "06:30" and rows[0]["timezone"] == "America/Chicago"
        assert rows[0]["role"] == "farmer" and rows[0]["email"] == ""
        with open(roster, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        assert header[:6] == ["roster_id", "farm_id", "name", "phone", "language", "role"]
        assert {"email", "send_time", "timezone", "address", "lon", "lat", "created"} <= set(header)
        assert "Overfield" in out["welcome"] and "06:30" in out["welcome"] and FID0 in out["welcome"]

    def test_email_only_signup_is_allowed(self, tmp_path):
        out = signup.register(FIELDS, tmp_path / "r.csv", name="Ben", email="Ben@Example.com", lon=C0[0], lat=C0[1])
        assert out["row"]["phone"] == "" and out["row"]["email"] == "ben@example.com"
        assert out["row"]["send_time"] == signup.DEFAULT_SEND_TIME and out["row"]["timezone"] == signup.DEFAULT_TIMEZONE
        assert "email" in out["welcome"].lower()

    def test_same_phone_updates_instead_of_duplicating(self, tmp_path):
        roster = tmp_path / "r.csv"
        signup.register(FIELDS, roster, name="Ana", phone="+15155550101", lon=C0[0], lat=C0[1])
        out = signup.register(FIELDS, roster, name="Ana L.", phone="515-555-0101", lon=C0[0], lat=C0[1], send_time="05:45")
        assert out["created"] is False and out["row"]["roster_id"] == "R001"
        rows = load_roster(roster)
        assert len(rows) == 1 and rows[0]["name"] == "Ana L." and rows[0]["send_time"] == "05:45"

    def test_appends_after_existing_rows_and_keeps_them(self, tmp_path):
        roster = tmp_path / "r.csv"
        roster.write_text("roster_id,farm_id,name,phone,language,role\n"
                          "R001,,Officer Kaur,+919876500001,pa,officer\n"
                          "R007,F0002,Gurpreet,+919876500002,pa,farmer\n")
        out = signup.register(FIELDS, roster, name="Ana", phone="+15155550101", lon=C0[0], lat=C0[1])
        assert out["row"]["roster_id"] == "R008"
        rows = load_roster(roster)
        assert [r["roster_id"] for r in rows] == ["R001", "R007", "R008"] and rows[0]["role"] == "officer"
        assert rows[0]["send_time"] == "" and rows[2]["send_time"] == signup.DEFAULT_SEND_TIME

    def test_address_is_geocoded(self, tmp_path):
        def http(m, u, h, b, t):
            return 200, json.dumps([{"lon": str(C0[0]), "lat": str(C0[1]), "display_name": "near Ames"}]).encode()
        out = signup.register(FIELDS, tmp_path / "r.csv", name="Cy", phone="+15155550102", address="123 County Rd, Ames, IA", http=http)
        assert out["row"]["farm_id"] == FID0 and out["row"]["address"] == "123 County Rd, Ames, IA"

    @pytest.mark.parametrize("kw,msg", [
        (dict(name="", phone="+15155550101"), "name"),
        (dict(name="X"), "phone or an email"),
        (dict(name="X", phone="+15155550101", send_time="25:00"), "time"),
        (dict(name="X", phone="+15155550101", timezone="Mars/Olympus"), "timezone"),
        (dict(name="X", phone="+15155550101", language="xx"), "language"),
        (dict(name="X", email="not-an-email"), "email"),
    ])
    def test_bad_input_is_refused_with_a_plain_reason(self, tmp_path, kw, msg):
        with pytest.raises(ValueError) as e:
            signup.register(FIELDS, tmp_path / "r.csv", lon=C0[0], lat=C0[1], **kw)
        assert msg in str(e.value)

    def test_uncovered_address_is_refused_and_nothing_is_written(self, tmp_path):
        with pytest.raises(ValueError) as e:
            signup.register(FIELDS, tmp_path / "r.csv", name="X", phone="+15155550101", lon=-122.4, lat=37.8)
        assert "cover" in str(e.value)
        assert not (tmp_path / "r.csv").exists()

    def test_address_that_cannot_be_found_is_refused(self, tmp_path):
        with pytest.raises(ValueError) as e:
            signup.register(FIELDS, tmp_path / "r.csv", name="X", phone="+15155550101", address="??", http=lambda *a: (200, b"[]"))
        assert "find" in str(e.value)


class TestStop:
    def test_stop_removes_the_row_by_phone_or_email(self, tmp_path):
        roster = tmp_path / "r.csv"
        signup.register(FIELDS, roster, name="Ana", phone="+15155550101", lon=C0[0], lat=C0[1])
        signup.register(FIELDS, roster, name="Ben", email="ben@example.com", lon=C0[0], lat=C0[1])
        assert signup.unsubscribe(roster, "515 555 0101") is True
        assert signup.unsubscribe(roster, "BEN@example.com") is True
        assert signup.unsubscribe(roster, "+10000000000") is False
        assert load_roster(roster) == []
