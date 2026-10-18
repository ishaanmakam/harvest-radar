"""The demo morning in one command: words for every roster row, the queue, and a summary.

    python intelligence/morning.py --data data/mock --preview                 # dry run + messages.json (default)
    python intelligence/morning.py --data data/out --real --translate         # real sends via providers.from_env()
    python intelligence/morning.py --data data/out --only R001 R002           # a subset of roster rows

Steps: read plans.json + route.json (written by run_all.py / refresh.py) -> optional messages.json with the exact
words per roster row (no phone numbers in it) -> queue_morning.run (dry run unless --real) -> one-line summary.
"""
import argparse
from collections import Counter
from datetime import date
from pathlib import Path

import analytics
from analytics import read_optional
import queue_morning as q
import accounts
from common import read_json, s, write_json
from tools import load_roster


def preview_messages(plans, route, roster, only=None, translate=None, outlook_doc=None):
    """[{roster_id, farm_id, role, language, text}] for every row that can be sent (has a phone and a plan)."""
    out = []
    for row in roster:
        if only is not None and row["roster_id"] not in only:
            continue
        if not row["phone"]:
            continue
        if row["role"] == "officer":
            text = q.render_route(route, row["language"], translate)
        elif row["farm_id"] in plans:
            text = q.render_plan(plans[row["farm_id"]], row["language"], translate,
                                 analytics.farm_outlook(outlook_doc, row["farm_id"]) if outlook_doc else None)
        else:
            continue
        out.append({"roster_id": row["roster_id"], "farm_id": row["farm_id"], "role": row["role"],
                    "language": row["language"], "text": text})
    return out


def run(data_dir, roster_path=None, today=None, dry_run=True, preview=False, providers=None, translate=None, only=None):
    data_dir = Path(data_dir)
    today = today or date.today()
    plans_doc = read_json(data_dir, "plans.json")
    route = read_json(data_dir, "route.json")
    plans = {p["farm_id"]: p for p in plans_doc["plans"]}
    outlook_doc = read_optional(data_dir, "analytics.json")
    roster = load_roster(roster_path)

    messages = []
    if preview:
        messages = preview_messages(plans, route, roster, only, translate, outlook_doc)
        write_json(data_dir, "messages.json", {"date": s(today), "messages": messages})

    doc = q.run(data_dir, roster_path=roster_path, dry_run=dry_run, today=today, providers=providers, only=only,
                translate=translate)
    try:
        logged = accounts.push_if_configured(doc, dry_run, run_date=s(today))   # the admin page reads this; a failed log never fails the run
    except Exception as e:
        logged = None
        print(f"  morning: delivery log not written: {e}")
    todays = Counter(r["status"] for r in doc["deliveries"] if r["date"] == s(today))
    summary = {
        "date": s(today), "mode": "dry run" if dry_run else "real send",
        "plans_as_of": plans_doc.get("as_of"),
        "flags": {f: sum(p["flag"] == f for p in plans.values()) for f in ("act_now", "watch", "fine")},
        "route_stops": len(route.get("stops", [])),
        "roster_rows": len(roster), "messages": len(messages),
        "deliveries": {k: todays.get(k, 0) for k in ("pending", "sent", "failed")},
        "logged_deliveries": logged,
    }
    print(f"  morning {summary['date']} ({summary['mode']}): plans from pass {summary['plans_as_of']}, farms {summary['flags']}, "
          f"{summary['route_stops']} route stops, {summary['roster_rows']} roster rows, deliveries {summary['deliveries']}")
    return {"summary": summary, "deliveries": doc, "messages": messages}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default: ROSTER_PATH from .env)")
    ap.add_argument("--date", default=None, help="morning date YYYY-MM-DD (default: today)")
    ap.add_argument("--real", action="store_true", help="place calls and send texts through providers.from_env()")
    ap.add_argument("--preview", action="store_true", help="write messages.json with the exact words per roster row")
    ap.add_argument("--translate", action="store_true", help="translate free text with Grok (XAI_API_KEY), cached")
    ap.add_argument("--only", nargs="*", default=None, help="roster_ids to process (default: all)")
    a = ap.parse_args()
    translate = None
    if a.translate:
        from providers import translator_from_env
        translate = translator_from_env(cache_path=Path(a.data) / "translations.json")
        if translate is None:
            print("  --translate: XAI_API_KEY not set, words stay English")
    run(Path(a.data), roster_path=a.roster, today=date.fromisoformat(a.date) if a.date else None, dry_run=not a.real,
        preview=a.preview, translate=translate, only=a.only)
