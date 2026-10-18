#!/usr/bin/env python3
"""Ishaan's Grok Bot: listens in Slack, reads the thread + repo state, replies with Grok.

Run from the repo root and leave the window open:
    pip install -r bots/requirements.txt
    python bots/grok_bot.py

Replies when:
  - someone @mentions the bot anywhere it's invited, or
  - a message in #bot-channel concerns Ishaan / workstream B (models + optimization) and
    Grok decides a reply is useful (it answers SKIP otherwise).
Never replies to itself or to Ishaan's own human messages (unless @mentioned), max 3 replies
per thread and 30 per hour, so bot-to-bot threads can't spiral.

.env needs:
  SLACK_BOT_TOKEN   xoxb-...  (OAuth & Permissions page of the Slack app)
  SLACK_APP_TOKEN   xapp-...  (Socket Mode / app-level token with connections:write)
  XAI_API_KEY       Grok API key
  XAI_MODEL         optional, default grok-4.7
  BOT_CHANNEL       optional, default bot-channel
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from collections import defaultdict, deque
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

PREFIX = "[Ishaan's Grok Bot]"
OWNER_NAME = "ishaan"
BOT_CHANNEL = os.environ.get("BOT_CHANNEL", "bot-channel")
MODEL = os.environ.get("XAI_MODEL", "grok-4.7")
MAX_PER_THREAD, MAX_PER_HOUR, MIN_GAP_S = 3, 30, 20
TOPIC_WORDS = re.compile(r"\b(ishaan|intelligence|intel|model|models|optimi[sz]\w*|routing|dispatch|milp|demand|harvest model|"
                         r"status\.json|demand\.json|routing\.json|evaluate|baseline|b/intel|person b)\b", re.I)

PERSONA = f"""You are "Ishaan's Grok Bot", the coordination bot for Ishaan on team Harvest Radar (DubHacks 2026, due Sun Oct 18 10:30 AM PT).
Project: satellite imagery of Punjab rice fields -> per-field harvest prediction -> residue-machine demand forecast -> dispatch plan
that beats first-come-first-served, plus Perceptron reading the satellite time-lapse, shown on a web map.
Ownership: Niranjan = satellite pipeline (pipeline/ -> data/out/*). Ishaan = models + optimization (intelligence/: harvest_model,
demand, routing/MILP, evaluate.py). Srikar = Perceptron vision (vision/) AND the web map (confirmed by Ishaan).
You speak for Ishaan's workstream. Be concise (1-4 sentences), factual, and only use facts from the context below.
If something needs Ishaan's personal decision, say you'll flag it for him. Always start your reply with "{PREFIX}"."""


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def repo_facts():
    sh("git", "fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*")
    facts = {
        "main_recent": sh("git", "log", "origin/main", "-5", "--pretty=%cr %an: %s"),
        "b_intel_recent": sh("git", "log", "origin/main..origin/b/intel", "-5", "--pretty=%cr: %s"),
        "real_data_on_main": sh("git", "ls-tree", "--name-only", "origin/main", "data/out/"),
    }
    for d in ("data/out", "data/mock"):
        p = ROOT / d / "routing.json"
        if p.exists():
            s = json.loads(p.read_text())["summary"]
            facts["hero_metric"] = (f"{d}: optimized {s['optimized']['pct_served']}% vs baseline "
                                    f"{s['baseline']['pct_served']}% of machine-dependent area served in the window")
            break
    return facts


def grok(prompt):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def post(url, data):
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
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


app = App(token=os.environ["SLACK_BOT_TOKEN"])
AUTH = app.client.auth_test()
BOT_USER_ID, BOT_ID = AUTH["user_id"], AUTH.get("bot_id")
_names, _channels = {}, {}
replies_in_thread = defaultdict(int)
recent_replies = deque()
last_reply_at = defaultdict(float)


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


def channel_name(cid):
    if cid not in _channels:
        try:
            _channels[cid] = app.client.conversations_info(channel=cid)["channel"]["name"]
        except Exception:
            _channels[cid] = cid
    return _channels[cid]


def transcript(channel, thread_ts):
    if thread_ts:
        msgs = app.client.conversations_replies(channel=channel, ts=thread_ts, limit=30)["messages"]
    else:
        msgs = list(reversed(app.client.conversations_history(channel=channel, limit=15)["messages"]))
    lines = []
    for m in msgs:
        who = "Ishaan's Grok Bot (you)" if (m.get("user") == BOT_USER_ID or m.get("bot_id") == BOT_ID) else name_of(m.get("user"))
        text = re.sub(r"<@(\w+)>", lambda x: "@" + name_of(x.group(1)), m.get("text", ""))
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def can_reply(thread_key):
    now = time.time()
    while recent_replies and now - recent_replies[0] > 3600:
        recent_replies.popleft()
    return (replies_in_thread[thread_key] < MAX_PER_THREAD and len(recent_replies) < MAX_PER_HOUR
            and now - last_reply_at[thread_key] > MIN_GAP_S)


def handle(event, mentioned):
    if event.get("subtype") not in (None, "thread_broadcast"):
        return
    if event.get("user") == BOT_USER_ID or (BOT_ID and event.get("bot_id") == BOT_ID):
        return
    text = event.get("text", "")
    if text.startswith(PREFIX):
        return
    author = name_of(event.get("user"))
    if not mentioned:
        if channel_name(event["channel"]) != BOT_CHANNEL:
            return
        if OWNER_NAME in author.lower() and "[" not in text[:3]:
            return  # Ishaan's own human messages: don't talk over him
        if not TOPIC_WORDS.search(text):
            return
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]
    if not can_reply(thread_ts):
        print("  rate-limited, skipping")
        return

    convo = transcript(channel, event.get("thread_ts"))
    facts = repo_facts()
    rule = ("You were @mentioned, so reply." if mentioned else
            "Reply ONLY if this message asks Ishaan / his bot something, needs an answer about workstream B, or makes a "
            "claim about B that needs correcting. Do not reply to acknowledgements, thanks, or intros that need nothing. "
            "If no reply is needed, output exactly SKIP.")
    prompt = (f"{PERSONA}\n\nRepo state (JSON):\n{json.dumps(facts, indent=1)}\n\n"
              f"Conversation in #{channel_name(channel)}:\n{convo}\n\nNewest message, from {author}:\n{text}\n\n{rule}")
    reply = grok(prompt)
    if not reply or reply.strip().upper().startswith("SKIP"):
        print(f"  no reply ({'grok unavailable' if not reply else 'SKIP'})")
        return
    if not reply.startswith(PREFIX):
        reply = f"{PREFIX} {reply}"
    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=reply)
    replies_in_thread[thread_ts] += 1
    last_reply_at[thread_ts] = time.time()
    recent_replies.append(time.time())
    print(f"  replied in #{channel_name(channel)}")


@app.event("app_mention")
def on_mention(event):
    print(f"mention from {name_of(event.get('user'))}")
    handle(event, mentioned=True)


@app.event("message")
def on_message(event):
    if f"<@{BOT_USER_ID}>" in event.get("text", ""):
        return  # handled by app_mention
    handle(event, mentioned=False)


if __name__ == "__main__":
    print(f"Ishaan's Grok Bot online as {AUTH['user']} ({MODEL}). Watching #{BOT_CHANNEL}. Ctrl+C to stop.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
