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
  POST /tools/<name>      {"handle", "args"} -> the shared tool's JSON (get_plan / get_route / get_change) for a
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

import inbound
from common import load_env, read_json
from providers import say_language
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


def make_handler(data_dir, roster_path, bridge_token=None, twilio_auth_token=None, public_url=None, public_ws_url=None):
    data_dir = Path(data_dir)
    roster = load_roster(roster_path)
    public_url = (public_url or "").rstrip("/")
    public_ws_url = public_ws_url or None

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

        # ---- routes ----
        def do_GET(self):
            if self.path.split("?")[0] == "/health":
                try:
                    plans = read_json(data_dir, "plans.json")["plans"]
                    stops = read_json(data_dir, "route.json")["stops"]
                except Exception as e:  # files missing: say so, do not crash
                    return self._json(503, {"ok": False, "error": str(e)})
                return self._json(200, {"ok": True, "plans": len(plans), "route_stops": len(stops), "roster_rows": len(roster)})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
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
                body = twiml_for(params.get("From", ""), params.get("SpeechResult"), data_dir, roster)
                return self._send(200, XML_HEAD + body, "text/xml; charset=utf-8")
            if path == "/photon/inbound":
                if not self._authorized():
                    return self._json(401, {"error": "unauthorized"})
                doc = json.loads(raw or b"{}")
                sender = doc.get("from") or doc.get("sender") or doc.get("handle") or ""
                text = doc.get("text") or doc.get("message") or doc.get("body") or ""
                return self._json(200, {"to": sender, "reply": inbound.answer(sender, text, data_dir, roster)})
            if path.startswith("/tools/"):
                if not self._authorized():
                    return self._json(401, {"error": "unauthorized"})
                doc = json.loads(raw or b"{}")
                out = inbound.call_tool(path[len("/tools/"):], doc.get("args") or {}, doc.get("handle", ""), data_dir, roster)
                if isinstance(out, dict) and "error" in out:
                    err = out["error"].lower()
                    return self._json(400 if "unknown tool" in err else (404 if "no plan" in err else 403), out)
                return self._json(200, out)
            return self._json(404, {"error": "not found"})

    return Handler


def serve(data_dir, roster_path=None, host="0.0.0.0", port=8788, public_url=None):
    load_env()
    handler = make_handler(data_dir, roster_path, bridge_token=os.environ.get("INBOUND_TOKEN") or None,
                           twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN") or None,
                           public_url=public_url or os.environ.get("PUBLIC_URL"),
                           public_ws_url=os.environ.get("PUBLIC_WS_URL"))
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"  inbound service on http://{host}:{port}  (data {data_dir}; Twilio signature check "
          f"{'on' if os.environ.get('TWILIO_AUTH_TOKEN') else 'off'}; bearer token {'on' if os.environ.get('INBOUND_TOKEN') else 'off'})")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None, help="roster CSV (default: ROSTER_PATH from .env)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--public-url", default=None, help="scheme+host Twilio calls, for signature checks (default PUBLIC_URL)")
    a = ap.parse_args()
    serve(Path(a.data), a.roster, a.host, a.port, a.public_url)
