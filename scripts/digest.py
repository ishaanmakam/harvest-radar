#!/usr/bin/env python3
"""Team digest bot: summarizes recent repo activity with Grok and posts it to Slack.

Runs hourly from .github/workflows/digest.yml (on GitHub's servers), or locally:
    python scripts/digest.py --hours 3 --force          # post now
    python scripts/digest.py --hours 3 --dry-run        # print only, don't post

Env (GitHub repo secrets, or .env locally):
    XAI_API_KEY        Grok API key (if missing or the call fails, posts a plain digest instead)
    XAI_MODEL          default grok-4.7
    SLACK_WEBHOOK_URL  Incoming webhook for #bot-channel
    GITHUB_TOKEN, GITHUB_REPOSITORY   provided automatically inside GitHub Actions (for PR info)
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "intelligence"))
from redact import redact  # noqa: E402
DEADLINE = datetime(2026, 10, 18, 17, 30, tzinfo=timezone.utc)  # Sun Oct 18, 10:30 AM PT
ROLES = [  # matched against branch names, first hit wins
    (("main",), "main (merged work)"),
    (("a/", "pipeline"), "A (satellite pipeline)"),
    (("b/", "intel", "model", "optim"), "B (models + optimization, Ishaan)"),
    (("c/", "vision", "perceptron"), "C (Perceptron vision)"),
    (("web", "ui", "frontend"), "Web app (whole team)"),
]

env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def sh(*args):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def http_json(url, data=None, headers=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def role_of(branch):
    for keys, name in ROLES:
        if branch == "main" and keys == ("main",):
            return name
        if keys != ("main",) and any(k in branch for k in keys):
            return name
    return branch


def gather(hours):
    since = f"{int(hours * 60)} minutes ago"
    sh("git", "fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*")
    branches = [b.strip() for b in sh("git", "branch", "-r", "--format=%(refname:short)").splitlines()
                if b.strip() and not b.strip().endswith("/HEAD")]
    activity = {}
    for b in branches:
        rng = b if b.endswith("/main") else f"origin/main..{b}"  # branch-only commits
        log = sh("git", "log", rng, f"--since={since}", "--no-merges", "--pretty=%an: %s")
        if log:
            short = b.split("/", 1)[1] if "/" in b else b
            activity[short] = {"role": role_of(short), "commits": [redact(line) for line in log.splitlines()[:15]]}

    prs = {"open": [], "merged_recently": []}
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if repo and token:
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        try:
            for p in http_json(f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=20", headers=h):
                prs["open"].append(f"#{p['number']} {p['title']} ({p['head']['ref']} by {p['user']['login']})")
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            for p in http_json(f"https://api.github.com/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=20", headers=h):
                if p.get("merged_at") and datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")) >= cutoff:
                    prs["merged_recently"].append(f"#{p['number']} {p['title']} ({p['head']['ref']})")
        except Exception as e:
            prs["error"] = str(e)

    checks = {}
    for d in ("data/mock", "data/out"):
        if (ROOT / d / "fields.geojson").exists():
            r = subprocess.run([sys.executable, "scripts/validate_contracts.py", d], cwd=ROOT, capture_output=True, text=True)
            checks[d] = "valid" if r.returncode == 0 else "FAILING: " + r.stdout.strip().splitlines()[-1][:200]

    metric = None
    for d in ("data/out", "data/mock"):
        p = ROOT / d / "routing.json"
        if p.exists():
            s = json.loads(p.read_text())["summary"]
            metric = (f"{d}: optimized serves {s['optimized']['pct_served']}% vs baseline {s['baseline']['pct_served']}% "
                      f"of machine-dependent area inside the sowing window")
            break

    real_data = sorted(p.name for p in (ROOT / "data" / "out").glob("*.json*")) if (ROOT / "data" / "out").exists() else []
    hours_left = max(0, (DEADLINE - datetime.now(timezone.utc)).total_seconds() / 3600)
    return {"window_hours": hours, "hours_to_deadline": round(hours_left, 1), "activity": activity, "pull_requests": prs,
            "contract_checks": checks, "hero_metric": metric, "real_data_files_on_main": real_data}


PROMPT = """You are the project manager bot for "Harvest Radar", a 3-person DubHacks team. Submissions are due Sun Oct 18 10:30 AM PT.
Product: satellite imagery of Punjab rice fields -> harvest-date predictions -> residue-machine demand forecast -> dispatch plan, plus Perceptron reading the satellite time-lapse, shown in a web map.
Roles: A = satellite pipeline (pipeline/, writes data/out/*), B = models + optimization (intelligence/, Ishaan), C = Perceptron vision (vision/), web app built by everyone from ~10 PM.
Dependencies: B and C need A's real data in data/out/ on main; the web app needs everyone's JSON outputs.

Here is the activity from the last {hours} hours as JSON:
{facts}

Write a Slack update under 140 words, plain Slack formatting (*bold* allowed, no headers, no tables):
1. One line per person/area: what they shipped (or "no commits" if nothing).
2. Risks or blockers you can infer (failing contract checks, nothing merged to main, B/C waiting on A's data, time left).
3. "Next:" one concrete action for each of A, B, C.
Be factual; do not invent work that isn't in the data. Start with the hours left."""


def ask_grok(facts):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return None
    model = os.environ.get("XAI_MODEL", "grok-4.7")
    prompt = PROMPT.format(hours=facts["window_hours"], facts=json.dumps(facts, indent=1))
    headers = {"Authorization": f"Bearer {key}"}
    try:
        r = http_json("https://api.x.ai/v1/responses", {"model": model, "input": prompt}, headers)
        if isinstance(r, dict):
            if r.get("output_text"):
                return r["output_text"].strip()
            texts = [c.get("text", "") for o in r.get("output", []) for c in (o.get("content") or []) if isinstance(c, dict)]
            if any(texts):
                return "\n".join(t for t in texts if t).strip()
    except Exception as e:
        print(f"responses endpoint failed ({e}); trying chat completions")
    try:
        r = http_json("https://api.x.ai/v1/chat/completions",
                      {"model": model, "messages": [{"role": "user", "content": prompt}]}, headers)
        return r["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Grok call failed: {e}")
        return None


def plain(facts):
    lines = [f"*{facts['hours_to_deadline']}h to deadline* | last {facts['window_hours']}h:"]
    for b, a in facts["activity"].items():
        lines.append(f"- {a['role']} [{b}]: {len(a['commits'])} commits, latest: {a['commits'][0]}")
    if not facts["activity"]:
        lines.append("- no commits on any branch")
    for d, c in facts["contract_checks"].items():
        lines.append(f"- contracts {d}: {c}")
    if facts["hero_metric"]:
        lines.append(f"- {facts['hero_metric']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.1)
    ap.add_argument("--force", action="store_true", help="post even if nothing changed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    force = args.force or os.environ.get("FORCE_DIGEST") == "true"

    facts = gather(args.hours)
    if not facts["activity"] and not facts["pull_requests"]["merged_recently"] and not force:
        print("No activity in window; not posting.")
        return
    text = redact(ask_grok(facts) or plain(facts))
    text = ":satellite: *Harvest Radar digest*\n" + text
    print(text)
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if args.dry_run or not url:
        print("(not posted: dry run or no SLACK_WEBHOOK_URL)")
        return
    http_json(url, {"text": text})
    print("posted to Slack")


if __name__ == "__main__":
    main()
