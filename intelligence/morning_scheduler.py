"""Send the morning plan at a fixed local time every day.

Runs the morning queue (queue_morning.run) once a day at --at (default 08:00 local time) for every roster row:
the farmers who created an account (ACCOUNTS_DB) and any roster CSV (ROSTER_PATH). By default only the text
channel is used, so each farmer gets one Photon text a morning with that day's plan for their farm.

    python intelligence/morning_scheduler.py --data data/out            # waits for 08:00, sends, waits for tomorrow
    python intelligence/morning_scheduler.py --data data/out --once     # send now, once (after a dry run elsewhere)
    python intelligence/morning_scheduler.py --data data/out --dry-run  # write delivery rows, send nothing
"""
import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_env  # noqa: E402
import queue_morning  # noqa: E402


def seconds_until(hhmm, now=None):
    """Seconds from now until the next occurrence of hh:mm local time (today if still ahead, else tomorrow)."""
    now = now or dt.datetime.now()
    hour, minute = (int(x) for x in hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def send_once(data_dir, channels, dry_run):
    today = dt.date.today()
    print(f"morning run {today} ({'dry run' if dry_run else 'live'}) channels={','.join(channels)}", flush=True)
    queue_morning.run(Path(data_dir), roster_path=os.environ.get("ROSTER_PATH") or None, dry_run=dry_run,
                      today=today, channel_filter=channels)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--at", default="08:00", help="local time of the daily send, HH:MM")
    ap.add_argument("--channels", default="photon_sms", help="comma list of channels; default the text only")
    ap.add_argument("--once", action="store_true", help="send now and exit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    load_env()
    channels = tuple(c.strip() for c in a.channels.split(",") if c.strip())
    if a.once:
        send_once(a.data, channels, a.dry_run)
        return
    while True:
        wait = seconds_until(a.at)
        print(f"next morning send at {a.at} local, in {wait / 3600:.1f} h", flush=True)
        time.sleep(wait)
        try:
            send_once(a.data, channels, a.dry_run)
        except Exception as e:  # keep the loop alive; the queue logs the row
            print(f"morning run failed: {e}", flush=True)
        time.sleep(90)  # past the minute, so the same morning does not fire twice


if __name__ == "__main__":
    main()
