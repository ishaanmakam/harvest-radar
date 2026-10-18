"""Provider adapters behind the morning queue. Each one has send(phone, text, language) -> short result string and
raises ProviderError on a failed attempt (queue_morning retries twice). No network code runs until a real send.

grok_voice -> TwilioVoice: an outbound call placed through Twilio's REST API (POST .../Calls.json).
  - With VOICE_TWIML_URL set, Twilio fetches TwiML from that URL (our bridge), which answers
    <Connect><Stream url="wss://..."/> to relay the call into a Grok Voice realtime session
    (wss://api.x.ai/v1/realtime, voices Ara / Rex / Sal / Eve / Leo). The bridge gets ?to=&language= so it can
    load the right plan through tools.py and let the farmer ask about their own farm.
  - Without it, the call speaks the rendered words with Twilio text-to-speech (<Say language="hi-IN">...):
    the fallback that works tonight with nothing but a Twilio number. Not the sponsor path, but a real call.
photon_sms -> PhotonBridgeSMS: POST {"to", "text", "language", "channel": "sms"} as JSON, bearer token, to
  PHOTON_BRIDGE_URL. Photon's platform (Spectrum) only ships a TypeScript SDK, so the bridge is a small Node
  service that creates the chat and sends the text; it answers 2xx with {"id": ..., "status": ...}.

from_env(env) builds the providers dict for queue_morning.run(). Anything unconfigured stays an
UnverifiedProvider whose message names the missing variables, so a real run fails loudly and early.

.env keys: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, VOICE_TWIML_URL (optional),
           PHOTON_BRIDGE_URL, PHOTON_BRIDGE_TOKEN
"""
import base64
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from common import load_env

SAY_LANGUAGE = {"pa": "pa-IN", "hi": "hi-IN", "en": "en-IN"}
VOICE_VARS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
SMS_VARS = ("PHOTON_BRIDGE_URL", "PHOTON_BRIDGE_TOKEN")


class ProviderError(RuntimeError):
    """A failed attempt the queue may retry."""


def say_language(code):
    return SAY_LANGUAGE.get((code or "").strip().lower()[:2], "en-IN")


def _urllib_http(method, url, headers, body, timeout):
    """(status, body bytes); HTTP errors come back as a status, transport errors raise ProviderError."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise ProviderError(f"{url}: {getattr(e, 'reason', e)}")


def _payload(body):
    try:
        return json.loads(body or b"{}")
    except Exception:
        return {"raw": (body or b"")[:200].decode(errors="replace")}


def _ok(status, payload, what):
    if not 200 <= status < 300:
        msg = payload.get("message") or payload.get("error") or payload.get("raw") or f"HTTP {status}"
        raise ProviderError(f"{what}: {msg} (HTTP {status})")


class TwilioVoice:
    def __init__(self, account_sid, auth_token, from_number, twiml_url=None, http=None, timeout=20):
        self.account_sid, self.auth_token, self.from_number = account_sid, auth_token, from_number
        self.twiml_url, self.http, self.timeout = twiml_url or None, http or _urllib_http, timeout

    def send(self, phone, text, language):
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls.json"
        form = {"To": phone, "From": self.from_number}
        if self.twiml_url:
            sep = "&" if "?" in self.twiml_url else "?"
            form["Url"] = self.twiml_url + sep + urlencode({"to": phone, "language": language or "en"})
        else:
            form["Twiml"] = f'<Response><Say language="{say_language(language)}">{escape(text)}</Say></Response>'
        token = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {token}", "Content-Type": "application/x-www-form-urlencoded"}
        status, body = self.http("POST", url, headers, urlencode(form).encode(), self.timeout)
        payload = _payload(body)
        _ok(status, payload, "twilio")
        return f"twilio call {payload.get('sid', '?')} ({payload.get('status', 'queued')})"


class PhotonBridgeSMS:
    def __init__(self, url, token, http=None, timeout=20):
        self.url, self.token, self.http, self.timeout = url, token, http or _urllib_http, timeout

    def send(self, phone, text, language):
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        body = json.dumps({"to": phone, "text": text, "language": language, "channel": "sms"}).encode()
        status, raw = self.http("POST", self.url, headers, body, self.timeout)
        payload = _payload(raw)
        _ok(status, payload, "photon bridge")
        return f"photon {payload.get('id', '?')} ({payload.get('status', 'queued')})"


def from_env(env=None, http=None):
    """Providers for queue_morning.run(); unconfigured channels stay UnverifiedProvider stubs."""
    from queue_morning import UnverifiedProvider  # local import: queue_morning imports this module lazily too

    if env is None:
        load_env()
        env = os.environ
    out = {}
    missing = [v for v in VOICE_VARS if not env.get(v)]
    out["grok_voice"] = (TwilioVoice(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"], env["TWILIO_FROM"],
                                     twiml_url=env.get("VOICE_TWIML_URL"), http=http)
                         if not missing else UnverifiedProvider("grok_voice", f"set {', '.join(missing)} in .env"))
    missing = [v for v in SMS_VARS if not env.get(v)]
    out["photon_sms"] = (PhotonBridgeSMS(env["PHOTON_BRIDGE_URL"], env["PHOTON_BRIDGE_TOKEN"], http=http)
                         if not missing else UnverifiedProvider("photon_sms", f"set {', '.join(missing)} in .env"))
    return out
