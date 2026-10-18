"""Morning queue: every farmer in the roster gets their plan by Grok Voice call + Photon SMS; the officer gets the
route by both. Spec: "Morning run", "Error handling", "Testing" (field-inspector design).

    python intelligence/queue_morning.py --data data/mock --dry-run     # writes delivery rows, places nothing
    python intelligence/queue_morning.py --data data/mock               # real send (providers must be verified first)

(Named queue_morning, not queue: a queue.py inside intelligence/ would shadow Python's standard `queue` module.)

Rules
- sends go only to numbers in the roster (ROSTER_PATH in .env; see roster.example.csv)
- one delivery row per (date, roster_id, channel) is written BEFORE each attempt; deliveries.json is the checkpoint
- a row already `sent` that morning on that channel is never sent again; pending / failed rows are continued on restart
- a missing phone or a missing plan is a `failed` row and the queue continues
- two retries after the first attempt, per channel; voice and SMS rows are independent
- the roster language is applied when the words are rendered, so both channels say the same thing
"""
import argparse
from collections import Counter
from datetime import date
from pathlib import Path

from common import read_json, s
from tools import load_roster

CHANNELS = ("grok_voice", "photon_sms")
RETRIES = 2
LABELS = {"harvest_ready": "Harvest readiness", "harvest_before_rain": "Harvest versus rain", "establishment": "Establishment",
          "burn_scar": "Burn scar", "fire": "Fire", "flood": "Flood", "stress": "Stress or browning",
          "visible_change": "Visible change"}
STATUS_WORDS = {"clear": "clear", "watch": "watch", "act_now": "act now"}


class UnverifiedProvider:
    """Placeholder for a channel that is not configured yet (see providers.py for the real adapters)."""

    def __init__(self, channel, reason=None):
        self.channel, self.reason = channel, reason

    def send(self, phone, text, language):
        why = self.reason or "provider not configured"
        raise NotImplementedError(f"{self.channel}: {why}. Run with --dry-run, configure .env for providers.from_env(), "
                                  f"or pass run(providers=...) objects with send(phone, text, language)")


# ---------------- words ----------------
def render_plan(plan, language):
    """The farmer's morning plan, every check included (clear ones too). Language is a tag for the voice/SMS layer."""
    lines = [f"[{language}] Harvest Radar morning plan for farm {plan['farm_id']}, from the satellite pass of {plan['as_of']}. "
             f"Flag: {STATUS_WORDS.get(plan['flag'], plan['flag'])}.",
             f"Today: {plan['today']}",
             "Checks:"]
    for c in plan["checks"]:
        lines.append(f"- {LABELS.get(c['kind'], c['kind'])}: {STATUS_WORDS[c['status']]}. {c['detail']}"
                     + (f" ({c['where']})" if c.get("where") else ""))
    lines.append("Risks: " + "; ".join(plan["risks"]) if plan["risks"] else "All eight checks are clear.")
    return "\n".join(lines)


def render_route(route, language):
    """The officer's route: which farms to visit or call today, in order, and why."""
    stops = route.get("stops", [])
    head = f"[{language}] Harvest Radar officer route for {route.get('as_of', '')}: {len(stops)} stops."
    if not stops:
        return head + " No stops today: every farm is fine."
    body = [f"{i}. Farm {st['farm_id']}: {st['action']}. {st['why']}" for i, st in enumerate(stops, start=1)]
    return "\n".join([head] + body)


# ---------------- checkpoint ----------------
def load_deliveries(data_dir):
    p = Path(data_dir) / "deliveries.json"
    return read_json(data_dir, "deliveries.json") if p.exists() else {"deliveries": []}


def save_deliveries(data_dir, doc):
    """Checkpoint write (quiet: it happens before and after every attempt)."""
    import json
    p = Path(data_dir) / "deliveries.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")


def _attempt(provider, phone, text, language):
    last = ""
    for i in range(1, RETRIES + 2):
        try:
            result = provider.send(phone, text, language)
            return "sent", f"{result or 'ok'} (attempt {i})"
        except NotImplementedError:
            raise
        except Exception as e:  # provider error: retry
            last = str(e)
    return "failed", f"failed after {RETRIES + 1} attempts: {last}"


# ---------------- the run ----------------
def run(data_dir, roster_path=None, dry_run=True, today=None, providers=None, only=None):
    data_dir = Path(data_dir)
    today = today or date.today()
    dstr = s(today)
    roster = load_roster(roster_path)
    plans = {p["farm_id"]: p for p in read_json(data_dir, "plans.json")["plans"]}
    route = read_json(data_dir, "route.json")
    if providers is None:
        if dry_run:
            providers = {c: UnverifiedProvider(c, "dry run") for c in CHANNELS}
        else:
            from providers import from_env  # real send: adapters from .env (unconfigured channels fail loudly)
            providers = from_env()
    doc = load_deliveries(data_dir)
    index = {(r["date"], r["roster_id"], r["channel"]): r for r in doc["deliveries"]}
    counts = Counter()

    for row in roster:
        if only and row["roster_id"] not in only:
            continue
        err, text = None, None
        if not row["phone"]:
            err = "no phone number in the roster"
        elif row["role"] == "farmer" and row["farm_id"] not in plans:
            err = f"no plan for farm {row['farm_id']}"
        else:
            text = render_route(route, row["language"]) if row["role"] == "officer" \
                else render_plan(plans[row["farm_id"]], row["language"])
        for ch in CHANNELS:
            key = (dstr, row["roster_id"], ch)
            rec = index.get(key)
            if rec and rec["status"] == "sent":
                counts["already sent"] += 1
                continue
            if rec is None:
                rec = {"date": dstr, "roster_id": row["roster_id"], "channel": ch, "status": "pending", "detail": ""}
                doc["deliveries"].append(rec)
                index[key] = rec
            rec["farm_id"], rec["role"] = row["farm_id"], row["role"]
            if err:
                rec["status"], rec["detail"] = "failed", err
                counts["failed"] += 1
                continue
            rec["status"], rec["detail"] = "pending", ("dry run: not sent" if dry_run else "attempting")
            save_deliveries(data_dir, doc)          # the row exists before the attempt
            if dry_run:
                counts["pending (dry run)"] += 1
                continue
            rec["status"], rec["detail"] = _attempt(providers[ch], row["phone"], text, row["language"])
            counts[rec["status"]] += 1
            save_deliveries(data_dir, doc)
    save_deliveries(data_dir, doc)
    mode = "DRY RUN" if dry_run else "real send"
    print(f"  morning queue ({mode}, {dstr}): " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default: ROSTER_PATH from .env)")
    ap.add_argument("--dry-run", action="store_true", help="write delivery rows, place no calls or texts")
    ap.add_argument("--date", default=None, help="morning date YYYY-MM-DD (default: today)")
    ap.add_argument("--only", nargs="*", default=None, help="roster_ids to process (default: all)")
    a = ap.parse_args()
    run(Path(a.data), roster_path=a.roster, dry_run=a.dry_run, today=date.fromisoformat(a.date) if a.date else None,
        only=a.only)
