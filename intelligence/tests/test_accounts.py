"""accounts.py: Overfield's own accounts in SQLite. Email and password sign-in (PBKDF2), bearer sessions, the roster
shape the queue reads, developer keys with hashed secrets and a daily limit, terms acceptances, the morning delivery
log, and the admin counts. Standard library only; every test runs on a temporary database."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import accounts as ac
import tools

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return ac.Accounts(tmp_path / "accounts.sqlite", now=lambda: T0)


def signup(db, email="ana@example.com", password="corn-and-soy-2026", name="Ana Lopez", **kw):
    return db.create_account(email=email, password=password, name=name, **kw)


class TestPasswords:
    def test_hash_is_salted_pbkdf2_and_verifies(self):
        h1, h2 = ac.hash_password("hunter22"), ac.hash_password("hunter22")
        assert h1 != h2 and h1.startswith("pbkdf2_sha256$") and int(h1.split("$")[1]) >= 200_000
        assert ac.verify_password("hunter22", h1) and not ac.verify_password("hunter23", h1)
        assert not ac.verify_password("hunter22", "garbage")

    def test_short_passwords_are_refused_with_a_sentence(self, db):
        with pytest.raises(ValueError, match="at least 8"):
            signup(db, password="short")


class TestAccounts:
    def test_create_login_and_session(self, db):
        a = signup(db)
        assert a["email"] == "ana@example.com" and a["role"] == "farmer" and a["roster_id"] == "R0001" and "password_hash" not in a
        token = db.login("Ana@Example.com", "corn-and-soy-2026")
        assert token.startswith("ovs_") and len(token) > 40
        me = db.account_for_token(token)
        assert me["id"] == a["id"] and me["email"] == "ana@example.com"
        db.logout(token)
        assert db.account_for_token(token) is None

    def test_wrong_password_and_unknown_email_look_the_same(self, db):
        signup(db)
        with pytest.raises(ac.AuthError, match="wrong email or password"):
            db.login("ana@example.com", "nope-nope-nope")
        with pytest.raises(ac.AuthError, match="wrong email or password"):
            db.login("nobody@example.com", "corn-and-soy-2026")

    def test_duplicate_email_is_refused(self, db):
        signup(db)
        with pytest.raises(ValueError, match="already has an account"):
            signup(db)

    def test_ten_failed_logins_lock_the_email_for_fifteen_minutes(self, tmp_path):
        clock = {"t": T0}
        db = ac.Accounts(tmp_path / "a.sqlite", now=lambda: clock["t"])
        signup(db)
        for _ in range(10):
            with pytest.raises(ac.AuthError):
                db.login("ana@example.com", "wrong-password-1")
        with pytest.raises(ac.AuthError, match="too many attempts"):
            db.login("ana@example.com", "corn-and-soy-2026")
        clock["t"] = T0 + timedelta(minutes=16)
        assert db.login("ana@example.com", "corn-and-soy-2026").startswith("ovs_")

    def test_sessions_expire(self, tmp_path):
        clock = {"t": T0}
        db = ac.Accounts(tmp_path / "a.sqlite", now=lambda: clock["t"])
        signup(db)
        token = db.login("ana@example.com", "corn-and-soy-2026")
        clock["t"] = T0 + timedelta(days=31)
        assert db.account_for_token(token) is None

    def test_update_profile_validates_like_signup(self, db):
        a = signup(db)
        row = db.update_profile(a["id"], {"name": "Ana L.", "phone": "+1 (515) 555-0101", "language": "pa", "send_time": "5:45",
                                          "timezone": "America/Chicago", "channels": ["sms", "email"], "sms_consent": True,
                                          "farm_id": "F0001", "lon": -93.47123, "lat": 42.03456, "address": "123 County Rd"})
        assert row["phone"] == "+15155550101" and row["send_time"] == "05:45" and row["language"] == "pa"
        assert row["channels"] == ["sms", "email"] and row["sms_consent_at"] == "2026-10-18T09:00Z" and row["farm_id"] == "F0001"
        row = db.update_profile(a["id"], {"sms_consent": False, "paused": True})
        assert row["sms_consent_at"] is None and row["paused"] is True
        with pytest.raises(ValueError, match="24-hour clock"):
            db.update_profile(a["id"], {"send_time": "half past six"})
        with pytest.raises(ValueError, match="timezone"):
            db.update_profile(a["id"], {"timezone": "Mars/Olympus"})
        with pytest.raises(ValueError, match="language"):
            db.update_profile(a["id"], {"language": "xx"})
        with pytest.raises(ValueError, match="role"):
            db.update_profile(a["id"], {"role": "admin"})                  # nobody promotes themselves

    def test_same_phone_on_two_accounts_is_refused(self, db):
        a = signup(db)
        b = signup(db, email="bo@example.com")
        db.update_profile(a["id"], {"phone": "+15155550101"})
        with pytest.raises(ValueError, match="already used"):
            db.update_profile(b["id"], {"phone": "+1 515 555 0101"})

    def test_admin_sets_role_and_pause(self, db):
        a = signup(db)
        row = db.admin_update(a["id"], {"role": "officer", "paused": True})
        assert row["role"] == "officer" and row["paused"] is True
        with pytest.raises(ValueError, match="role must be"):
            db.admin_update(a["id"], {"role": "king"})

    def test_delete_account_removes_everything(self, db):
        a = signup(db)
        token = db.login("ana@example.com", "corn-and-soy-2026")
        db.accept_terms(a["id"], "developer-v1", "developer", "ua")
        db.issue_api_key(a["id"], "test")
        db.delete_account(a["id"])
        assert db.account_for_token(token) is None and db.list_accounts() == [] and db.list_keys(a["id"]) == []

    def test_promote_by_email_for_the_command_line(self, db):
        signup(db)
        assert db.set_role_by_email("ana@example.com", "admin") is True
        assert db.list_accounts()[0]["role"] == "admin"
        assert db.set_role_by_email("nobody@example.com", "admin") is False


class TestRoster:
    def test_roster_rows_have_the_csv_shape_and_skip_who_cannot_be_sent_to(self, db):
        a = signup(db)
        db.update_profile(a["id"], {"farm_id": "F0001", "phone": "+15155550101", "language": "pa", "send_time": "06:30",
                                    "timezone": "America/Chicago", "lon": -93.47123, "lat": 42.03456, "address": "123 County Rd"})
        b = signup(db, email="bo@example.com")                                  # no farm yet: not in the roster
        c = signup(db, email="cy@example.com")
        db.update_profile(c["id"], {"farm_id": "F0002", "paused": True})       # paused: not in the roster
        o = signup(db, email="off@example.com", role="officer")                 # officer with email only: in the roster
        rows = db.roster_rows()
        assert [r["roster_id"] for r in rows] == ["R0001", "R0004"]
        first = rows[0]
        assert set(tools.ROSTER_COLUMNS) <= set(first) and first["farm_id"] == "F0001" and first["phone"] == "+15155550101"
        assert first["send_time"] == "06:30" and first["lon"] == "-93.47123" and first["created"] == "2026-10-18T09:00Z"
        assert rows[1]["role"] == "officer" and rows[1]["farm_id"] == "" and rows[1]["email"] == "off@example.com"
        assert tools.validate_rows(rows)
        db.set_role_by_email("off@example.com", "admin")
        rows = db.roster_rows()
        assert rows[1]["role"] == "officer" and tools.validate_rows(rows)      # an admin is an officer to the queue
        del b, o

    def test_pause_by_handle_for_stop_texts(self, db):
        a = signup(db)
        db.update_profile(a["id"], {"phone": "+15155550101", "farm_id": "F0001"})
        assert db.pause_by_handle("+15155550101") is True and db.roster_rows() == []
        assert db.pause_by_handle("+10000000000") is False


class TestTermsAndKeys:
    def test_key_needs_the_terms_then_is_shown_once_and_verifies_by_hash(self, db):
        a = signup(db)
        with pytest.raises(ValueError, match="accept the developer terms"):
            db.issue_api_key(a["id"], "first")
        when = db.accept_terms(a["id"], "developer-v1", "developer", "Mozilla/5.0")
        assert when == "2026-10-18T09:00Z" and db.terms_accepted(a["id"], "developer-v1")
        key = db.issue_api_key(a["id"], "first")
        assert key.startswith("ovf_") and len(key) == 44
        listed = db.list_keys(a["id"])
        assert listed[0]["key_prefix"] == key[:12] and "key_hash" not in listed[0] and listed[0]["daily_limit"] == 1000
        v = db.verify_api_key(key)
        assert v["account_id"] == a["id"] and v["used_today"] == 0
        db.log_api_use(v["key_id"], "/v1/plans", 200)
        assert db.verify_api_key(key)["used_today"] == 1 and db.list_keys(a["id"])[0]["last_used_at"]
        assert db.verify_api_key("ovf_" + "0" * 40) is None

    def test_at_most_five_live_keys_and_revoke(self, db):
        a = signup(db)
        db.accept_terms(a["id"], "developer-v1", "developer", "")
        keys = [db.issue_api_key(a["id"], f"k{i}") for i in range(5)]
        with pytest.raises(ValueError, match="at most 5"):
            db.issue_api_key(a["id"], "k6")
        kid = db.list_keys(a["id"])[0]["id"]
        assert db.revoke_api_key(a["id"], kid) is True
        assert db.verify_api_key(keys[-1]) is None or db.verify_api_key(keys[-1])["key_id"] != kid
        assert db.revoke_api_key(a["id"], kid) is False                          # already revoked

    def test_usage_in_the_last_seven_days_per_key(self, tmp_path):
        clock = {"t": T0}
        db = ac.Accounts(tmp_path / "a.sqlite", now=lambda: clock["t"])
        a = signup(db)
        db.accept_terms(a["id"], "developer-v1", "developer", "")
        key = db.issue_api_key(a["id"], "k")
        kid = db.verify_api_key(key)["key_id"]
        for d in (0, 1, 1, 9):
            clock["t"] = T0 - timedelta(days=d)
            db.log_api_use(kid, "/v1/plans", 200)
        clock["t"] = T0
        usage = db.usage_last_days(7)
        assert usage[kid]["total"] == 3 and usage[kid]["by_day"]["2026-10-17"] == 2


class TestDeliveriesAndAdmin:
    def test_push_deliveries_keeps_only_the_runs_rows_and_lists_them(self, db):
        a = signup(db)
        db.update_profile(a["id"], {"farm_id": "F0001", "phone": "+15155550101"})
        doc = {"date": "2026-10-18", "deliveries": [
            {"date": "2026-10-18", "roster_id": "R0001", "farm_id": "F0001", "role": "farmer", "channel": "sms", "status": "sent", "detail": "ok", "sent_at": "2026-10-18T11:30:00Z"},
            {"date": "2026-10-17", "roster_id": "R0001", "farm_id": "F0001", "role": "farmer", "channel": "sms", "status": "sent", "detail": "old"}]}
        assert db.push_deliveries(doc, dry_run=False) == 1
        mine = db.deliveries_for(a["id"])
        assert len(mine) == 1 and mine[0]["status"] == "sent" and mine[0]["dry_run"] is False
        runs = db.delivery_runs()
        assert runs == [{"run_date": "2026-10-18", "rows": 1, "sent": 1, "pending": 0, "failed": 0}]
        assert db.deliveries_on("2026-10-18")[0]["roster_id"] == "R0001"

    def test_push_without_a_document_date_uses_the_run_date_or_the_latest(self, db):
        rows = [{"date": "2026-10-17", "roster_id": "R0001", "role": "officer", "channel": "sms", "status": "sent"},
                {"date": "2026-10-18", "roster_id": "R0001", "role": "officer", "channel": "sms", "status": "pending"}]
        assert db.push_deliveries({"deliveries": rows}, dry_run=True) == 1 and db.delivery_runs()[0]["run_date"] == "2026-10-18"
        assert db.push_deliveries({"deliveries": rows}, dry_run=True, run_date="2026-10-17") == 1
        assert [r["run_date"] for r in db.delivery_runs()] == ["2026-10-18", "2026-10-17"]

    def test_overview_counts(self, tmp_path):
        clock = {"t": T0}
        db = ac.Accounts(tmp_path / "a.sqlite", now=lambda: clock["t"])
        a = signup(db)
        db.update_profile(a["id"], {"sms_consent": True, "farm_id": "F0001", "phone": "+15155550101"})
        clock["t"] = T0 - timedelta(days=9)
        signup(db, email="old@example.com", role="officer")
        clock["t"] = T0
        o = db.overview()
        assert o["people"] == 2 and o["farmers"] == 1 and o["officers"] == 1 and o["consented"] == 1 and o["paused"] == 0
        assert o["signups_7d"] == 1 and o["signups_per_day"] == [{"day": "2026-10-09", "signups": 1}, {"day": "2026-10-18", "signups": 1}]

    def test_export_csv_has_the_roster_path_columns(self, db):
        a = signup(db)
        db.update_profile(a["id"], {"farm_id": "F0001", "phone": "+15155550101", "address": "Road, with a comma"})
        text = db.export_csv()
        head, row = text.splitlines()[:2]
        assert head == "roster_id,farm_id,name,phone,language,role,email,send_time,timezone,address,lon,lat,created"
        assert row.startswith("R0001,F0001,Ana Lopez,+15155550101,en,farmer,ana@example.com,06:30,America/Chicago,\"Road, with a comma\"")


class TestLoadRoster:
    def test_load_roster_reads_the_accounts_db_and_merges_the_csv(self, db, tmp_path, monkeypatch):
        a = signup(db)
        db.update_profile(a["id"], {"farm_id": "F0001", "phone": "+15155550101"})
        monkeypatch.setenv("ACCOUNTS_DB", str(db.path))
        monkeypatch.delenv("ROSTER_PATH", raising=False)
        rows = tools.load_roster()
        assert [r["roster_id"] for r in rows] == ["R0001"] and tools.roster_source() == "accounts"
        csv_path = tmp_path / "roster.csv"
        csv_path.write_text("roster_id,farm_id,name,phone,language,role\nR001,,Officer Ola,+15155550199,en,officer\n", encoding="utf-8")
        monkeypatch.setenv("ROSTER_PATH", str(csv_path))
        rows = tools.load_roster()
        assert [r["roster_id"] for r in rows] == ["R001", "R0001"] and tools.roster_source() == "csv+accounts"
        with pytest.raises(ValueError, match="already used"):
            csv_path.write_text("roster_id,farm_id,name,phone,language,role\nR001,,Officer Ola,+15155550101,en,officer\n", encoding="utf-8")
            tools.load_roster()

    def test_push_if_configured(self, db, monkeypatch):
        doc = {"date": "2026-10-18", "deliveries": [{"date": "2026-10-18", "roster_id": "R0001", "role": "officer", "channel": "sms", "status": "pending"}]}
        monkeypatch.delenv("ACCOUNTS_DB", raising=False)
        assert ac.push_if_configured(doc, dry_run=True) is None
        monkeypatch.setenv("ACCOUNTS_DB", str(db.path))
        assert ac.push_if_configured(doc, dry_run=True) == 1
        assert json.loads(json.dumps(ac.Accounts(db.path).delivery_runs()))[0]["rows"] == 1


class TestStopTexts:
    def test_stop_pauses_the_account_without_a_csv(self, db, monkeypatch):
        import inbound
        a = signup(db)
        db.update_profile(a["id"], {"phone": "+15155550101", "farm_id": "F0001"})
        monkeypatch.setenv("ACCOUNTS_DB", str(db.path))
        assert inbound.handle_stop(None, "tel:+15155550101", "stop").startswith("Stopped")
        assert db.get_account(a["id"])["paused"] is True
        assert "nothing to stop" in inbound.handle_stop(None, "+10000000000", "STOP")
        assert inbound.handle_stop(None, "+15155550101", "hello") is None
