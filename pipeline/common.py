"""Shared helpers for the season pipeline. Owner: Person A."""
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

# Child steps of pipeline/run.py set these. Unset, every script uses the files it always has.
OUT = Path(os.environ["HR_OUT"]) if os.environ.get("HR_OUT") else ROOT / "data" / "out"


def load_region() -> dict:
    region = json.loads((ROOT / "config" / "region.json").read_text())
    override = os.environ.get("HR_AS_OF", "").strip()
    if override:
        date.fromisoformat(override)
        region["as_of"] = override
    return region


def write_json(path: Path, obj, pretty: bool = False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(obj, indent=2) + "\n"
    else:
        text = json.dumps(obj, separators=(",", ":"))
    path.write_text(text)


def log(step: str, msg: str):
    print(f"[{step}] {msg}", flush=True)
