"""Poll Photon for inbound texts and answer them the same way /photon/webhook does.

A shared iMessage line cannot deliver every message through the webhook. This process lists received messages
and replies in the chat the person already opened.

    python intelligence/photon_poll.py --data data/out [--interval 5] [--dry-run]

--dry-run prints the reply and sends nothing. The createdSince cursor and the seen message ids stay in memory.
HTTP 429 waits for the Retry-After header (seconds or an HTTP date) inside PhotonSMS, then this loop waits
the poll interval if the call still fails.
"""
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import inbound
import providers
from common import load_env
from tools import load_roster


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def handle_inbound(ev, data_dir, roster, photon=None, dry_run=False, log=print):
    """Remember the chat, answer from the roster, and reply in that space. Shared by the webhook and the poller."""
    handle, space_id = ev.get("handle") or "", ev.get("space_id")
    if photon is not None and space_id and hasattr(photon, "remember_space"):
        photon.remember_space(handle, space_id)
    if photon is not None and ev.get("received_by_id") and hasattr(photon, "remember_line_sender"):
        photon.remember_line_sender(ev["received_by_id"])
    reply = inbound.answer(handle, ev.get("text") or "", data_dir, roster)
    if dry_run:
        log(f"dry-run {handle}: {reply}")
        return {"handled": True, "to": handle, "reply": reply, "sent": None}
    sent = None
    if photon is not None and space_id:
        try:
            sent = photon.send_to_space(space_id, reply)
        except Exception as e:  # the webhook must still be acknowledged; the poller moves on to the next text
            sent = f"reply failed: {e}"
    return {"handled": True, "to": handle, "reply": reply, "sent": sent}


def poll_once(photon, data_dir, roster, cursor, dry_run=False, log=print):
    """List messages newer than cursor['created_since'] and answer each new one. cursor is updated in place."""
    items = photon.list_received(created_since=cursor.get("created_since"))
    seen = cursor.setdefault("seen", set())
    for msg in items:
        ev = providers.parse_photon_webhook({"type": "message.received", "data": {"object": msg}})
        mid = (ev or {}).get("message_id") or msg.get("id")
        created = msg.get("createdAt")
        if mid and mid in seen:
            if created:
                cursor["created_since"] = created
            continue
        if mid:
            if len(seen) > 5000:
                seen.clear()
            seen.add(mid)
        if ev is None or ev.get("from_agent"):
            if created:
                cursor["created_since"] = created
            continue
        handle_inbound(ev, data_dir, roster, photon=photon, dry_run=dry_run, log=log)
        if created:
            cursor["created_since"] = created
    return cursor


def run(data_dir, roster_path=None, interval=5, dry_run=False, since=None, photon=None, sleep=time.sleep, log=print):
    load_env()
    photon = photon if photon is not None else providers.photon_from_env()
    if photon is None:
        raise SystemExit("Photon is not configured (set PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_FROM)")
    roster = load_roster(roster_path)
    cursor = {"created_since": since or _now_iso(), "seen": set()}
    log(f"photon poll every {interval:g}s platform={photon.platform} dry_run={dry_run}")
    while True:
        try:
            poll_once(photon, data_dir, roster, cursor, dry_run=dry_run, log=log)
        except providers.ProviderError as e:
            log(f"photon poll: {e}")
        sleep(interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Answer inbound Photon messages by polling listMessages.")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default: ROSTER_PATH from .env)")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--dry-run", action="store_true", help="print the reply and send nothing")
    args = ap.parse_args()
    run(Path(args.data), args.roster, args.interval, args.dry_run)
