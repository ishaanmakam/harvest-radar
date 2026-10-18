"""Overfield's own accounts: one SQLite file, standard library only.

What lives here: a person's email and password (PBKDF2-HMAC-SHA256, salted), bearer sessions, the farm profile that
becomes the morning roster (same shape as the CSV: tools.ROSTER_COLUMNS plus signup.OPTIONAL_COLUMNS), developer keys
(the key is shown once, only its sha256 is stored, 1000 calls a day by default, at most 5 live keys), acceptances of
the developer terms, the morning delivery log the queue writes, and the counts the admin page shows.

    ACCOUNTS_DB=data/accounts.sqlite          # in .env; the inbound server creates the file; gitignored
    python intelligence/accounts.py promote you@example.com admin     # roles: farmer, officer, admin
    python intelligence/accounts.py list

The HTTP routes are in accounts_api.py and are served by inbound_server.py. Nothing here sends anything.
"""
import argparse
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from providers import LANGUAGE_NAMES
from scheduler import DEFAULT_SEND_TIME, DEFAULT_TIMEZONE
from signup import EMAIL_RE, OPTIONAL_COLUMNS, TIME_RE
from tools import ROSTER_COLUMNS, normalize_phone

PBKDF2_ITERATIONS = 300_000
MIN_PASSWORD = 8
SESSION_DAYS = 30
LOCK_ATTEMPTS = 10
LOCK_MINUTES = 15
MAX_LIVE_KEYS = 5
DEFAULT_DAILY_LIMIT = 1000
ROLES = ("farmer", "officer", "admin")
CHANNELS = ("sms", "voice", "email")
TERMS_VERSION = "developer-v1"
CSV_COLUMNS = ROSTER_COLUMNS + OPTIONAL_COLUMNS

SCHEMA = """
create table if not exists accounts (
  id text primary key, roster_id text unique not null, email text unique not null, password_hash text not null,
  name text not null default '', phone text unique, language text not null default 'en',
  role text not null default 'farmer', farm_id text, address text, lon real, lat real,
  send_time text not null default '06:30', timezone text not null default 'America/Chicago',
  channels text not null default '["sms","voice"]', sms_consent_at text, paused integer not null default 0,
  created_at text not null, updated_at text not null);
create table if not exists sessions (token_hash text primary key, account_id text not null, created_at text not null, expires_at text not null);
create table if not exists login_attempts (email text not null, at text not null);
create table if not exists terms_acceptances (id integer primary key autoincrement, account_id text not null, version text not null,
  kind text not null default 'developer', accepted_at text not null, user_agent text);
create table if not exists api_keys (id text primary key, account_id text not null, name text not null, key_prefix text not null,
  key_hash text unique not null, daily_limit integer not null default 1000, created_at text not null, last_used_at text, revoked_at text);
create table if not exists api_usage (id integer primary key autoincrement, key_id text not null, at text not null, path text not null, status integer not null);
create table if not exists deliveries (id integer primary key autoincrement, run_date text not null, roster_id text not null, farm_id text,
  role text not null, channel text not null, status text not null, detail text, dry_run integer not null default 1, sent_at text, created_at text not null);
create index if not exists idx_sessions_account on sessions (account_id);
create index if not exists idx_usage_key_at on api_usage (key_id, at);
create index if not exists idx_deliveries_run on deliveries (run_date, roster_id);
create index if not exists idx_attempts_email on login_attempts (email, at);
"""


class AuthError(Exception):
    """Wrong credentials or a locked email: the message is safe to show."""


# ---------------------------------------------------------------- passwords and tokens
def hash_password(password, iterations=PBKDF2_ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algo, iterations, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _iso_s(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _clean_time(value):
    text = (value or "").strip() or DEFAULT_SEND_TIME
    m = TIME_RE.match(text)
    if not m:
        raise ValueError("send time must be HH:MM on the 24-hour clock, like 06:30")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _clean_timezone(value):
    text = (value or "").strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(text)
    except Exception:
        raise ValueError(f"timezone must be a name like America/Chicago or Asia/Kolkata, got {text}")
    return text


def _clean_language(value):
    text = ((value or "en").strip().lower() or "en")[:2]
    if text not in LANGUAGE_NAMES:
        raise ValueError(f"language must be one of {', '.join(sorted(LANGUAGE_NAMES))}, got {text}")
    return text


def _clean_channels(value):
    if value is None:
        return ["sms", "voice"]
    chans = [str(c).strip().lower() for c in (value if isinstance(value, (list, tuple)) else str(value).split(","))]
    chans = [c for c in chans if c]
    bad = [c for c in chans if c not in CHANNELS]
    if bad:
        raise ValueError(f"channels must be among {', '.join(CHANNELS)}, got {', '.join(bad)}")
    return chans


def _num(value):
    return "" if value in (None, "") else f"{float(value):.5f}"


# ---------------------------------------------------------------- the store
class Accounts:
    def __init__(self, path, now=None):
        self.path = Path(path)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("pragma journal_mode=wal")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _run(self, sql, params=(), *, fetch=None, commit=False):
        with self._lock:
            cur = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            return cur

    # ---- accounts ----
    def _public(self, row):
        if not row:
            return None
        out = {k: v for k, v in row.items() if k != "password_hash"}
        out["channels"] = json.loads(out.get("channels") or "[]")
        out["paused"] = bool(out.get("paused"))
        return out

    def _next_roster_id(self):
        n = self._run("select count(*) as n from accounts", fetch="one")["n"]
        while True:
            n += 1
            rid = f"R{n:04d}"
            if not self._run("select 1 from accounts where roster_id = ?", (rid,), fetch="one"):
                return rid

    def create_account(self, email, password, name="", role="farmer", **profile):
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValueError(f"That email does not look right: {email or '(empty)'}")
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if self._run("select 1 from accounts where email = ?", (email,), fetch="one"):
            raise ValueError("That email already has an account. Sign in instead.")
        now = _iso_s(self.now())
        aid = uuid.uuid4().hex
        self._run("insert into accounts (id, roster_id, email, password_hash, name, role, send_time, timezone, created_at, updated_at) "
                  "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (aid, self._next_roster_id(), email, hash_password(password), (name or "").strip(), role, DEFAULT_SEND_TIME,
                   DEFAULT_TIMEZONE, now, now), commit=True)
        if profile:
            self.update_profile(aid, profile)
        return self.get_account(aid)

    def get_account(self, account_id):
        return self._public(self._run("select * from accounts where id = ?", (account_id,), fetch="one"))

    def account_by_email(self, email):
        return self._public(self._run("select * from accounts where email = ?", ((email or "").strip().lower(),), fetch="one"))

    def list_accounts(self):
        return [self._public(r) for r in self._run("select * from accounts order by roster_id", fetch="all")]

    def update_profile(self, account_id, patch):
        """The person's own edits. role and roster_id are refused here (admin_update changes them)."""
        if "role" in patch or "roster_id" in patch:
            raise ValueError("role and roster_id can only be changed by an admin")
        return self._apply(account_id, patch)

    def admin_update(self, account_id, patch):
        if "role" in patch and patch["role"] not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        return self._apply(account_id, patch)

    def _apply(self, account_id, patch):
        cur = self._run("select * from accounts where id = ?", (account_id,), fetch="one")
        if not cur:
            raise ValueError("no such account")
        sets, vals = [], []

        def put(col, val):
            sets.append(f"{col} = ?")
            vals.append(val)

        if "name" in patch:
            put("name", (patch["name"] or "").strip())
        if "phone" in patch:
            phone = normalize_phone(patch["phone"] or "") or None
            if phone:
                other = self._run("select id from accounts where phone = ? and id <> ?", (phone, account_id), fetch="one")
                if other:
                    raise ValueError(f"phone {phone} is already used by another account")
            put("phone", phone)
        if "language" in patch:
            put("language", _clean_language(patch["language"]))
        if "send_time" in patch:
            put("send_time", _clean_time(patch["send_time"]))
        if "timezone" in patch:
            put("timezone", _clean_timezone(patch["timezone"]))
        if "channels" in patch:
            put("channels", json.dumps(_clean_channels(patch["channels"])))
        for col in ("farm_id", "address"):
            if col in patch:
                put(col, ((patch[col] or "").strip() or None))
        for col in ("lon", "lat"):
            if col in patch:
                try:
                    put(col, None if patch[col] in (None, "") else float(patch[col]))
                except (TypeError, ValueError):
                    raise ValueError("latitude and longitude must be numbers")
        if "sms_consent" in patch:
            put("sms_consent_at", _iso(self.now()) if patch["sms_consent"] else None)
        elif "sms_consent_at" in patch:
            put("sms_consent_at", patch["sms_consent_at"] or None)
        if "paused" in patch:
            put("paused", 1 if patch["paused"] else 0)
        if "role" in patch:
            put("role", patch["role"])
        put("updated_at", _iso_s(self.now()))
        self._run(f"update accounts set {', '.join(sets)} where id = ?", (*vals, account_id), commit=True)
        return self.get_account(account_id)

    def set_role_by_email(self, email, role):
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        acc = self.account_by_email(email)
        if not acc:
            return False
        self.admin_update(acc["id"], {"role": role})
        return True

    def delete_account(self, account_id):
        for table in ("sessions", "terms_acceptances"):
            self._run(f"delete from {table} where account_id = ?", (account_id,))
        self._run("delete from api_usage where key_id in (select id from api_keys where account_id = ?)", (account_id,))
        self._run("delete from api_keys where account_id = ?", (account_id,))
        self._run("delete from accounts where id = ?", (account_id,), commit=True)

    # ---- sessions ----
    def _attempts(self, email):
        since = _iso_s(self.now() - timedelta(minutes=LOCK_MINUTES))
        return self._run("select count(*) as n from login_attempts where email = ? and at > ?", (email, since), fetch="one")["n"]

    def login(self, email, password):
        email = (email or "").strip().lower()
        if self._attempts(email) >= LOCK_ATTEMPTS:
            raise AuthError(f"too many attempts: try again in {LOCK_MINUTES} minutes")
        row = self._run("select * from accounts where email = ?", (email,), fetch="one")
        if not row or not verify_password(password or "", row["password_hash"]):
            self._run("insert into login_attempts (email, at) values (?, ?)", (email, _iso_s(self.now())), commit=True)
            raise AuthError("wrong email or password")
        self._run("delete from login_attempts where email = ?", (email,))
        token = "ovs_" + secrets.token_hex(32)
        now = self.now()
        self._run("insert into sessions (token_hash, account_id, created_at, expires_at) values (?, ?, ?, ?)",
                  (_sha256(token), row["id"], _iso_s(now), _iso_s(now + timedelta(days=SESSION_DAYS))), commit=True)
        return token

    def account_for_token(self, token):
        if not token:
            return None
        s = self._run("select * from sessions where token_hash = ?", (_sha256(token),), fetch="one")
        if not s or _parse(s["expires_at"]) <= self.now():
            return None
        return self.get_account(s["account_id"])

    def logout(self, token):
        self._run("delete from sessions where token_hash = ?", (_sha256(token or ""),), commit=True)

    # ---- roster ----
    def roster_rows(self):
        """Rows in the CSV shape for everyone who can be sent to: not paused, a phone or an email, and a farm for farmers."""
        rows = []
        for a in self.list_accounts():
            if a["paused"] or not (a.get("phone") or a.get("email")):
                continue
            if a["role"] == "farmer" and not a.get("farm_id"):
                continue
            # the queue knows farmers and officers; an admin is staff and gets the route like an officer
            rows.append({"roster_id": a["roster_id"], "farm_id": a.get("farm_id") or "", "name": a.get("name") or "",
                         "phone": a.get("phone") or "", "language": a.get("language") or "en",
                         "role": "officer" if a["role"] == "admin" else a["role"],
                         "email": a.get("email") or "", "send_time": a.get("send_time") or "", "timezone": a.get("timezone") or "",
                         "address": a.get("address") or "", "lon": _num(a.get("lon")), "lat": _num(a.get("lat")),
                         "created": _iso(_parse(a["created_at"]))})
        return rows

    def pause_by_handle(self, handle):
        phone = normalize_phone(handle or "")
        email = (handle or "").strip().lower() if "@" in (handle or "") else ""
        row = None
        if phone:
            row = self._run("select id from accounts where phone = ?", (phone,), fetch="one")
        if not row and email:
            row = self._run("select id from accounts where email = ?", (email,), fetch="one")
        if not row:
            return False
        self._apply(row["id"], {"paused": True})
        return True

    # ---- terms and keys ----
    def accept_terms(self, account_id, version=TERMS_VERSION, kind="developer", user_agent=""):
        when = _iso(self.now())
        self._run("insert into terms_acceptances (account_id, version, kind, accepted_at, user_agent) values (?, ?, ?, ?, ?)",
                  (account_id, version, kind, when, (user_agent or "")[:300]), commit=True)
        return when

    def terms_accepted(self, account_id, version=TERMS_VERSION, kind="developer"):
        row = self._run("select accepted_at from terms_acceptances where account_id = ? and version = ? and kind = ? "
                        "order by id desc limit 1", (account_id, version, kind), fetch="one")
        return row["accepted_at"] if row else None

    def issue_api_key(self, account_id, name="default"):
        if not self.terms_accepted(account_id):
            raise ValueError(f"accept the developer terms ({TERMS_VERSION}) first")
        live = self._run("select count(*) as n from api_keys where account_id = ? and revoked_at is null", (account_id,), fetch="one")["n"]
        if live >= MAX_LIVE_KEYS:
            raise ValueError(f"at most {MAX_LIVE_KEYS} live keys; revoke one first")
        key = "ovf_" + secrets.token_hex(20)
        self._run("insert into api_keys (id, account_id, name, key_prefix, key_hash, daily_limit, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                  (uuid.uuid4().hex, account_id, ((name or "").strip() or "default")[:60], key[:12], _sha256(key), DEFAULT_DAILY_LIMIT,
                   _iso_s(self.now())), commit=True)
        return key

    def list_keys(self, account_id=None):
        sql = "select id, account_id, name, key_prefix, daily_limit, created_at, last_used_at, revoked_at from api_keys"
        rows = self._run(sql + (" where account_id = ?" if account_id else "") + " order by created_at desc",
                         (account_id,) if account_id else (), fetch="all")
        return rows

    def revoke_api_key(self, account_id, key_id, admin=False):
        cur = self._run("update api_keys set revoked_at = ? where id = ? and revoked_at is null" + ("" if admin else " and account_id = ?"),
                        (_iso_s(self.now()), key_id) + (() if admin else (account_id,)), commit=True)
        return cur.rowcount > 0

    def set_daily_limit(self, key_id, limit):
        self._run("update api_keys set daily_limit = ? where id = ?", (int(limit), key_id), commit=True)

    def verify_api_key(self, key):
        row = self._run("select * from api_keys where key_hash = ? and revoked_at is null", (_sha256(key or ""),), fetch="one")
        if not row:
            return None
        since = _iso_s(self.now() - timedelta(hours=24))
        used = self._run("select count(*) as n from api_usage where key_id = ? and at > ?", (row["id"], since), fetch="one")["n"]
        return {"key_id": row["id"], "account_id": row["account_id"], "daily_limit": row["daily_limit"], "used_today": used}

    def log_api_use(self, key_id, path, status):
        now = _iso_s(self.now())
        self._run("insert into api_usage (key_id, at, path, status) values (?, ?, ?, ?)", (key_id, now, (path or "")[:200], int(status)))
        self._run("update api_keys set last_used_at = ? where id = ?", (now, key_id), commit=True)

    def usage_last_days(self, days=7):
        since = _iso_s(self.now() - timedelta(days=days))
        out = {}
        for r in self._run("select key_id, substr(at, 1, 10) as day, count(*) as n from api_usage where at > ? group by key_id, day",
                           (since,), fetch="all"):
            entry = out.setdefault(r["key_id"], {"total": 0, "by_day": {}})
            entry["total"] += r["n"]
            entry["by_day"][r["day"]] = r["n"]
        return out

    # ---- deliveries ----
    def push_deliveries(self, doc, dry_run, run_date=None):
        """Log one run: the rows dated run_date (else the document's date, else the latest date in it)."""
        rows_all = doc.get("deliveries") or []
        today = run_date or doc.get("date") or max((r.get("date") or "" for r in rows_all), default=None)
        rows = [r for r in rows_all if r.get("date") == today]
        now = _iso_s(self.now())
        for r in rows:
            self._run("insert into deliveries (run_date, roster_id, farm_id, role, channel, status, detail, dry_run, sent_at, created_at) "
                      "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (r["date"], r.get("roster_id") or "", r.get("farm_id") or None, r.get("role") or "", r.get("channel") or "",
                       r.get("status") or "", r.get("detail") or None, 1 if dry_run else 0, r.get("sent_at") or None, now))
        if rows:
            self._run("select 1", commit=True)
        return len(rows)

    def _delivery(self, r):
        r = dict(r)
        r["dry_run"] = bool(r["dry_run"])
        return r

    def deliveries_for(self, account_id, limit=20):
        acc = self.get_account(account_id)
        if not acc:
            return []
        return [self._delivery(r) for r in self._run("select * from deliveries where roster_id = ? order by id desc limit ?",
                                                     (acc["roster_id"], limit), fetch="all")]

    def delivery_runs(self):
        return self._run("select run_date, count(*) as rows, sum(status = 'sent') as sent, sum(status = 'pending') as pending, "
                         "sum(status = 'failed') as failed from deliveries group by run_date order by run_date desc", fetch="all")

    def deliveries_on(self, run_date, limit=2000):
        return [self._delivery(r) for r in self._run("select * from deliveries where run_date = ? order by roster_id, channel limit ?",
                                                     (run_date, limit), fetch="all")]

    # ---- admin ----
    def overview(self):
        accs = self.list_accounts()
        since = _iso_s(self.now() - timedelta(days=7))
        per_day = self._run("select substr(created_at, 1, 10) as day, count(*) as signups from accounts group by day order by day", fetch="all")
        return {"people": len(accs), "farmers": sum(a["role"] == "farmer" for a in accs), "officers": sum(a["role"] == "officer" for a in accs),
                "admins": sum(a["role"] == "admin" for a in accs), "consented": sum(bool(a.get("sms_consent_at")) for a in accs),
                "paused": sum(a["paused"] for a in accs), "in_roster": len(self.roster_rows()),
                "signups_7d": sum(1 for a in accs if a["created_at"] > since), "signups_per_day": per_day,
                "live_keys": sum(1 for k in self.list_keys() if not k["revoked_at"]), "runs": self.delivery_runs()[:30]}

    def export_csv(self):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in self.roster_rows():
            w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        return buf.getvalue()


# ---------------------------------------------------------------- module helpers
def configured(env=None):
    env = os.environ if env is None else env
    return bool(env.get("ACCOUNTS_DB"))


def open_from_env(env=None):
    env = os.environ if env is None else env
    return Accounts(env["ACCOUNTS_DB"]) if configured(env) else None


def push_if_configured(doc, dry_run, env=None, run_date=None):
    db = open_from_env(env)
    return None if db is None else db.push_deliveries(doc, dry_run, run_date=run_date)


def main():
    ap = argparse.ArgumentParser(description="Overfield accounts: list people or set a role.")
    ap.add_argument("command", choices=["list", "promote"])
    ap.add_argument("email", nargs="?")
    ap.add_argument("role", nargs="?", default="admin")
    ap.add_argument("--db", default=None, help="SQLite path (default ACCOUNTS_DB from .env, else data/accounts.sqlite)")
    a = ap.parse_args()
    from common import load_env
    load_env()
    db = Accounts(a.db or os.environ.get("ACCOUNTS_DB") or "data/accounts.sqlite")
    if a.command == "list":
        for acc in db.list_accounts():
            print(f"{acc['roster_id']}  {acc['role']:8} {acc['email']:32} farm {acc.get('farm_id') or '-':6} phone {acc.get('phone') or '-'}")
        print(f"{len(db.list_accounts())} accounts, {len(db.roster_rows())} in the roster")
        return
    if not a.email:
        ap.error("promote needs an email")
    print("updated" if db.set_role_by_email(a.email, a.role) else f"no account for {a.email}")


if __name__ == "__main__":
    main()
