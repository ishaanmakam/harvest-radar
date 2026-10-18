"""scheduler.py: each roster row gets its morning plan at its own local time (send_time + timezone, defaults 06:30
America/Chicago); a tick sends only what is due, and the deliveries checkpoint keeps a second tick from repeating."""
import json
from datetime import datetime, timezone

import pytest

import scheduler
from test_queue import plan

UTC = timezone.utc


def row(rid, **kw):
    base = {"roster_id": rid, "farm_id": "F0001", "name": "A", "phone": "+15155550101", "language": "en", "role": "farmer",
            "email": "", "send_time": "06:30", "timezone": "America/Chicago"}
    base.update(kw)
    return base


class TestDue:
    def test_due_from_the_local_minute_until_the_late_window_closes(self):
        r = row("R001")
        assert scheduler.is_due(r, datetime(2026, 10, 17, 11, 30, tzinfo=UTC))        # 06:30 CDT (UTC-5)
        assert not scheduler.is_due(r, datetime(2026, 10, 17, 11, 29, tzinfo=UTC))
        assert scheduler.is_due(r, datetime(2026, 10, 17, 12, 59, tzinfo=UTC))        # 89 minutes late: still worth sending
        assert not scheduler.is_due(r, datetime(2026, 10, 17, 13, 0, tzinfo=UTC))     # 90 minutes late: wait for tomorrow

    def test_other_timezones_and_defaults(self):
        assert scheduler.is_due(row("R002", timezone="Asia/Kolkata", send_time="06:30"), datetime(2026, 10, 17, 1, 0, tzinfo=UTC))
        assert scheduler.is_due(row("R003", timezone="", send_time=""), datetime(2026, 10, 17, 11, 30, tzinfo=UTC))
        assert scheduler.local_date(row("R002", timezone="Asia/Kolkata"), datetime(2026, 10, 17, 20, 0, tzinfo=UTC)).isoformat() == "2026-10-18"

    def test_bad_timezone_or_time_falls_back_to_the_defaults(self):
        assert scheduler.schedule_of(row("R004", timezone="Mars/Olympus", send_time="99:99")) == (scheduler.DEFAULT_SEND_TIME, scheduler.DEFAULT_TIMEZONE)
        assert scheduler.schedule_of({"roster_id": "R005"}) == (scheduler.DEFAULT_SEND_TIME, scheduler.DEFAULT_TIMEZONE)


class FakeProvider:
    def __init__(self):
        self.sent = []

    def send(self, handle, text, language):
        self.sent.append((handle, language))
        return "ok"


@pytest.fixture
def data(tmp_path):
    (tmp_path / "plans.json").write_text(json.dumps({"as_of": "2026-10-16", "plans": [plan("F0001"), plan("F0002")]}))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
    (tmp_path / "roster.csv").write_text(
        "roster_id,farm_id,name,phone,language,role,email,send_time,timezone\n"
        "R001,F0001,Ana,+15155550101,en,farmer,,06:30,America/Chicago\n"
        "R002,F0002,Ben,+15155550102,en,farmer,,07:00,America/Chicago\n"
        "R003,,Officer,+15155550103,en,officer,,06:30,America/Chicago\n")
    return tmp_path


class TestTick:
    def test_tick_sends_only_the_due_rows_and_never_twice(self, data):
        providers = {"grok_voice": FakeProvider(), "photon_sms": FakeProvider(), "email": FakeProvider()}
        sent = scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 11, 31, tzinfo=UTC), dry_run=False, providers=providers)
        assert sent == ["R001", "R003"]
        assert sorted(h for h, _ in providers["photon_sms"].sent) == ["+15155550101", "+15155550103"]
        again = scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 11, 45, tzinfo=UTC), dry_run=False, providers=providers)
        assert again == []                                                        # checkpoint: already sent today
        later = scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 12, 1, tzinfo=UTC), dry_run=False, providers=providers)
        assert later == ["R002"]
        deliveries = json.loads((data / "deliveries.json").read_text())["deliveries"]
        got = {(d["roster_id"], d["channel"], d["status"]) for d in deliveries}
        assert {("R001", "photon_sms", "sent"), ("R002", "grok_voice", "sent"), ("R003", "grok_voice", "sent")} <= got
        assert all(d["date"] == "2026-10-17" for d in deliveries)

    def test_dry_run_tick_reports_what_was_due(self, data):
        assert scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 11, 31, tzinfo=UTC), dry_run=True) == ["R001", "R003"]
        assert scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 3, 0, tzinfo=UTC), dry_run=True) == []


class TestChannelFilter:
    def test_channels_limits_a_due_phone_row_to_the_text(self, data):
        providers = {"grok_voice": FakeProvider(), "photon_sms": FakeProvider(), "email": FakeProvider()}
        sent = scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 11, 31, tzinfo=UTC), dry_run=False,
                              providers=providers, channels=("photon_sms",))
        assert sent == ["R001", "R003"]
        assert sorted(h for h, _ in providers["photon_sms"].sent) == ["+15155550101", "+15155550103"]
        assert providers["grok_voice"].sent == []
        again = scheduler.tick(data, data / "roster.csv", datetime(2026, 10, 17, 11, 45, tzinfo=UTC), dry_run=False,
                               providers=providers, channels=("photon_sms",))
        assert again == []                                                        # the text went; the call is not owed
        deliveries = json.loads((data / "deliveries.json").read_text())["deliveries"]
        assert {d["channel"] for d in deliveries} == {"photon_sms"}
