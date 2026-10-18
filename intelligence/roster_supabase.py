"""The roster from Supabase, and the morning log written back.

When SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set, the people who signed up on the site are the roster:
`profiles` rows that are not paused, can receive something (a phone or an email) and, for farmers, have a farm.
They come back in the shape of the roster CSV (tools.ROSTER_COLUMNS plus signup.OPTIONAL_COLUMNS), so the queue,
the scheduler and the inbound handlers do not change. The service role key bypasses row-level security; it lives
in .env only. Design: docs/superpowers/specs/2026-10-18-product-accounts-api-design.md, backend/README.md.
"""
import json
import os
import urllib.parse
from datetime import datetime, timezone

from providers import _urllib_http

ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
SELECT = "roster_id,farm_id,name,phone,email,language,role,send_time,timezone,address,lon,lat,created_at"
TIMEOUT_S = 30


def configured(env=None):
    env = os.environ if env is None else env
    return bool(env.get(ENV[0]) and env.get(ENV[1]))


def _headers(key, **extra):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Overfield-queue/1"}
    h.update(extra)
    return h


def _hhmm(value):
    """'06:30:00' or '06:30' -> '06:30'; None -> ''."""
    text = (value or "").strip()
    return text[:5] if len(text) >= 5 and text[2] == ":" else ""


def _num(value):
    return "" if value in (None, "") else f"{float(value):.5f}"


def _created(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def fetch_roster(url, key, http=None):
    """Roster rows (CSV shape, strings only) from the profiles table. Raises RuntimeError with a plain sentence."""
    from tools import normalize_phone                                     # tools imports this module lazily
    http = http or _urllib_http
    query = urllib.parse.urlencode({"select": SELECT, "paused": "eq.false", "order": "roster_id.asc"}, safe=".,")
    status, body = http("GET", f"{url.rstrip('/')}/rest/v1/profiles?{query}", _headers(key), None, TIMEOUT_S)
    if status != 200:
        raise RuntimeError(f"Supabase roster read failed ({status}): {(body or b'')[:200].decode(errors='replace')}")
    rows = []
    for p in json.loads(body or b"[]"):
        phone = normalize_phone(p.get("phone") or "")
        email = (p.get("email") or "").strip().lower()
        role = (p.get("role") or "farmer").strip() or "farmer"
        farm_id = (p.get("farm_id") or "").strip()
        if not phone and not email:
            continue                                                       # nothing to send to
        if role == "farmer" and not farm_id:
            continue                                                       # the account page is not finished yet
        rows.append({"roster_id": (p.get("roster_id") or "").strip(), "farm_id": farm_id, "name": (p.get("name") or "").strip(),
                     "phone": phone, "language": ((p.get("language") or "en").strip().lower() or "en")[:2], "role": role,
                     "email": email, "send_time": _hhmm(p.get("send_time")), "timezone": (p.get("timezone") or "").strip(),
                     "address": (p.get("address") or "").strip(), "lon": _num(p.get("lon")), "lat": _num(p.get("lat")),
                     "created": _created(p.get("created_at"))})
    return rows


def push_deliveries(url, key, doc, dry_run, http=None):
    """Write this run's rows of a deliveries document to the deliveries table. Returns how many were written."""
    http = http or _urllib_http
    today = doc.get("date")
    rows = [{"run_date": r["date"], "roster_id": r.get("roster_id") or "", "farm_id": r.get("farm_id") or None,
             "role": r.get("role") or "", "channel": r.get("channel") or "", "status": r.get("status") or "",
             "detail": r.get("detail") or None, "dry_run": bool(dry_run), "sent_at": r.get("sent_at") or None}
            for r in doc.get("deliveries") or [] if r.get("date") == today]
    if not rows:
        return 0
    status, body = http("POST", f"{url.rstrip('/')}/rest/v1/deliveries", _headers(key, Prefer="return=minimal"),
                        json.dumps(rows).encode("utf-8"), TIMEOUT_S)
    if status not in (200, 201, 204):
        raise RuntimeError(f"Supabase delivery log write failed ({status}): {(body or b'')[:200].decode(errors='replace')}")
    return len(rows)


def push_if_configured(doc, dry_run, env=None, http=None):
    """push_deliveries when the two variables are set, else None (the CSV world has no log to write)."""
    env = os.environ if env is None else env
    if not configured(env):
        return None
    return push_deliveries(env[ENV[0]], env[ENV[1]], doc, dry_run, http=http)
