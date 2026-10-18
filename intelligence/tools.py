"""Shared tools for the Grok Voice and Photon handlers, scoped by the caller's roster row.
Spec: "Tools shared by Grok Voice and Photon: get_plan(farm_id), get_route(), get_change(farm_id). A farmer session
may call these only with that row's farm_id. An officer session may call them for any farm."

Roster: a CSV outside git (path in .env as ROSTER_PATH; see roster.example.csv) with columns
roster_id, farm_id (empty for the officer), name, phone, language, role (farmer | officer). Language is required.
Everything here is read-only over the finished plan files; nothing calls a provider.
"""
import csv
import json
import os
import re
from pathlib import Path

from common import load_env

KINDS = ["harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change"]
ROSTER_COLUMNS = ["roster_id", "farm_id", "name", "phone", "language", "role"]
ROLES = ("farmer", "officer")
UNKNOWN_CALLER_MESSAGE = "No farm is registered for this number."


def normalize_phone(phone: str) -> str:
    """Digits only, with a leading + when the number was given with one (or with a country code)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    return "+" + digits if (phone.strip().startswith("+") or len(digits) > 10) else digits


def load_roster(path=None):
    """Rows of the roster CSV, validated. path defaults to ROSTER_PATH from the environment / .env."""
    load_env()
    path = Path(path or os.environ.get("ROSTER_PATH") or "")
    if not str(path) or not path.exists():
        raise FileNotFoundError(f"roster not found at {path or '<ROSTER_PATH unset>'}; see intelligence/roster.example.csv")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in ROSTER_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"roster is missing columns {missing}")
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in reader]
    seen = set()
    for i, r in enumerate(rows, start=2):
        where = f"roster line {i} ({r.get('roster_id') or '?'})"
        if not r["roster_id"] or r["roster_id"] in seen:
            raise ValueError(f"{where}: roster_id missing or duplicated")
        seen.add(r["roster_id"])
        if r["role"] not in ROLES:
            raise ValueError(f"{where}: role must be one of {ROLES}, got {r['role']!r}")
        if not r["language"]:
            raise ValueError(f"{where}: language is required on every row")
        if r["role"] == "farmer" and not r["farm_id"]:
            raise ValueError(f"{where}: a farmer row needs a farm_id")
        r["phone"] = normalize_phone(r["phone"])
    return rows


def resolve_caller(handle: str, roster):
    """Roster row for a caller ID / iMessage handle, or None (the handler then says UNKNOWN_CALLER_MESSAGE)."""
    h = normalize_phone(handle)
    if not h:
        return None
    tail = h.lstrip("+")[-10:]
    for r in roster:
        p = r["phone"]
        if p and (p == h or p.lstrip("+")[-10:] == tail):
            return r
    return None


class Tools:
    """get_plan / get_route / get_change over data_dir, scoped by `caller` (a roster row, or None = unknown)."""

    def __init__(self, data_dir, caller):
        self.data_dir = Path(data_dir)
        self.caller = caller
        self._plans = None
        self._route = None

    # ---- scope ----
    @property
    def role(self):
        return self.caller["role"] if self.caller else None

    def _farm(self, farm_id):
        if self.caller is None:
            raise PermissionError(UNKNOWN_CALLER_MESSAGE)
        if self.role == "farmer":
            own = self.caller["farm_id"]
            if farm_id is None:
                return own
            if farm_id != own:
                raise PermissionError("A farmer session can only ask about its own farm.")
            return own
        if farm_id is None:
            raise ValueError("farm_id is required for an officer session")
        return farm_id

    # ---- files ----
    def plans(self):
        if self._plans is None:
            doc = json.loads((self.data_dir / "plans.json").read_text(encoding="utf-8"))
            self._plans = {p["farm_id"]: p for p in doc["plans"]}
        return self._plans

    def route(self):
        if self._route is None:
            self._route = json.loads((self.data_dir / "route.json").read_text(encoding="utf-8"))
        return self._route

    # ---- the three shared tools ----
    def get_plan(self, farm_id=None):
        fid = self._farm(farm_id)
        plans = self.plans()
        if fid not in plans:
            raise KeyError(f"no plan for farm {fid}")
        return plans[fid]

    def get_route(self):
        if self.caller is None:
            raise PermissionError(UNKNOWN_CALLER_MESSAGE)
        if self.role != "officer":
            raise PermissionError("Only the officer session can read the route.")
        return self.route()

    def get_change(self, farm_id=None):
        """What changed on the farm since the previous clear pass: the visible_change check, with the plan's date."""
        plan = self.get_plan(farm_id)
        ch = next(c for c in plan["checks"] if c["kind"] == "visible_change")
        return {"farm_id": plan["farm_id"], "as_of": plan["as_of"], "source": plan["source"], "status": ch["status"],
                "detail": ch["detail"], "where": ch["where"], "window": ch["window"]}
