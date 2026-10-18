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
import os
from collections import Counter
from datetime import date
from pathlib import Path

import analytics
from analytics import read_optional
from common import read_json, s, product_name
from tools import load_roster

CHANNELS = ("grok_voice", "photon_sms", "email")
SPEC_CHANNELS = ("grok_voice", "photon_sms")      # the two channels in the spec; email is additive


def row_channels(row):
    """The channels a roster row can receive: a phone gets the call and the text, an email gets the email."""
    out = list(SPEC_CHANNELS) if (row.get("phone") or "").strip() else []
    if (row.get("email") or "").strip():
        out.append("email")
    return out
RETRIES = 2
LABELS = {"harvest_ready": "Harvest readiness", "harvest_before_rain": "Harvest versus rain", "establishment": "Establishment",
          "burn_scar": "Burn or tillage scar", "fire": "Fire", "flood": "Flood", "stress": "Stress or browning",
          "visible_change": "Visible change"}
STATUS_WORDS = {"clear": "clear", "watch": "watch", "act_now": "act now"}

# Language packs for the fixed structure of the words (labels, status words, headings). The free text (details,
# today line, route reasons) is English unless a `translate(text, language)` hook is given, e.g. a Grok call.
# pa / hi strings were drafted by the team's agent: have a native speaker read them before the first real send.
PACKS = {
    "en": {
        "head": "{product} morning plan for farm {farm}, from the satellite pass of {date}.",
        "flag": "Flag", "today": "Today", "checks": "Checks", "risks": "Risks", "all_clear": "All eight checks are clear.",
        "status": STATUS_WORDS, "flags": {"fine": "fine", "watch": "watch", "act_now": "act now"}, "labels": LABELS,
        "route_head": "{product} officer route for {date}: {n} stops.", "no_stops": "No stops today: every farm is fine.",
        "farm": "Farm", "actions": {"visit": "visit", "call": "call"}, "outlook": "Outlook",
    },
    "hi": {
        "head": "{product} की सुबह की योजना, खेत {farm}, {date} के सैटेलाइट पास से।",
        "flag": "स्थिति", "today": "आज", "checks": "जाँचें", "risks": "जोखिम", "all_clear": "सभी आठ जाँचें ठीक हैं।", "weather": "मौसम", "outlook": "आगे का अनुमान",
        "status": {"clear": "ठीक", "watch": "ध्यान दें", "act_now": "अभी कार्रवाई करें"},
        "flags": {"fine": "ठीक", "watch": "ध्यान दें", "act_now": "अभी कार्रवाई करें"},
        "labels": {"harvest_ready": "कटाई की तैयारी", "harvest_before_rain": "बारिश से पहले कटाई", "establishment": "फसल का जमाव",
                   "burn_scar": "जलने का निशान", "fire": "आग", "flood": "खड़ा पानी", "stress": "तनाव या पीलापन",
                   "visible_change": "दिखने वाला बदलाव"},
        "route_head": "{product} अधिकारी रूट, {date}: {n} स्टॉप।", "no_stops": "आज कोई स्टॉप नहीं: हर खेत ठीक है।",
        "farm": "खेत", "actions": {"visit": "जाएँ", "call": "फ़ोन करें"},
    },
    "pa": {
        "head": "{product} ਸਵੇਰ ਦੀ ਯੋਜਨਾ, ਖੇਤ {farm}, {date} ਦੇ ਸੈਟੇਲਾਈਟ ਪਾਸ ਤੋਂ।",
        "flag": "ਸਥਿਤੀ", "today": "ਅੱਜ", "checks": "ਜਾਂਚਾਂ", "risks": "ਖ਼ਤਰੇ", "all_clear": "ਸਾਰੀਆਂ ਅੱਠ ਜਾਂਚਾਂ ਠੀਕ ਹਨ।", "weather": "ਮੌਸਮ", "outlook": "ਅੱਗੇ ਦਾ ਅੰਦਾਜ਼ਾ",
        "status": {"clear": "ਠੀਕ", "watch": "ਧਿਆਨ ਦਿਓ", "act_now": "ਹੁਣੇ ਕਾਰਵਾਈ ਕਰੋ"},
        "flags": {"fine": "ਠੀਕ", "watch": "ਧਿਆਨ ਦਿਓ", "act_now": "ਹੁਣੇ ਕਾਰਵਾਈ ਕਰੋ"},
        "labels": {"harvest_ready": "ਵਾਢੀ ਦੀ ਤਿਆਰੀ", "harvest_before_rain": "ਮੀਂਹ ਤੋਂ ਪਹਿਲਾਂ ਵਾਢੀ", "establishment": "ਫ਼ਸਲ ਦਾ ਜੰਮਣਾ",
                   "burn_scar": "ਸੜਨ ਦਾ ਨਿਸ਼ਾਨ", "fire": "ਅੱਗ", "flood": "ਖੜ੍ਹਾ ਪਾਣੀ", "stress": "ਤਣਾਅ ਜਾਂ ਪੀਲਾਪਣ",
                   "visible_change": "ਦਿਖਾਈ ਦੇਣ ਵਾਲੀ ਤਬਦੀਲੀ"},
        "route_head": "{product} ਅਫ਼ਸਰ ਰੂਟ, {date}: {n} ਸਟਾਪ।", "no_stops": "ਅੱਜ ਕੋਈ ਸਟਾਪ ਨਹੀਂ: ਹਰ ਖੇਤ ਠੀਕ ਹੈ।",
        "farm": "ਖੇਤ", "actions": {"visit": "ਜਾਓ", "call": "ਫ਼ੋਨ ਕਰੋ"},
    },
}


def pack(language):
    return PACKS.get((language or "en").strip().lower()[:2], PACKS["en"])


def _translator(translate, language, P):
    if translate and P is not PACKS["en"]:
        return lambda text: translate(text, language)
    return lambda text: text


class UnverifiedProvider:
    """Placeholder for a channel that is not configured yet (see providers.py for the real adapters)."""

    def __init__(self, channel, reason=None):
        self.channel, self.reason = channel, reason

    def send(self, phone, text, language):
        why = self.reason or "provider not configured"
        raise NotImplementedError(f"{self.channel}: {why}. Run with --dry-run, configure .env for providers.from_env(), "
                                  f"or pass run(providers=...) objects with send(phone, text, language)")


# ---------------- words ----------------
def render_plan(plan, language, translate=None, outlook=None):
    """The farmer's morning plan in the roster language, every check included (clear ones too).
    translate(text, language) -> text, when given, is applied to the free-text parts (details, today, risks).
    outlook = analytics.farm_outlook(...) lines for this farm, when the analytics layer has run (additive line)."""
    P = pack(language)
    tr = _translator(translate, language, P)
    lines = [P["head"].format(product=product_name(), farm=plan["farm_id"], date=plan["as_of"]) + f" {P['flag']}: {P['flags'].get(plan['flag'], plan['flag'])}.",
             f"{P['today']}: {tr(plan['today'])}",
             f"{P['checks']}:"]
    for c in plan["checks"]:
        lines.append(f"- {P['labels'].get(c['kind'], c['kind'])}: {P['status'][c['status']]}. {tr(c['detail'])}"
                     + (f" ({tr(c['where'])})" if c.get("where") else ""))
    if plan.get("harvest_outlook", {}).get("summary"):
        lines.append(f"{P.get('weather', 'Weather')}: {tr(plan['harvest_outlook']['summary'])}")
    if outlook:
        lines.append(f"{P.get('outlook', 'Outlook')}: " + " ".join(tr(x) for x in outlook))
    lines.append(f"{P['risks']}: " + "; ".join(tr(r) for r in plan["risks"]) if plan["risks"] else P["all_clear"])
    return "\n".join(lines)


def render_route(route, language, translate=None):
    """The officer's route in the roster language: which farms to visit or call today, in order, and why."""
    P = pack(language)
    tr = _translator(translate, language, P)
    stops = route.get("stops", [])
    head = P["route_head"].format(product=product_name(), date=route.get("as_of", ""), n=len(stops))
    if not stops:
        return head + " " + P["no_stops"]
    body = [f"{i}. {P['farm']} {st['farm_id']}: {P['actions'].get(st['action'], st['action'])}. {tr(st['why'])}"
            for i, st in enumerate(stops, start=1)]
    return "\n".join([head] + body)


# ---------------- checkpoint ----------------
def load_deliveries(data_dir):
    p = Path(data_dir) / "deliveries.json"
    return read_json(data_dir, "deliveries.json") if p.exists() else {"deliveries": []}


def save_deliveries(data_dir, doc):
    """Checkpoint write: quiet (it happens before and after every attempt) and atomic (temp file + replace), so a
    crash mid-write leaves the previous complete checkpoint, never a truncated one that would break the restart."""
    import json
    p = Path(data_dir) / "deliveries.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


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
def run(data_dir, roster_path=None, dry_run=True, today=None, providers=None, only=None, translate=None):
    """only = roster_ids to process (None = all; an empty list = nobody). translate = optional (text, language) hook
    for the free-text parts of the words."""
    data_dir = Path(data_dir)
    today = today or date.today()
    dstr = s(today)
    roster = load_roster(roster_path)
    plans = {p["farm_id"]: p for p in read_json(data_dir, "plans.json")["plans"]}
    route = read_json(data_dir, "route.json")
    outlook_doc = read_optional(data_dir, "analytics.json")   # additive: outlook lines when the analytics ran
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
        if only is not None and row["roster_id"] not in only:
            continue
        err, text = None, None
        channels = row_channels(row)
        if not channels:
            err = "no phone number or email in the roster"
        elif row["role"] == "farmer" and row["farm_id"] not in plans:
            err = f"no plan for farm {row['farm_id']}"
        else:
            text = render_route(route, row["language"], translate) if row["role"] == "officer" \
                else render_plan(plans[row["farm_id"]], row["language"], translate,
                                 analytics.farm_outlook(outlook_doc, row["farm_id"]) if outlook_doc else None)
        for ch in (channels or SPEC_CHANNELS):
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
            rec["status"], rec["detail"] = _attempt(providers.get(ch) or UnverifiedProvider(ch, "provider not configured"),
                                                   row.get("email") if ch == "email" else row["phone"], text, row["language"])
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
    ap.add_argument("--translate", action="store_true", help="translate free text with Grok (XAI_API_KEY), cached in <data>/translations.json")
    a = ap.parse_args()
    translate = None
    if a.translate:
        from providers import translator_from_env
        translate = translator_from_env(cache_path=Path(a.data) / "translations.json")
        if translate is None:
            print("  --translate: XAI_API_KEY not set, words stay English")
    run(Path(a.data), roster_path=a.roster, dry_run=a.dry_run, today=date.fromisoformat(a.date) if a.date else None,
        only=a.only, translate=translate)
