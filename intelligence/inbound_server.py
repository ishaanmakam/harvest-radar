"""Inbound HTTP service for the live channels. Standard library only; reads plans.json / route.json fresh on every
request, so a season refresh (which swaps the files atomically) is picked up at once.

    python intelligence/inbound_server.py --data data/mock --port 8788 [--roster ROSTER.csv] [--public-url https://...]

Endpoints
  GET  /health            {"ok", "plans", "route_stops", "roster_rows"}
  POST /twilio/voice      Twilio Programmable Voice webhook (form-encoded). First hit: the caller hears their own
                          plan (officer: the route) in their roster language, then <Gather input="speech"> lets
                          them ask; with SpeechResult: the answer, then Gather again. Unknown caller: the no-farm
                          message and <Hangup/>. X-Twilio-Signature is checked when TWILIO_AUTH_TOKEN is set
                          (PUBLIC_URL must then be the scheme+host Twilio calls).
  POST /photon/inbound    {"from"|"sender", "text"|"message"} -> {"to", "reply"}; the officer's iMessage questions
                          (farmers are scoped the same way). Bearer INBOUND_TOKEN when set.
  GET  /signup            a small sign-up page; POST /signup {name, address|lat+lon, phone|email, language, send_time,
                          timezone} adds the person to the roster (JSON or form body); no token needed.
  POST /tools/<name>      {"handle", "args"} -> the shared tool's JSON (get_plan / get_route / get_change / get_outlook) for a
                          voice agent; {"error"} with 403 / 404 / 400 on refusal. Bearer INBOUND_TOKEN when set.
Nothing here places calls or sends texts.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

import accounts as accounts_mod
import accounts_api
import inbound
import signup
from common import load_env, read_json, product_name
from photon_poll import handle_inbound
from providers import parse_photon_webhook, photon_from_env, photon_signature_ok, say_language, LANGUAGE_NAMES
from tools import UNKNOWN_CALLER_MESSAGE, load_roster, resolve_caller

XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'
ASK_FARMER = "Ask me about your farm, or hang up."
ASK_OFFICER = "Ask about the route or any farm, or hang up."


def twilio_signature(url, params, auth_token):
    """Twilio's request signature: HMAC-SHA1 over the full URL plus the POST params sorted by key (key+value)."""
    payload = url + "".join(k + str(v) for k, v in sorted(params.items()))
    return base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def twilio_signature_ok(url, params, signature, auth_token):
    return bool(signature) and hmac.compare_digest(twilio_signature(url, params, auth_token), signature)


def twiml_for(handle, speech, data_dir, roster):
    """TwiML for one turn of a call: what to say, then listen again (or hang up on an unknown caller)."""
    row = resolve_caller(inbound._strip_scheme(handle), roster)
    if row is None:
        return f"<Response><Say>{escape(UNKNOWN_CALLER_MESSAGE)}</Say><Hangup/></Response>"
    lang = say_language(row["language"])
    reply = inbound.answer(handle, speech or "", data_dir, roster)
    prompt = ASK_OFFICER if row["role"] == "officer" else ASK_FARMER
    return (f'<Response><Say language="{lang}">{escape(reply)}</Say>'
            f'<Gather input="speech" language="{lang}" action="/twilio/voice" method="POST" speechTimeout="auto">'
            f'<Say language="{lang}">{escape(prompt)}</Say></Gather></Response>')


SIGNUPS_PER_IP = 30     # per server lifetime; a kiosk at a field day stays under this, a script does not

TIMEZONES = ("America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles", "Asia/Kolkata",
             "Africa/Nairobi", "Europe/London", "America/Sao_Paulo", "Asia/Manila")


def signup_page():
    """A small page anyone can open on a phone: address or location, phone or email, language, time, timezone."""
    name = escape(product_name())
    langs = "".join(f'<option value="{k}">{escape(v.split(" (")[0])}</option>' for k, v in sorted(LANGUAGE_NAMES.items(), key=lambda kv: kv[0] != "en"))
    tzs = "".join(f'<option value="{t}">{t}</option>' for t in TIMEZONES)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} sign-up</title>
<style>
:root{{--ink:#15221b;--mut:#5b6b60;--line:#d8e0d9;--ok:#1f7a3f;--bad:#a12a2a;--bg:#f4f7f3;--card:#fff;--acc:#1f7a3f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:520px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:26px;margin:8px 0 4px}}p.lead{{color:var(--mut);margin:0 0 20px}}
form{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}}label{{display:block;font-weight:600;margin:14px 0 6px}}
input,select{{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fff}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}small{{color:var(--mut)}}
button{{margin-top:18px;width:100%;font:inherit;font-weight:700;padding:12px;border:0;border-radius:10px;background:var(--acc);color:#fff}}
#out{{margin-top:16px;padding:14px;border-radius:10px;display:none}}#out.ok{{display:block;background:#e6f4ea;color:var(--ok)}}#out.bad{{display:block;background:#fbe9e9;color:var(--bad)}}
</style></head><body><main>
<h1>{name}</h1><p class="lead">The satellite checks your field every clear pass. Tell us where it is and how to reach you, and the morning plan arrives at the time you pick.</p>
<form id="f" autocomplete="on">
<label>Your name<input name="name" required maxlength="80"></label>
<label>Field address, or "latitude, longitude"<input name="address" required placeholder="123 County Rd, Ames, IA  or  42.03, -93.47"></label>
<div class="row"><label>Phone (call + text)<input name="phone" type="tel" placeholder="+1 515 555 0101"></label>
<label>Email<input name="email" type="email" placeholder="you@example.com"></label></div>
<small>Give a phone, an email, or both.</small>
<div class="row"><label>Language<select name="language">{langs}</select></label>
<label>Morning time<input name="send_time" type="time" value="06:30" required></label></div>
<label>Timezone<select name="timezone">{tzs}</select></label>
<button type="submit">Start my morning plan</button>
<div id="out" role="status"></div>
</form>
<p><small>Text or email STOP at any time to stop. Your details stay with the {name} team and are used only to send your plan.</small></p>
</main><script>
const f=document.getElementById("f"),out=document.getElementById("out");
try{{const tz=Intl.DateTimeFormat().resolvedOptions().timeZone;const sel=f.timezone;if(tz&&![...sel.options].some(o=>o.value===tz)){{sel.add(new Option(tz,tz))}};if(tz)sel.value=tz}}catch(e){{}}
f.addEventListener("submit",async e=>{{e.preventDefault();out.className="";out.textContent="Checking the satellite map...";out.style.display="block";
const body=Object.fromEntries(new FormData(f).entries());
try{{const r=await fetch("/signup",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(body)}});const j=await r.json();
out.className=j.ok?"ok":"bad";out.textContent=j.ok?j.message:(j.error||"Something went wrong.");}}catch(err){{out.className="bad";out.textContent="Could not reach the server.";}}}});
</script></body></html>"""


def make_handler(data_dir, roster_path, bridge_token=None, twilio_auth_token=None, public_url=None, public_ws_url=None,
                 photon=None, photon_webhook_secret=None, geocode=None, accounts_db=None, sources_path=None):
    """photon: a provider with send_to_space(space_id, text) used to answer Photon webhooks (None = reply only in the
    HTTP response); photon_webhook_secret: verify Photon's HMAC signature when set. accounts_db: the SQLite file of
    Overfield's own accounts (sign-in, profiles, developer keys, admin); its routes are served here too."""
    data_dir = Path(data_dir)
    roster_state = {"mtime": None, "rows": []}
    api_ctx = None
    if accounts_db:
        os.environ.setdefault("ACCOUNTS_DB", str(accounts_db))        # so load_roster merges the sign-ups in
        api_ctx = accounts_api.Context(accounts=accounts_mod.Accounts(accounts_db), data_dir=data_dir,
                                       sources_path=Path(sources_path) if sources_path else data_dir.parents[1] / "web" / "public" / "data" / "sources.json")

    def current_roster():
        """The roster as it is now: re-read when the CSV changed, and always when sign-ups come from the accounts
        database (sign-ups, pauses and STOPs land without a restart)."""
        try:
            mtime = os.path.getmtime(roster_path) if roster_path else None
        except OSError:
            mtime = None
        if api_ctx is not None or roster_state["mtime"] != mtime or roster_state["rows"] is None:
            roster_state["rows"] = load_roster(roster_path)
            roster_state["mtime"] = mtime
        return roster_state["rows"]

    current_roster()                                    # a bad roster fails at start, not on the first call
    geocode_fn = geocode or signup.geocode
    fields_cache = {}
    signups_by_ip = {}

    def fields():
        if "doc" not in fields_cache:
            fields_cache["doc"] = read_json(data_dir, "fields.geojson")
        return fields_cache["doc"]

    def answer_text(sender, text):
        return inbound.handle_stop(roster_path, sender, text) or inbound.answer(sender, text, data_dir, current_roster())
    public_url = (public_url or "").rstrip("/")
    public_ws_url = public_ws_url or None
    seen_message_ids = set()

    class Handler(BaseHTTPRequestHandler):
        server_version = "HarvestRadarInbound/1"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        # ---- helpers ----
        def _send(self, status, body, ctype):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _authorized(self):
            return not bridge_token or self.headers.get("Authorization") == f"Bearer {bridge_token}"

        def _api(self):
            """Accounts, admin and the /v1 developer API (accounts_api.dispatch)."""
            if api_ctx is None:
                return self._json(503, {"error": "accounts are not set up on this server (ACCOUNTS_DB)"})
            ip = self.client_address[0] if self.client_address else "?"
            status, hdr, body = accounts_api.dispatch(self.command, self.path, dict(self.headers.items()), self._body(), api_ctx, client_ip=ip)
            self.send_response(status)
            for k, v in hdr.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self):
            return self._api()

        def do_PATCH(self):
            return self._api()

        def do_DELETE(self):
            return self._api()

        # ---- routes ----
        def do_GET(self):
            if accounts_api.handles(self.path):
                return self._api()
            if self.path.split("?")[0] == "/signup":
                return self._send(200, signup_page(), "text/html; charset=utf-8")
            if self.path.split("?")[0] == "/health":
                try:
                    plans = read_json(data_dir, "plans.json")["plans"]
                    stops = read_json(data_dir, "route.json")["stops"]
                except Exception as e:  # files missing: say so, do not crash
                    return self._json(503, {"ok": False, "error": str(e)})
                return self._json(200, {"ok": True, "plans": len(plans), "route_stops": len(stops), "roster_rows": len(current_roster()),
                                        "signup": "/signup"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if accounts_api.handles(self.path):
                return self._api()
            path = self.path.split("?")[0]
            raw = self._body()
            if path == "/twilio/stream":
                # TwiML that connects the call to the Grok bridge; `from` identifies the person on the line
                params = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                person = (query.get("to") or [None])[0] or params.get("From", "")
                if not public_ws_url:
                    return self._send(503, XML_HEAD + "<Response><Say>The voice bridge is not configured.</Say><Hangup/></Response>", "text/xml; charset=utf-8")
                body = (f'<Response><Connect><Stream url="{escape(public_ws_url.rstrip("/"))}/media-stream">'
                        f'<Parameter name="from" value="{escape(person)}"/></Stream></Connect></Response>')
                return self._send(200, XML_HEAD + body, "text/xml; charset=utf-8")
            if path == "/twilio/voice":
                params = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
                if twilio_auth_token:
                    url = public_url + self.path
                    if not twilio_signature_ok(url, params, self.headers.get("X-Twilio-Signature", ""), twilio_auth_token):
                        return self._send(403, XML_HEAD + "<Response><Say>Forbidden</Say><Hangup/></Response>", "text/xml; charset=utf-8")
                body = twiml_for(params.get("From", ""), params.get("SpeechResult"), data_dir, current_roster())
                return self._send(200, XML_HEAD + body, "text/xml; charset=utf-8")
            if path == "/photon/webhook":
                # Photon 2.0 webhook destination (event message.received): answer the texter in the same Space
                if photon_webhook_secret:
                    sig = (self.headers.get("X-Spectrum-Signature") or self.headers.get("X-Photon-Signature")
                           or self.headers.get("Photon-Signature") or "")
                    if not photon_signature_ok(raw, sig, photon_webhook_secret):
                        return self._json(401, {"error": "bad signature"})
                try:
                    doc = json.loads(raw or b"{}")
                except ValueError:
                    return self._json(400, {"error": "bad json"})
                ev = parse_photon_webhook(doc)
                if ev is None or ev["from_agent"]:
                    return self._json(200, {"handled": False, "reason": "not a text from a person"})
                mid = ev.get("message_id")
                if mid in seen_message_ids:
                    return self._json(200, {"handled": False, "reason": "duplicate"})
                if mid:
                    if len(seen_message_ids) > 5000:
                        seen_message_ids.clear()
                    seen_message_ids.add(mid)
                return self._json(200, handle_inbound(ev, data_dir, current_roster(), photon=photon))
            if path == "/signup":
                ip = self.client_address[0] if self.client_address else "?"
                signups_by_ip[ip] = signups_by_ip.get(ip, 0) + 1
                if signups_by_ip[ip] > SIGNUPS_PER_IP:
                    return self._json(429, {"error": "too many sign-ups from this address; try again later"})
                ctype = (self.headers.get("Content-Type") or "").lower()
                try:
                    if "json" in ctype:
                        doc = json.loads(raw or b"{}")
                    else:
                        doc = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
                except ValueError:
                    return self._json(400, {"error": "the sign-up body must be JSON or a form"})
                lon, lat, address = doc.get("lon"), doc.get("lat"), (doc.get("address") or "").strip()
                if (lon in (None, "") or lat in (None, "")) and address and not signup.parse_latlon(address):
                    found = geocode_fn(address)
                    if not found:
                        return self._json(400, {"error": f"Could not find that address: {address}. Try adding the town and state, or give latitude, longitude."})
                    lon, lat = found[0], found[1]
                try:
                    out = signup.register(fields(), roster_path, name=doc.get("name"), phone=doc.get("phone"), email=doc.get("email"),
                                          address=address, lon=lon if lon not in (None, "") else None, lat=lat if lat not in (None, "") else None,
                                          language=doc.get("language") or "en", send_time=doc.get("send_time"), timezone=doc.get("timezone"))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                except FileNotFoundError as e:
                    return self._json(503, {"error": f"the season files are not ready: {e}"})
                row = out["row"]
                return self._json(200, {"ok": True, "roster_id": row["roster_id"], "farm_id": row["farm_id"], "created": out["created"],
                                        "send_time": row["send_time"], "timezone": row["timezone"], "message": out["welcome"]})
            if path == "/photon/inbound":
                if not self._authorized():
                    return self._json(401, {"error": "unauthorized"})
                doc = json.loads(raw or b"{}")
                sender = doc.get("from") or doc.get("sender") or doc.get("handle") or ""
                text = doc.get("text") or doc.get("message") or doc.get("body") or ""
                return self._json(200, {"to": sender, "reply": answer_text(sender, text)})
            if path.startswith("/tools/"):
                if not self._authorized():
                    return self._json(401, {"error": "unauthorized"})
                doc = json.loads(raw or b"{}")
                out = inbound.call_tool(path[len("/tools/"):], doc.get("args") or {}, doc.get("handle", ""), data_dir, current_roster())
                if isinstance(out, dict) and "error" in out:
                    err = out["error"].lower()
                    return self._json(400 if "unknown tool" in err else (404 if "no plan" in err else 403), out)
                return self._json(200, out)
            return self._json(404, {"error": "not found"})

    return Handler


def serve(data_dir, roster_path=None, host="0.0.0.0", port=8788, public_url=None, accounts_db=None):
    load_env()
    photon = photon_from_env()
    accounts_db = accounts_db or os.environ.get("ACCOUNTS_DB") or "data/accounts.sqlite"
    handler = make_handler(data_dir, roster_path, bridge_token=os.environ.get("INBOUND_TOKEN") or None,
                           twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN") or None,
                           public_url=public_url or os.environ.get("PUBLIC_URL"),
                           public_ws_url=os.environ.get("PUBLIC_WS_URL"),
                           photon=photon, photon_webhook_secret=os.environ.get("PHOTON_WEBHOOK_SECRET") or None,
                           accounts_db=accounts_db)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"  inbound service on http://{host}:{port}  (data {data_dir}; accounts {accounts_db}; Twilio signature check "
          f"{'on' if os.environ.get('TWILIO_AUTH_TOKEN') else 'off'}; bearer token {'on' if os.environ.get('INBOUND_TOKEN') else 'off'}; "
          f"Photon replies {'on' if photon else 'off'}; Photon signature check {'on' if os.environ.get('PHOTON_WEBHOOK_SECRET') else 'off'})")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default: ROSTER_PATH from .env)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--public-url", default=None, help="scheme+host Twilio calls, for signature checks (default PUBLIC_URL)")
    ap.add_argument("--accounts-db", default=None, help="SQLite file for sign-ins, profiles and keys (default ACCOUNTS_DB, else data/accounts.sqlite)")
    a = ap.parse_args()
    serve(Path(a.data), a.roster, a.host, a.port, a.public_url, a.accounts_db)
