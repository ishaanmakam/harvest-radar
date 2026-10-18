"""HTTP routes for Overfield's own accounts, served inside inbound_server.py. Pure dispatch: (method, path, headers,
body) in, (status, headers, body) out, so tests need no sockets.

Sessions: POST /auth/signup {email, password, name}; POST /auth/login {email, password}; both answer {token, account}.
          GET /auth/me; POST /auth/logout. The token goes in Authorization: Bearer ovs_...
Profile:  PATCH /me (name, address, lon, lat, farm_id, phone, language, send_time, timezone, channels, sms_consent,
          paused); a point without a farm_id is matched to the nearest watched field. GET /me/deliveries. POST /me/delete.
Keys:     GET /terms, POST /terms/accept, GET /keys, POST /keys {name} (the key is in the answer once), DELETE /keys/{id}.
Admin:    officers and admins read GET /admin/overview, /admin/accounts, /admin/deliveries[?run_date], /admin/keys,
          /admin/export.csv; admins PATCH /admin/accounts/{id} {role, paused}.
API:      GET /v1/health (no key); with Authorization: Bearer ovf_... or ?key=: /v1/plans, /v1/farms/{id}/plan, /v1/route,
          /v1/landscape, /v1/sources. 401 without a live key, 429 past the key's daily limit, 503 while a file is missing.
Every answer carries CORS headers for the static site.
"""
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import accounts as ac
import signup
from common import read_json

SIGNUPS_PER_IP = 30
ATTRIBUTION = "Overfield (ishaanmakam/harvest-radar). Imagery: Copernicus Sentinel-2. Weather: Open-Meteo. Yields: USDA NASS."
LICENCE = "Plans and analytics: CC BY 4.0. Underlying sources keep their own terms; see /v1/sources."
ENDPOINTS = ["/v1/plans", "/v1/farms/{farm_id}/plan", "/v1/route", "/v1/landscape", "/v1/sources"]
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS", "Access-Control-Max-Age": "600"}


@dataclass
class Context:
    accounts: ac.Accounts
    data_dir: Path
    sources_path: Path = None
    now: callable = None
    signups_by_ip: dict = field(default_factory=dict)
    _fields: dict = field(default_factory=dict)

    def fields(self):
        if "doc" not in self._fields:
            self._fields["doc"] = read_json(self.data_dir, "fields.geojson")
        return self._fields["doc"]

    def time(self):
        return (self.now or (lambda: datetime.now(timezone.utc)))()


class Reply(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def centroid(geometry):
    """Mean of the outer ring, enough to land a test point inside a field."""
    coords = geometry["coordinates"]
    ring = coords[0][0] if geometry["type"] == "MultiPolygon" else coords[0]
    lon = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    return lon, lat


def _json_body(body):
    if not body:
        return {}
    try:
        doc = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, UnicodeDecodeError):
        raise Reply(400, "the body must be JSON")
    if not isinstance(doc, dict):
        raise Reply(400, "the body must be a JSON object")
    return doc


def _bearer(headers):
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    m = re.match(r"^\s*Bearer\s+(\S+)\s*$", auth, re.I)
    return m.group(1) if m else None


def _envelope(as_of, data):
    return {"served_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "as_of": as_of, "attribution": ATTRIBUTION,
            "licence": LICENCE, "data": data}


def _file(ctx, name):
    try:
        return read_json(ctx.data_dir, name)
    except Exception:
        raise Reply(503, f"{name} is not published yet")


# ---------------------------------------------------------------- handlers
def _need_account(ctx, headers):
    token = _bearer(headers)
    acc = ctx.accounts.account_for_token(token) if token and token.startswith("ovs_") else None
    if not acc:
        raise Reply(401, "sign in first")
    return acc


def _need_staff(ctx, headers, admin_only=False):
    acc = _need_account(ctx, headers)
    if acc["role"] not in ("officer", "admin") or (admin_only and acc["role"] != "admin"):
        raise Reply(403, "admins only" if admin_only else "officers and admins only")
    return acc


def _auth(ctx, method, path, headers, body, ip):
    db = ctx.accounts
    if path == "/auth/signup" and method == "POST":
        ctx.signups_by_ip[ip] = ctx.signups_by_ip.get(ip, 0) + 1
        if ctx.signups_by_ip[ip] > SIGNUPS_PER_IP:
            raise Reply(429, "too many sign-ups from this address; try again later")
        doc = _json_body(body)
        try:
            acc = db.create_account(email=doc.get("email"), password=doc.get("password"), name=doc.get("name") or "")
        except ValueError as e:
            raise Reply(409 if "already has an account" in str(e) else 400, str(e))
        return 200, {"token": db.login(acc["email"], doc.get("password")), "account": acc}
    if path == "/auth/login" and method == "POST":
        doc = _json_body(body)
        try:
            token = db.login(doc.get("email"), doc.get("password"))
        except ac.AuthError as e:
            raise Reply(429 if "too many" in str(e) else 401, str(e))
        return 200, {"token": token, "account": db.account_by_email(doc.get("email"))}
    if path == "/auth/me" and method == "GET":
        return 200, {"account": _need_account(ctx, headers)}
    if path == "/auth/logout" and method == "POST":
        db.logout(_bearer(headers))
        return 200, {"ok": True}
    raise Reply(404, "not found")


def _me(ctx, method, path, headers, body):
    db = ctx.accounts
    acc = _need_account(ctx, headers)
    if path == "/me" and method == "PATCH":
        doc = _json_body(body)
        match = None
        lon, lat = doc.get("lon"), doc.get("lat")
        if lon not in (None, "") and lat not in (None, "") and not doc.get("farm_id"):
            try:
                lon, lat = float(lon), float(lat)
            except (TypeError, ValueError):
                raise Reply(400, "latitude and longitude must be numbers")
            match = signup.match_field(lon, lat, ctx.fields())
            if not match:
                raise Reply(400, f"Overfield does not cover that location yet: the nearest watched field is more than "
                                 f"{signup.MAX_MATCH_KM:.0f} km away.")
            doc["farm_id"] = match["field_id"]
        try:
            updated = db.update_profile(acc["id"], doc)
        except ValueError as e:
            raise Reply(400, str(e))
        return 200, {"account": updated, "field": match}
    if path == "/me/deliveries" and method == "GET":
        return 200, {"deliveries": db.deliveries_for(acc["id"])}
    if path == "/me/delete" and method == "POST":
        db.delete_account(acc["id"])
        return 200, {"ok": True}
    raise Reply(404, "not found")


def _keys(ctx, method, path, headers, body):
    db = ctx.accounts
    acc = _need_account(ctx, headers)
    if path == "/terms" and method == "GET":
        return 200, {"accepted": db.terms_accepted(acc["id"]), "version": ac.TERMS_VERSION}
    if path == "/terms/accept" and method == "POST":
        doc = _json_body(body)
        when = db.accept_terms(acc["id"], doc.get("version") or ac.TERMS_VERSION, doc.get("kind") or "developer",
                               headers.get("User-Agent") or headers.get("user-agent") or "")
        return 200, {"accepted_at": when, "version": doc.get("version") or ac.TERMS_VERSION}
    if path == "/keys" and method == "GET":
        usage = db.usage_last_days(7)
        keys = [{**k, "usage_7d": usage.get(k["id"], {}).get("total", 0)} for k in db.list_keys(acc["id"])]
        return 200, {"keys": keys}
    if path == "/keys" and method == "POST":
        doc = _json_body(body)
        try:
            key = db.issue_api_key(acc["id"], doc.get("name") or "default")
        except ValueError as e:
            raise Reply(400, str(e))
        return 200, {"key": key, "key_prefix": key[:12]}
    m = re.match(r"^/keys/([0-9a-f]{32})$", path)
    if m and method == "DELETE":
        return 200, {"revoked": db.revoke_api_key(acc["id"], m.group(1), admin=acc["role"] == "admin")}
    raise Reply(404, "not found")


def _admin(ctx, method, path, query, headers, body):
    db = ctx.accounts
    if path == "/admin/overview" and method == "GET":
        _need_staff(ctx, headers)
        return 200, db.overview()
    if path == "/admin/accounts" and method == "GET":
        _need_staff(ctx, headers)
        return 200, {"accounts": db.list_accounts()}
    m = re.match(r"^/admin/accounts/([0-9a-f]{32})$", path)
    if m and method == "PATCH":
        _need_staff(ctx, headers, admin_only=True)
        doc = _json_body(body)
        patch = {k: doc[k] for k in ("role", "paused", "name", "farm_id", "language", "send_time", "timezone", "channels") if k in doc}
        try:
            return 200, {"account": db.admin_update(m.group(1), patch)}
        except ValueError as e:
            raise Reply(400, str(e))
    if path == "/admin/deliveries" and method == "GET":
        _need_staff(ctx, headers)
        runs = db.delivery_runs()
        run_date = (query.get("run_date") or [None])[0] or (runs[0]["run_date"] if runs else None)
        return 200, {"runs": runs, "run_date": run_date, "rows": db.deliveries_on(run_date) if run_date else []}
    if path == "/admin/keys" and method == "GET":
        _need_staff(ctx, headers)
        usage = db.usage_last_days(7)
        emails = {a["id"]: a["email"] for a in db.list_accounts()}
        keys = [{**k, "email": emails.get(k["account_id"], ""), "usage_7d": usage.get(k["id"], {}).get("total", 0),
                 "by_day": usage.get(k["id"], {}).get("by_day", {})} for k in db.list_keys()]
        return 200, {"keys": keys}
    if path == "/admin/export.csv" and method == "GET":
        _need_staff(ctx, headers)
        return 200, ("text/csv; charset=utf-8", db.export_csv())
    raise Reply(404, "not found")


def _v1(ctx, method, path, query, headers):
    db = ctx.accounts
    if method != "GET":
        raise Reply(405, "GET only")
    if path == "/v1/health":
        try:
            run = read_json(ctx.data_dir, "run.json")
        except Exception:
            run = {}
        return 200, {"ok": True, "as_of": run.get("as_of"), "endpoints": ENDPOINTS}
    key = _bearer(headers) or (query.get("key") or [None])[0]
    if not key or not key.startswith("ovf_"):
        raise Reply(401, "an API key is needed: Authorization: Bearer ovf_... (create one on the Developers page)")
    k = db.verify_api_key(key)
    if not k:
        raise Reply(401, "unknown or revoked key")
    status, body = 200, None
    try:
        if k["used_today"] >= k["daily_limit"]:
            raise Reply(429, f"daily limit of {k['daily_limit']} calls reached; it resets 24 hours after the first call")
        if path == "/v1/plans":
            plans = _file(ctx, "plans.json")
            body = _envelope(plans.get("as_of"), plans)
        elif re.match(r"^/v1/farms/[^/]+/plan$", path):
            fid = urllib.parse.unquote(path.split("/")[3])
            plans = _file(ctx, "plans.json")
            plan = next((p for p in plans.get("plans", []) if p.get("farm_id") == fid), None)
            if not plan:
                raise Reply(404, f"no plan for farm {fid}")
            body = _envelope(plans.get("as_of"), plan)
        elif path == "/v1/route":
            route = _file(ctx, "route.json")
            body = _envelope(route.get("as_of"), route)
        elif path == "/v1/landscape":
            a = _file(ctx, "analytics.json")
            body = _envelope(a.get("as_of"), {"landscape": a.get("landscape"), "forecast_source": a.get("forecast_source")})
        elif path == "/v1/sources":
            try:
                doc = json.loads(Path(ctx.sources_path).read_text(encoding="utf-8"))
            except Exception:
                raise Reply(503, "sources list not published yet")
            body = _envelope(doc.get("generated"), doc)
        else:
            raise Reply(404, "unknown endpoint")
    except Reply as r:
        status, body = r.status, {"error": r.error, "endpoints": ENDPOINTS} if r.status == 404 and "endpoint" in r.error else {"error": r.error}
    db.log_api_use(k["key_id"], path, status)
    return status, body


ACCOUNT_PREFIXES = ("/demo/", "/auth/", "/me", "/terms", "/keys", "/admin/", "/v1/")


def handles(path):
    """True when accounts_api owns this path (the inbound server asks before trying its own routes)."""
    p = path.split("?")[0]
    return any(p == pre.rstrip("/") or p.startswith(pre) for pre in ACCOUNT_PREFIXES)



def _demo_text(ctx, method, headers, body):
    """Send the morning plan for one farm to one phone, now, through Photon: the demo of the 08:00 text.
    The signed-in account's phone and farm are the defaults; the body may name a phone and a farm id."""
    if method != "POST":
        raise Reply(405, "POST only")
    acc = _need_account(ctx, headers)
    doc = _json_body(body)
    phone = str(doc.get("phone") or acc.get("phone") or "").strip()
    if not phone:
        raise Reply(400, "no phone: add one to your profile or send it in the body")
    farm = str(doc.get("farm") or acc.get("farm_id") or "").strip()
    language = str(doc.get("language") or acc.get("language") or "en").strip() or "en"
    dry_run = bool(doc.get("dry_run"))  # render and answer, send nothing
    try:
        plans = {p["farm_id"]: p for p in json.loads((Path(ctx.data_dir) / "plans.json").read_text(encoding="utf-8"))["plans"]}
    except (OSError, ValueError, KeyError) as e:
        raise Reply(503, f"no plans on this server: {e}")
    plan = plans.get(farm) or next(iter(plans.values()), None)
    if plan is None:
        raise Reply(503, "no plans on this server")
    import providers
    import queue_morning
    photon = providers.photon_from_env()
    if photon is None and not dry_run:
        raise Reply(503, "Photon is not configured on this server (PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_FROM)")
    text = queue_morning.render_plan(plan, language)
    if dry_run:
        return 200, {"sent": False, "dry_run": True, "to": phone, "farm": plan["farm_id"], "channel": "photon_sms",
                     "configured": photon is not None, "preview": text[:240], "length": len(text)}
    try:
        result = photon.send(phone, text, language)
    except Exception as e:  # provider errors come back as a plain message, never a crash
        raise Reply(502, f"Photon did not accept the text: {e}")
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        ctx.accounts.push_deliveries({"date": today, "deliveries": [{
            "date": today, "roster_id": acc.get("roster_id") or "", "farm_id": plan["farm_id"], "role": acc.get("role") or "farmer",
            "channel": "photon_sms", "status": "sent", "detail": f"demo text to {phone}", "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}]}, dry_run=False)
    except Exception:
        pass  # the text went out; the log line is a courtesy
    return 200, {"sent": True, "to": phone, "farm": plan["farm_id"], "channel": "photon_sms", "preview": text[:240],
                 "result": result if isinstance(result, (dict, str, int)) else str(result)}

def dispatch(method, raw_path, headers, body, ctx, client_ip="?"):
    """-> (status, headers, body bytes). headers is a plain dict (any case); body is bytes."""
    parsed = urllib.parse.urlparse(raw_path)
    path, query = parsed.path.rstrip("/") or "/", urllib.parse.parse_qs(parsed.query)
    headers = {k: v for k, v in (headers or {}).items()}
    hdr = {k: v for k, v in CORS.items()}
    if method == "OPTIONS":
        return 204, hdr, b""
    try:
        if path.startswith("/auth/"):
            status, out = _auth(ctx, method, path, headers, body, client_ip)
        elif path == "/me" or path.startswith("/me/"):
            status, out = _me(ctx, method, path, headers, body)
        elif path == "/terms" or path.startswith("/terms/") or path == "/keys" or path.startswith("/keys/"):
            status, out = _keys(ctx, method, path, headers, body)
        elif path == "/demo/text":
            status, out = _demo_text(ctx, method, headers, body)
        elif path.startswith("/admin/"):
            status, out = _admin(ctx, method, path, query, headers, body)
        elif path.startswith("/v1/"):
            status, out = _v1(ctx, method, path, query, headers)
        else:
            raise Reply(404, "not found")
    except Reply as r:
        status, out = r.status, {"error": r.error}
    if isinstance(out, tuple):
        ctype, text = out
        hdr["Content-Type"] = ctype
        return status, hdr, text.encode("utf-8")
    hdr["Content-Type"] = "application/json; charset=utf-8"
    return status, hdr, json.dumps(out, ensure_ascii=False).encode("utf-8")
