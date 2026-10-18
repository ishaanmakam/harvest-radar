#!/usr/bin/env python3
"""Ishaan's Grok Bot: team-coordination bot for Ishaan's workstream on Harvest Radar.

Run from the repo root and leave the window open:
    pip install -r bots/requirements.txt
    python bots/grok_bot.py

What it does (mirrors the team's Grok Bot rules, adapted for Ishaan):
  1. Ishaan DMs the bot what his agent built -> bot posts DONE / FILES / CONTRACT IMPACT / NEEDS in #bot-channel.
     Ishaan can also DM questions ("what's blocking us?") and gets answers from the team log.
  2. Reads every message in #bot-channel and keeps a running team log (bots/team_log.jsonl, not committed).
  3. When another workstream's update affects Ishaan's work (contracts/, config/, data/out inputs, plans/route/deliveries),
     it acknowledges in that thread and DMs Ishaan. Questions for Ishaan's workstream get answered if the log/repo
     covers it, otherwise "checking with Ishaan" + a DM.
  4. Flags blockers and contradictions between workstreams without picking a side.
Never agrees to contract/config/spec changes or commits Ishaan to times or work; those go to Ishaan by DM.
Guardrails: never replies to itself, max 3 replies per thread, 30 posts per hour.

.env needs: SLACK_BOT_TOKEN (xoxb-), SLACK_APP_TOKEN (xapp-), XAI_API_KEY.
Optional: XAI_MODEL (default grok-4.7), BOT_CHANNEL (default bot-channel), OWNER_NAME (default "Ishaan").
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

ROOT = Path(__file__).resolve().parents[1]
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

OWNER = os.environ.get("OWNER_NAME", "Ishaan")
PREFIX = f"[{OWNER}'s Grok Bot]"
BOT_CHANNEL = os.environ.get("BOT_CHANNEL", "bot-channel")
MODEL = os.environ.get("XAI_MODEL", "grok-4.7")
LOG = ROOT / "bots" / "team_log.jsonl"
SPEC = "docs/superpowers/specs/2026-10-17-field-inspector-design.md"
MAX_PER_THREAD, MAX_PER_HOUR = 3, 30

PERSONA = f"""You are "{PREFIX[1:-1]}", the project-planning and team-coordination bot for {OWNER}'s workstream on Harvest Radar
(DubHacks 2026, submissions due Sun Oct 18 10:30 AM PT). You do not write code, place calls, or send texts.

Project (source of truth: {SPEC}): an autonomous field inspector for small family farms. Sentinel-2 imagery, rainfall and the
forecast go in; Perceptron Mk1.5 reads the season time-lapse; a decision layer writes one plan per farm in plans.json (8 checks
every morning, clear ones included: harvest_ready, harvest_before_rain, establishment, burn_scar, fire, flood, stress,
visible_change; flag fine/watch/act_now) plus one officer route in route.json; Grok Voice calls and Photon SMS/iMessage deliver
it (deliveries.json); a Vite+MapLibre map shows it. Repo: github.com/ishaanmakam/harvest-radar.

Workstreams:
- Niranjan: season pipeline in pipeline/ -> fields, zones, timeseries, frames, weather, fires in data/out/.
- {OWNER}: intelligence -> decision layer (plans.json, route.json), morning queue, Grok Voice and Photon texts.
- Srikar: vision (Perceptron) and the web map.
Rules: each person edits only their own folder; shared contracts/config/spec change only after the team agrees.
When talking about farms, use plans.json / route.json facts only; never invent measurements. Keep messages short and plain.
You post from a bot account; every message you post must start with "{PREFIX}"."""


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def repo_facts():
    sh("git", "fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*")
    facts = {"main_recent": sh("git", "log", "origin/main", "-6", "--pretty=%cr %an: %s")}
    for b in sh("git", "branch", "-r", "--format=%(refname:short)").splitlines():
        if b.endswith("/main") or b.endswith("/HEAD"):
            continue
        log = sh("git", "log", f"origin/main..{b}", "-4", "--pretty=%cr: %s")
        if log:
            facts[f"branch {b}"] = log
    files = sh("git", "ls-tree", "--name-only", "origin/main", "data/out/", "contracts/")
    facts["shared_files_on_main"] = files
    for name in ("plans.json", "route.json"):
        for d in ("data/out", "data/mock"):
            raw = sh("git", "show", f"origin/main:{d}/{name}")
            if raw:
                facts[f"{d}/{name}"] = raw[:3000]
                break
    return facts


def team_log(n=40):
    if not LOG.exists():
        return ""
    lines = LOG.read_text().splitlines()[-n:]
    out = []
    for l in lines:
        try:
            e = json.loads(l)
            out.append(f"{e['t']} {e['who']}: {e['text'][:400]}")
        except Exception:
            pass
    return "\n".join(out)


def log_event(who, text):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"t": datetime.now().strftime("%H:%M"), "who": who, "text": text}) + "\n")


def grok(prompt):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        print("  no XAI_API_KEY")
        return None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def post(url, data):
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())

    try:
        r = post("https://api.x.ai/v1/responses", {"model": MODEL, "input": prompt})
        if r.get("output_text"):
            return r["output_text"].strip()
        texts = [c.get("text", "") for o in r.get("output", []) for c in (o.get("content") or []) if isinstance(c, dict)]
        if any(texts):
            return "\n".join(t for t in texts if t).strip()
    except Exception as e:
        print(f"  responses endpoint failed: {e}")
    try:
        r = post("https://api.x.ai/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": prompt}]})
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Grok call failed: {e}")
        return None


def grok_json(prompt):
    out = grok(prompt + "\n\nRespond with ONLY a JSON object, no code fences.")
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        print(f"  could not parse Grok JSON: {out[:200]}")
        return None


def tag(text):
    text = (text or "").strip()
    return text if text.startswith(PREFIX) else f"{PREFIX} {text}"


app = App(token=os.environ["SLACK_BOT_TOKEN"])
AUTH = app.client.auth_test()
BOT_USER_ID, BOT_ID = AUTH["user_id"], AUTH.get("bot_id")
_names, _channels = {}, {}
_owner = {"id": None, "dm": None}
replies_in_thread = defaultdict(int)
recent_posts = deque()


def name_of(user_id):
    if not user_id:
        return "unknown"
    if user_id not in _names:
        try:
            u = app.client.users_info(user=user_id)["user"]
            _names[user_id] = u.get("real_name") or u.get("name") or user_id
        except Exception:
            _names[user_id] = user_id
    return _names[user_id]


def owner_id():
    if not _owner["id"]:
        for page in app.client.users_list(limit=200)["members"]:
            if OWNER.lower() in (page.get("real_name") or page.get("name") or "").lower() and not page.get("is_bot"):
                _owner["id"] = page["id"]
                break
    return _owner["id"]


def dm_owner(text):
    oid = owner_id()
    if not oid:
        print("  could not find owner to DM")
        return
    if not _owner["dm"]:
        _owner["dm"] = app.client.conversations_open(users=oid)["channel"]["id"]
    app.client.chat_postMessage(channel=_owner["dm"], text=text)


def channel_name(cid):
    if cid not in _channels:
        try:
            _channels[cid] = app.client.conversations_info(channel=cid)["channel"].get("name", cid)
        except Exception:
            _channels[cid] = cid
    return _channels[cid]


def bot_channel_id():
    for c in app.client.conversations_list(limit=200, types="public_channel")["channels"]:
        if c["name"] == BOT_CHANNEL:
            return c["id"]
    return None


def thread_text(channel, thread_ts):
    if not thread_ts:
        return ""
    msgs = app.client.conversations_replies(channel=channel, ts=thread_ts, limit=30)["messages"]
    return "\n".join(f"{'you' if m.get('bot_id') == BOT_ID else name_of(m.get('user'))}: {m.get('text', '')}" for m in msgs)


def allowed(thread_key):
    now = time.time()
    while recent_posts and now - recent_posts[0] > 3600:
        recent_posts.popleft()
    return replies_in_thread[thread_key] < MAX_PER_THREAD and len(recent_posts) < MAX_PER_HOUR


def posted(thread_key):
    replies_in_thread[thread_key] += 1
    recent_posts.append(time.time())


def context_block(extra=""):
    return (f"{PERSONA}\n\nTeam log (most recent last):\n{team_log() or '(empty)'}\n\n"
            f"Repo state:\n{json.dumps(repo_facts(), indent=1)}\n{extra}")


def on_owner_dm(event):
    text = event.get("text", "")
    decision = grok_json(context_block() + f"""

{OWNER} just DMed you:
\"\"\"{text}\"\"\"

Decide:
- If it describes work his agent built or finished, write a #bot-channel update in exactly this format (plain text):
  {PREFIX}
  DONE: what works now
  FILES: what changed
  CONTRACT IMPACT: none, or which shared file/schema is affected
  NEEDS: anything needed from another workstream
  Only use facts from his message and the repo state. Put it in "post".
- If it's a question, answer it from the team log and repo state in "reply" (say plainly if you don't know).
- If he asks you to post something specific, put it in "post".
JSON keys: "post" (string, may be empty), "reply" (string for {OWNER}, may be empty).""")
    if not decision:
        app.client.chat_postMessage(channel=event["channel"], text="Grok is unavailable right now (check the bot window for the error).")
        return
    if decision.get("post"):
        cid = bot_channel_id()
        if cid and allowed("dm-post"):
            msg = tag(decision["post"])
            app.client.chat_postMessage(channel=cid, text=msg)
            posted("dm-post")
            log_event(PREFIX[1:-1], msg)
            app.client.chat_postMessage(channel=event["channel"], text=f"Posted to #{BOT_CHANNEL}:\n{msg}")
    if decision.get("reply"):
        app.client.chat_postMessage(channel=event["channel"], text=decision["reply"])


def on_channel_message(event, mentioned):
    text = event.get("text", "")
    author = name_of(event.get("user"))
    channel = event["channel"]
    log_event(author, text)
    if not mentioned and channel_name(channel) != BOT_CHANNEL:
        return
    thread_ts = event.get("thread_ts") or event["ts"]
    if not allowed(thread_ts):
        print("  rate limit, skipping")
        return
    decision = grok_json(context_block(f"\nThread so far:\n{thread_text(channel, event.get('thread_ts'))}") + f"""

New message in #{channel_name(channel)} from {author}{' (they @mentioned you)' if mentioned else ''}:
\"\"\"{text}\"\"\"

Decide what to do, following these rules:
- If you were @mentioned, always answer in the thread.
- If it asks {OWNER}'s workstream something answerable from the log/repo, answer in the thread.
- If it affects {OWNER}'s work (contracts/, config/, the spec, data/out inputs, plans/route/deliveries, voice/texts), acknowledge
  in the thread and say what it means for the decision layer, and notify {OWNER}.
- If it reveals a blocker or two workstreams contradicting each other, say so in the thread without picking a side, and notify {OWNER}.
- If answering would agree to change a shared contract/config/spec, commit {OWNER} to a time, or you don't know: thread reply
  "checking with {OWNER}" and put the question in "notify".
- Acknowledgements, thanks, intros that need nothing: no thread reply.
- {OWNER}'s own human messages: never reply in the thread; notify only if something needs his attention.
JSON keys: "thread_reply" (string, may be empty), "notify" (string for {OWNER}'s DM, may be empty).""")
    if not decision:
        return
    if decision.get("thread_reply"):
        reply = tag(decision["thread_reply"])
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=reply)
        posted(thread_ts)
        log_event(PREFIX[1:-1], reply)
        print(f"  replied in #{channel_name(channel)}")
    if decision.get("notify"):
        dm_owner(f"{decision['notify']}\n(from #{channel_name(channel)}, {author})")
        print("  notified owner")


def skip(event):
    if event.get("subtype") not in (None, "thread_broadcast"):
        return True
    if event.get("user") == BOT_USER_ID or (BOT_ID and event.get("bot_id") == BOT_ID):
        return True
    return event.get("text", "").startswith(PREFIX)


@app.event("app_mention")
def on_mention(event):
    if skip(event):
        return
    print(f"mention from {name_of(event.get('user'))}")
    on_channel_message(event, mentioned=True)


@app.event("message")
def on_message(event):
    if skip(event) or f"<@{BOT_USER_ID}>" in event.get("text", ""):
        return
    if event.get("channel_type") == "im":
        if event.get("user") == owner_id():
            print("DM from owner")
            on_owner_dm(event)
        return
    on_channel_message(event, mentioned=False)


if __name__ == "__main__":
    print(f"{PREFIX} online as {AUTH['user']} ({MODEL}). Watching #{BOT_CHANNEL}; DM me your updates. Ctrl+C to stop.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
