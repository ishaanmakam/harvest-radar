#!/usr/bin/env python3
"""Post a status update to the team Slack channel.

    python scripts/notify.py "A: real timeseries.json merged to main (Sangrur, 24 passes)"

Needs SLACK_WEBHOOK_URL in .env (Slack: create app -> Incoming Webhooks -> add to #harvest-radar).
Silently does nothing if the webhook isn't set, so it never breaks a script.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "intelligence"))
from redact import redact  # noqa: E402
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

url = os.environ.get("SLACK_WEBHOOK_URL")
msg = " ".join(sys.argv[1:]).strip()
if not url or not msg:
    print("notify: skipped (no SLACK_WEBHOOK_URL or empty message)")
    sys.exit(0)
try:
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
except Exception:
    branch = "?"
body = json.dumps({"text": redact(f"[{branch}] {msg}")}).encode()
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10)
    print("notify: sent")
except Exception as e:
    print(f"notify: failed ({e})")
