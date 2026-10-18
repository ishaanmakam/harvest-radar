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
import hashlib
import hmac
import json
import os
import smtplib
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from common import load_env, product_name

SAY_LANGUAGE = {"pa": "pa-IN", "hi": "hi-IN", "en": "en-IN"}
VOICE_VARS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
PHOTON_VARS = ("PHOTON_API_KEY", "PHOTON_PROJECT_ID", "PHOTON_FROM")      # Photon 2.0 REST, preferred
SMS_VARS = ("PHOTON_BRIDGE_URL", "PHOTON_BRIDGE_TOKEN")                    # a Node bridge, the fallback
PHOTON_BASE = "https://api.photon.codes"


class ProviderError(RuntimeError):
    """A failed attempt the queue may retry."""


def say_language(code):
    return SAY_LANGUAGE.get((code or "").strip().lower()[:2], "en-IN")


def _urllib_http(method, url, headers, body, timeout):
    """(status, body bytes); HTTP errors come back as a status, transport errors raise ProviderError."""
    # Photon sits behind Cloudflare, which refuses requests with no browser-like identity.
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "Overfield/1.0 (+https://ishaanmakam.github.io/harvest-radar/)", **headers})
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
        msg = (payload.get("message") or payload.get("title") or payload.get("detail") or payload.get("error")
               or payload.get("raw") or f"HTTP {status}")
        if isinstance(msg, dict):
            msg = json.dumps(msg)
        code = payload.get("code") if isinstance(payload, dict) else None
        raise ProviderError(f"{what}: {msg}{f' [{code}]' if code else ''} (HTTP {status})")


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class TwilioVoice:
    def __init__(self, account_sid, auth_token, from_number, twiml_url=None, http=None, timeout=20,
                 api_key_sid=None, api_key_secret=None):
        self.account_sid, self.auth_token, self.from_number = account_sid, auth_token, from_number
        self.twiml_url, self.http, self.timeout = twiml_url or None, http or _urllib_http, timeout
        self.api_key_sid, self.api_key_secret = api_key_sid or None, api_key_secret or None

    def _auth(self):
        """A Twilio API key (SKxxx + secret) when given, else the account SID + auth token."""
        if self.api_key_sid and self.api_key_secret:
            return _basic(self.api_key_sid, self.api_key_secret)
        return _basic(self.account_sid, self.auth_token)

    def send(self, phone, text, language):
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls.json"
        form = {"To": phone, "From": self.from_number}
        if self.twiml_url:
            sep = "&" if "?" in self.twiml_url else "?"
            form["Url"] = self.twiml_url + sep + urlencode({"to": phone, "language": language or "en"})
        else:
            form["Twiml"] = f'<Response><Say language="{say_language(language)}">{escape(text)}</Say></Response>'
        headers = {"Authorization": self._auth(), "Content-Type": "application/x-www-form-urlencoded"}
        status, body = self.http("POST", url, headers, urlencode(form).encode(), self.timeout)
        payload = _payload(body)
        _ok(status, payload, "twilio")
        return f"twilio call {payload.get('sid', '?')} ({payload.get('status', 'queued')})"


class PhotonSMS:
    """Photon 2.0 REST (api.photon.codes/v1/projects/{id}): create-or-get the recipient's User, create-or-get the
    direct Space between our line and them, then send a plain-text message. Lookups are cached per recipient.

    platform "imessage" (the default from .env) uses Photon's shared iMessage lines: each farmer's handle gets an
    assignment on one of the project's pooled lines, that line is the sender, and the farmer must text it once before
    it may message them (Photon's rule for shared lines). platform "sms" needs a number the project owns."""

    def __init__(self, api_key, project_id, from_number, http=None, base_url=PHOTON_BASE, platform="sms",
                 text_format="plain", timeout=20):
        self.api_key, self.project_id, self.from_number = api_key, project_id, from_number
        self.http, self.base_url, self.platform, self.text_format, self.timeout = http or _urllib_http, base_url.rstrip("/"), platform, text_format, timeout
        self._users, self._spaces, self._lines = {}, {}, {}

    def _call(self, method, path, body=None, idempotency=None):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        data = json.dumps(body).encode() if body is not None else None
        status, raw = self.http(method, f"{self.base_url}/v1/projects/{self.project_id}{path}", headers, data, self.timeout)
        payload = _payload(raw)
        _ok(status, payload, "photon")
        return payload

    def user_id(self, handle):
        if handle not in self._users:
            self._users[handle] = self._call("POST", "/users", {"handle": handle, "platform": self.platform})["id"]
        return self._users[handle]

    def line_for(self, handle):
        """The line that talks to this handle: PHOTON_FROM, or on iMessage the shared line Photon assigned to the
        handle (assigned now when it has none)."""
        if self.platform != "imessage":
            return self.from_number
        if handle not in self._lines:
            for a in self._call("GET", "/platforms/imessage/assignments").get("assignments") or []:
                if not a.get("releasedAt") and a.get("handle") and a.get("assignedPhoneNumber"):
                    self._lines[a["handle"]] = a["assignedPhoneNumber"]
            if handle not in self._lines:
                key = "asg-" + hashlib.sha256(f"{self.project_id}|{handle}".encode()).hexdigest()[:32]
                a = self._call("POST", "/platforms/imessage/assignments", {"handle": handle}, idempotency=key)
                self._lines[handle] = a["assignedPhoneNumber"]
        return self._lines[handle]

    def sender_id(self, handle=None):
        return self.user_id(self.line_for(handle) if handle else self.from_number)

    def space_id(self, handle):
        if handle not in self._spaces:
            line = self.line_for(handle)
            key = "space-" + hashlib.sha256(f"{self.platform}|{line}|{handle}".encode()).hexdigest()[:32]
            body = {"participantIds": [self.user_id(line), self.user_id(handle)], "platform": self.platform}
            self._spaces[handle] = self._call("POST", "/spaces", body, idempotency=key)["id"]
        return self._spaces[handle]

    def send_to_space(self, space_id, text, sender_id=None):
        """Post to a Space; the sender defaults to our line (a webhook reply goes out on the Space's own line)."""
        body = {"spaceId": space_id, "senderId": sender_id or self.sender_id(),
                "content": {"type": "text", "format": self.text_format, "text": text}}
        payload = self._call("POST", "/messages", body, idempotency=str(uuid.uuid4()))
        return f"photon {payload.get('id', '?')} ({payload.get('status', 'accepted')})"

    def send(self, phone, text, language):
        try:
            return self.send_to_space(self.space_id(phone), text, sender_id=self.sender_id(phone))
        except ProviderError as e:
            if "SHARED_LINE_INBOUND_REQUIRED" in str(e) or "Inbound Required" in str(e):
                raise ProviderError(f"photon: {phone} has not texted the line yet. Ask them to send one message to "
                                    f"{self.line_for(phone)} first, then send again.") from e
            raise


EMAIL_VARS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")


class SmtpEmail:
    """The email channel: one plain-text message per person over SMTP with STARTTLS (any mailbox provider's app
    password works: Gmail, Outlook, Fastmail). `smtp` is the smtplib.SMTP class, injectable for tests."""

    def __init__(self, host, port=587, user=None, password=None, from_addr=None, smtp=None, subject=None):
        self.host, self.port, self.user, self.password = host, int(port or 587), user, password
        self.from_addr = from_addr or user
        self.smtp = smtp or smtplib.SMTP
        self.subject = subject

    def send(self, to, text, language):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["To"], msg["From"] = to, self.from_addr
        msg["Subject"] = self.subject or f"{product_name()} morning plan"
        msg.set_content(text)
        try:
            with self.smtp(self.host, self.port, timeout=30) as conn:
                conn.starttls()
                if self.user:
                    conn.login(self.user, self.password or "")
                conn.send_message(msg)
        except Exception as e:
            raise ProviderError(f"email to {to} failed: {e}") from e
        return f"email sent to {to}"


def parse_photon_webhook(doc):
    """{handle, text, space_id, message_id, from_agent} for a received text message, else None.
    Accepts the 2.0 envelope {type: "message.received", data: {object: Message}} and the older Spectrum
    envelope {event: "messages", space, message}."""
    if not isinstance(doc, dict):
        return None
    if isinstance(doc.get("data"), dict) and "object" in doc["data"]:
        if doc.get("type") not in (None, "message.received"):
            return None
        msg = doc["data"].get("object") or {}
        space, sender = msg.get("space") or {}, msg.get("sentBy") or {}
    elif isinstance(doc.get("message"), dict):
        msg = doc["message"]
        space, sender = doc.get("space") or {}, msg.get("sender") or msg.get("sentBy") or {}
    else:
        return None
    content = msg.get("content") or {}
    if content.get("type") != "text" or not content.get("text"):
        return None
    handle = sender.get("handle") or sender.get("phone") or sender.get("address") or ""
    return {"handle": handle, "text": content["text"], "space_id": space.get("id"), "message_id": msg.get("id"),
            "from_agent": sender.get("kind") == "agent"}


def photon_signature_ok(body, header, secret):
    """HMAC-SHA256 of the raw body with the per-webhook secret; the header may be 'v0=<hex>', '<hex>', 'sha256=<hex>'
    or base64 (the docs show v0= and do not pin the encoding)."""
    if not header or not secret:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    given = header.strip()
    for prefix in ("v0=", "sha256=", "v1="):
        if given.startswith(prefix):
            given = given[len(prefix):]
    candidates = {mac.hex(), base64.b64encode(mac).decode()}
    return any(hmac.compare_digest(c, given) for c in candidates)


def photon_from_env(env=None, http=None):
    """A PhotonSMS when the direct REST variables are set, else None."""
    if env is None:
        load_env()
        env = os.environ
    if all(env.get(v) for v in PHOTON_VARS):
        return PhotonSMS(env["PHOTON_API_KEY"], env["PHOTON_PROJECT_ID"], env["PHOTON_FROM"], http=http,
                         base_url=env.get("PHOTON_BASE_URL") or PHOTON_BASE,
                         platform=(env.get("PHOTON_PLATFORM") or "imessage").strip().lower(),
                         text_format=env.get("PHOTON_TEXT_FORMAT") or "plain")
    return None


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


LANGUAGE_NAMES = {"pa": "Punjabi (Gurmukhi script)", "hi": "Hindi", "en": "English"}
TRANSLATE_SYSTEM = ("You translate short agricultural advisory messages into {language}. Keep farm ids (like F0012), "
                    "dates written as YYYY-MM-DD, numbers and units exactly as written. Plain text only, no notes. "
                    "Reply with the translation and nothing else.")
CACHE_SEP = "␟"


class GrokTranslator:
    """translate(text, language) through xAI chat completions, cached on disk so the same sentence is never paid
    for twice; on any failure the English text is returned (a morning never waits on a translation)."""

    def __init__(self, api_key, model="grok-4.7", cache_path=None, http=None, timeout=30):
        self.api_key, self.model, self.http, self.timeout = api_key, model, http or _urllib_http, timeout
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save(self):
        if not self.cache_path:
            return
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(tmp, self.cache_path)

    def __call__(self, text, language):
        lang = (language or "en").strip().lower()[:2]
        if not text or lang == "en":
            return text
        key = f"{lang}{CACHE_SEP}{text}"
        if key in self.cache:
            return self.cache[key]
        body = json.dumps({"model": self.model, "temperature": 0,
                           "messages": [{"role": "system", "content": TRANSLATE_SYSTEM.format(language=LANGUAGE_NAMES.get(lang, lang))},
                                        {"role": "user", "content": text}]}).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            status, raw = self.http("POST", "https://api.x.ai/v1/chat/completions", headers, body, self.timeout)
            payload = _payload(raw)
            _ok(status, payload, "grok translate")
            out = (payload["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            print(f"  translate: falling back to English ({e})")
            return text
        if not out:
            return text
        self.cache[key] = out
        self._save()
        return out


def translator_from_env(env=None, cache_path=None, http=None):
    """A GrokTranslator when XAI_API_KEY is set, else None (English words)."""
    if env is None:
        load_env()
        env = os.environ
    key = env.get("XAI_API_KEY")
    if not key:
        return None
    return GrokTranslator(key, model=env.get("XAI_MODEL") or "grok-4.7", cache_path=cache_path, http=http)


def from_env(env=None, http=None):
    """Providers for queue_morning.run(); unconfigured channels stay UnverifiedProvider stubs."""
    from queue_morning import UnverifiedProvider  # local import: queue_morning imports this module lazily too

    if env is None:
        load_env()
        env = os.environ
    out = {}
    missing = [v for v in VOICE_VARS if not env.get(v)]
    out["grok_voice"] = (TwilioVoice(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"], env["TWILIO_FROM"],
                                     twiml_url=env.get("VOICE_TWIML_URL"), http=http,
                                     api_key_sid=env.get("TWILIO_API_KEY_SID"), api_key_secret=env.get("TWILIO_API_KEY_SECRET"))
                         if not missing else UnverifiedProvider("grok_voice", f"set {', '.join(missing)} in .env"))
    direct = photon_from_env(env, http=http)
    if direct is not None:
        out["photon_sms"] = direct
    elif all(env.get(v) for v in SMS_VARS):
        out["photon_sms"] = PhotonBridgeSMS(env["PHOTON_BRIDGE_URL"], env["PHOTON_BRIDGE_TOKEN"], http=http)
    else:
        missing = [v for v in PHOTON_VARS if not env.get(v)]
        out["photon_sms"] = UnverifiedProvider("photon_sms", f"set {', '.join(missing)} in .env "
                                               f"(or {' + '.join(SMS_VARS)} for the Node bridge)")
    missing = [v for v in EMAIL_VARS if not env.get(v)]
    out["email"] = (SmtpEmail(env["SMTP_HOST"], env.get("SMTP_PORT") or 587, env["SMTP_USER"], env["SMTP_PASSWORD"], env["SMTP_FROM"])
                    if not missing else UnverifiedProvider("email", f"set {', '.join(missing)} in .env (optional channel)"))
    return out
