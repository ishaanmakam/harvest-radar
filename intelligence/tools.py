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
from analytics import farm_outlook
import neighbors

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


def roster_source(path=None):
    """Where the roster comes from: "csv" (a path was given, or ROSTER_PATH is set), "accounts" (ACCOUNTS_DB is set:
    the people who signed up on the site), "csv+accounts" (both, merged), or "none"."""
    load_env()
    csv_on = bool(path or os.environ.get("ROSTER_PATH"))
    db_on = bool(os.environ.get("ACCOUNTS_DB"))
    if csv_on and db_on:
        return "csv+accounts"
    return "csv" if csv_on else ("accounts" if db_on else "none")


def _csv_rows(raw):
    path = Path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"roster not found at {path}; see intelligence/roster.example.csv")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in ROSTER_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"roster is missing columns {missing}")
        return [{k: (v or "").strip() for k, v in r.items()} for r in reader]


def load_roster(path=None, http=None):
    """Rows of the roster, validated. An explicit path or ROSTER_PATH is the CSV (the officer and the demo farms);
    ACCOUNTS_DB adds the people who signed up on the site (see accounts.py). Both together are one roster, so a phone
    may appear only once across them. http is unused and kept for callers that pass it."""
    source = roster_source(path)
    if source == "none":
        raise FileNotFoundError("no roster: set ROSTER_PATH in .env (see intelligence/roster.example.csv) and/or ACCOUNTS_DB "
                                "(see backend/README.md)")
    rows = []
    if "csv" in source:
        rows += _csv_rows(str(path) if path else os.environ.get("ROSTER_PATH"))
    if "accounts" in source:
        import accounts
        rows += accounts.Accounts(os.environ["ACCOUNTS_DB"]).roster_rows()
    return validate_rows(rows)


def validate_rows(rows):
    """The same checks for every roster source: ids unique, phones unique and normalised, roles known, language set,
    farmers with a farm."""
    seen, phones = set(), set()
    for i, r in enumerate(rows, start=2):
        where = f"roster line {i} ({r.get('roster_id') or '?'})"
        if not r["roster_id"] or r["roster_id"] in seen:
            raise ValueError(f"{where}: roster_id missing or duplicated")
        seen.add(r["roster_id"])
        phone = normalize_phone(r["phone"])
        if phone:
            if phone in phones:
                raise ValueError(f"{where}: phone {phone} is already used by another row (a caller must map to one row)")
            phones.add(phone)
        if r["role"] not in ROLES:
            raise ValueError(f"{where}: role must be one of {ROLES}, got {r['role']!r}")
        if not r["language"]:
            raise ValueError(f"{where}: language is required on every row")
        if r["role"] == "farmer" and not r["farm_id"]:
            raise ValueError(f"{where}: a farmer row needs a farm_id")
        r["phone"] = normalize_phone(r["phone"])
    return rows


def resolve_caller(handle: str, roster):
    """Roster row for a caller ID / iMessage handle, or None (the handler then says UNKNOWN_CALLER_MESSAGE).
    A number given with a country code (+..., or more than ten digits) must equal a roster phone exactly; a
    national-format number (ten digits or fewer, no +) matches on its ten-digit tail, and only if exactly one row
    matches. Email-style handles never match a phone number."""
    raw = (handle or "").strip()
    if not raw or "@" in raw or re.search(r"[A-Za-z]", raw):
        return None
    h = normalize_phone(raw)
    if not h:
        return None
    if h.startswith("+"):
        matches = [r for r in roster if r["phone"] == h]
    else:
        tail = h[-10:]
        matches = [r for r in roster if r["phone"] and r["phone"].lstrip("+")[-10:] == tail]
    return matches[0] if len(matches) == 1 else None


def _norm_farm(fid):
    return fid.strip().upper() if isinstance(fid, str) else fid


class Tools:
    """get_plan / get_route / get_change over data_dir, scoped by `caller` (a roster row, or None = unknown)."""

    def __init__(self, data_dir, caller, roster=None):
        self.data_dir = Path(data_dir)
        self.caller = caller
        self.roster = roster
        self._plans = None
        self._route = None
        self._analytics = None

    # ---- scope ----
    @property
    def role(self):
        return self.caller["role"] if self.caller else None

    def _farm(self, farm_id):
        farm_id = _norm_farm(farm_id)
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

    def analytics(self):
        if self._analytics is None:
            p = self.data_dir / "analytics.json"
            self._analytics = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return self._analytics

    def get_outlook(self, farm_id=None):
        """The analytics layer's outlook for a farm beyond today's plan: yield band, harvest now or wait, the chance to
        finish before the rain, soil water, anything unusual versus the neighbours. Same scope rules as get_plan;
        empty lines with a note when the analytics have not run for this season."""
        fid = self._farm(farm_id)
        doc = self.analytics()
        lines = farm_outlook(doc, fid) if doc else []
        note = neighbors.sentence(fid, self.data_dir, self.roster)
        if note:
            lines = list(lines) + [note]
        if not doc:
            out = {"farm_id": fid, "as_of": None, "lines": lines}
            if not lines:
                out["note"] = "no analytics for this season yet"
            return out
        out = {"farm_id": fid, "as_of": doc.get("as_of"), "lines": lines, "detail": doc.get("fields", {}).get(fid, {})}
        if not lines:
            out["note"] = "nothing beyond today's plan for this farm"
        return out

    def get_change(self, farm_id=None):
        """What changed on the farm since the previous clear pass: the visible_change check, with the plan's date."""
        plan = self.get_plan(farm_id)
        ch = next(c for c in plan["checks"] if c["kind"] == "visible_change")
        return {"farm_id": plan["farm_id"], "as_of": plan["as_of"], "source": plan["source"], "status": ch["status"],
                "detail": ch["detail"], "where": ch["where"], "window": ch["window"]}
