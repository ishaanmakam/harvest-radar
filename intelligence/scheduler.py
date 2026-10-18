"""Send each roster row its morning plan at its own local time.

    python intelligence/scheduler.py --data data/out --roster roster.csv              # dry run, one tick
    python intelligence/scheduler.py --data data/out --roster roster.csv --real --forever   # every 30 s with .env providers

Rows carry send_time (HH:MM local) and timezone (an IANA name such as America/Chicago or Asia/Kolkata); missing or bad
values fall back to DEFAULT_SEND_TIME in DEFAULT_TIMEZONE. A row is due from its minute until LATE_MINUTES after it
(a late plan beats no plan; past that it waits for tomorrow). The deliveries checkpoint (date, roster_id, channel) keeps
a row from going out twice in a day and lets a restart continue where it stopped; a failed channel is retried on the
next tick inside the window.
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import queue_morning as q  # noqa: E402
from common import d, s  # noqa: E402
from tools import load_roster  # noqa: E402

DEFAULT_SEND_TIME = "06:30"
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE") or "America/Chicago"   # the demo landscape is central Iowa
LATE_MINUTES = 90


def schedule_of(row):
    """(send_time, timezone) for a row, with the defaults for anything missing or malformed."""
    t = (row.get("send_time") or "").strip()
    try:
        hh, mm = t.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        t = f"{hh:02d}:{mm:02d}"
    except (ValueError, AttributeError):
        t = DEFAULT_SEND_TIME
    tz = (row.get("timezone") or "").strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(tz)
    except Exception:
        tz = DEFAULT_TIMEZONE
    return t, tz


def local_now(row, now_utc):
    return now_utc.astimezone(ZoneInfo(schedule_of(row)[1]))


def local_date(row, now_utc):
    return local_now(row, now_utc).date()


def is_due(row, now_utc):
    t, _ = schedule_of(row)
    now = local_now(row, now_utc)
    hh, mm = (int(x) for x in t.split(":"))
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= now < start + timedelta(minutes=LATE_MINUTES)


def due_rows(roster, now_utc):
    return [r for r in roster if is_due(r, now_utc)]


def tick(data_dir, roster_path=None, now_utc=None, dry_run=True, providers=None, translate=None):
    """One pass: every due row that still has a channel to send today goes through the morning queue (grouped by its
    local date, so the checkpoint date is the farmer's own morning). Returns the roster ids that were processed."""
    data_dir = Path(data_dir)
    now_utc = now_utc or datetime.now(timezone.utc)
    roster = load_roster(roster_path)
    sent = {(r["date"], r["roster_id"], r["channel"]) for r in q.load_deliveries(data_dir)["deliveries"] if r["status"] == "sent"}
    todo = defaultdict(list)
    for row in due_rows(roster, now_utc):
        day = s(local_date(row, now_utc))
        chans = q.row_channels(row)
        if chans and all((day, row["roster_id"], ch) in sent for ch in chans):
            continue
        todo[day].append(row["roster_id"])
    for day, ids in todo.items():
        q.run(data_dir, roster_path, dry_run=dry_run, today=d(day), providers=providers, only=ids, translate=translate)
    return [rid for ids in todo.values() for rid in ids]


def run_forever(data_dir, roster_path=None, interval_s=30, dry_run=True, providers=None, translate=None):
    print(f"scheduler: every {interval_s} s, {'dry run' if dry_run else 'REAL sends'}, defaults {DEFAULT_SEND_TIME} {DEFAULT_TIMEZONE}")
    while True:
        try:
            ids = tick(data_dir, roster_path, dry_run=dry_run, providers=providers, translate=translate)
            if ids:
                print(f"  {datetime.now(timezone.utc).strftime('%H:%M')}Z sent to {', '.join(ids)}")
        except Exception as e:  # keep the loop alive: a bad roster line or a provider outage is logged, not fatal
            print(f"  scheduler tick failed: {e}")
        time.sleep(interval_s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default ROSTER_PATH from .env)")
    ap.add_argument("--real", action="store_true", help="send for real with the providers configured in .env")
    ap.add_argument("--forever", action="store_true", help="keep ticking every --interval seconds")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--translate", action="store_true", help="translate free text with Grok (XAI_API_KEY)")
    a = ap.parse_args()
    providers = translate = None
    if a.real:
        from providers import from_env, translator_from_env
        providers = from_env()
        translate = translator_from_env() if a.translate else None
    if a.forever:
        run_forever(a.data, a.roster, a.interval, dry_run=not a.real, providers=providers, translate=translate)
    else:
        ids = tick(a.data, a.roster, dry_run=not a.real, providers=providers, translate=translate)
        print("due now:", ", ".join(ids) if ids else "nobody")
