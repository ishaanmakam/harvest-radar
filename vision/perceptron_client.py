"""Thin wrapper around the Perceptron API with budget tracking and annotation parsing.

Docs: https://docs.perceptron.inc  (SDK: pip install "perceptron>=0.4.0")
Auth: PERCEPTRON_API_KEY in .env
Model: PERCEPTRON_MODEL (default perceptron-mk1.5; the sponsor card names the Mk1 family,
       so if judges care, pin "perceptron-mk1" — confirm with the Perceptron booth).

Facts from the docs (verified 2026-10-17):
  - request annotations with vision_config={"annotation_format": "point"|"box"|"polygon"|"clip"}
  - annotations come back as markup inside message.content, e.g.
        <polygon mention="field" asset_idx="0"> (20,40) (60,40) (60,80) (20,80) </polygon>
        <point_box mention="burn scar"> (100,150) (300,350) </point_box>
        <clip mention="harvest" asset_idx="0" t="1.0 seconds 4.5 seconds" />
  - coordinates are on a normalized 0-1000 grid, origin top-left, y down
  - price: $0.15 / 1M input tokens, $1.50 / 1M output tokens
"""
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEND_FILE = ROOT / "data" / "out" / "perceptron" / "spend.json"
PRICE_IN, PRICE_OUT = 0.15 / 1e6, 1.50 / 1e6


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
MODEL = os.environ.get("PERCEPTRON_MODEL", "perceptron-mk1.5")
BUDGET = float(os.environ.get("PERCEPTRON_BUDGET_USD", "40"))


def spent_usd():
    if SPEND_FILE.exists():
        return json.loads(SPEND_FILE.read_text())["usd"]
    return 0.0


def _record(usage, label):
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(SPEND_FILE.read_text()) if SPEND_FILE.exists() else {"usd": 0.0, "calls": []}
    pin = getattr(usage, "prompt_tokens", 0) or 0
    pout = getattr(usage, "completion_tokens", 0) or 0
    cost = pin * PRICE_IN + pout * PRICE_OUT
    data["usd"] = round(data["usd"] + cost, 5)
    data["calls"].append({"t": time.strftime("%H:%M:%S"), "label": label, "in": pin, "out": pout, "usd": round(cost, 5)})
    SPEND_FILE.write_text(json.dumps(data, indent=1))
    return cost


def ask(media, prompt, annotation_format=None, label="call", max_tokens=2048, reasoning_effort=None):
    """media: list of local paths (images .jpg/.png or videos .mp4). Returns (text, cost_usd)."""
    if spent_usd() >= BUDGET:
        raise RuntimeError(f"Perceptron budget cap reached (${BUDGET}). Raise PERCEPTRON_BUDGET_USD deliberately.")
    from perceptron import Client, image, video  # imported lazily so the rest of the repo runs without the SDK

    client = Client(api_key=os.environ["PERCEPTRON_API_KEY"])
    parts = []
    for m in media:
        parts.append(video(str(m)) if str(m).lower().endswith((".mp4", ".mov", ".webm")) else image(str(m)))
    parts.append({"type": "text", "text": prompt})
    kwargs = dict(model=MODEL, messages=[{"role": "user", "content": parts}], max_completion_tokens=max_tokens)
    if annotation_format:
        kwargs["vision_config"] = {"annotation_format": annotation_format}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    resp = client.chat.completions.create(**kwargs)
    cost = _record(resp.usage, label)
    choice = resp.choices[0]
    text = choice.message.content or ""
    if choice.finish_reason != "stop":
        print(f"  WARN finish_reason={choice.finish_reason} (answer may be cut off)")
    print(f"  [{label}] ${cost:.4f}  (total ${spent_usd():.3f} of ${BUDGET})")
    return text, cost


# ---------------- annotation parsing ----------------
_PAIR = re.compile(r"\((-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\)")
_TAG = re.compile(r"<(polygon|point_box|point)([^>]*)>(.*?)</\1>", re.S)
_CLIP = re.compile(r"<clip([^>]*)/>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def parse_annotations(text):
    """Return list of dicts: {kind, mention, asset_idx, coords:[(x,y)...]} or clips {kind:'clip', mention, t:[s] or [s,e]}."""
    out = []
    for kind, attrs, body in _TAG.findall(text):
        a = dict(_ATTR.findall(attrs))
        out.append({"kind": kind, "mention": a.get("mention"), "asset_idx": int(a.get("asset_idx", 0) or 0),
                    "coords": [(float(x), float(y)) for x, y in _PAIR.findall(body)]})
    for attrs in _CLIP.findall(text):
        a = dict(_ATTR.findall(attrs))
        secs = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*seconds", a.get("t", ""))]
        out.append({"kind": "clip", "mention": a.get("mention"), "asset_idx": int(a.get("asset_idx", 0) or 0), "t": secs})
    return out


def norm_to_lonlat(x, y, bounds):
    """0-1000 normalized image coords -> lon/lat, for north-up images covering bounds=[W,S,E,N]."""
    w, s, e, n = bounds
    return w + (x / 1000.0) * (e - w), n - (y / 1000.0) * (n - s)


def clean_text(text):
    """Strip markup so the remaining prose can be shown in the UI."""
    text = _TAG.sub("", text)
    text = _CLIP.sub("", text)
    text = re.sub(r"</?collection[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()
