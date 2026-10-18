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
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    """A failed attempt the queue may retry. retry_after is set when Photon answers 429."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# Shared iMessage lines cannot open a chat. The queue stores this string on the failed delivery and moves on.
IMESSAGE_NO_CHAT = ("no iMessage chat yet: the person must text the Overfield line first "
                    "(shared lines cannot start a conversation)")
_RATE_LIMIT_TRIES = 4          # one try plus three backoffs, then the error reaches the caller


def _handle_key(handle):
    """Phone handles become E.164 (the roster's own normalizer). Email handles stay emails, lowercased."""
    h = (handle or "").strip()
    lower = h.lower()
    for prefix in ("tel:", "sms:", "imessage:"):
        if lower.startswith(prefix):
            h = h[len(prefix):].strip()
            lower = h.lower()
            break
    if "@" in h:
        return lower
    from tools import normalize_phone
    return normalize_phone(h)


def _http_parts(result):
    """(status, body, headers). Fakes may return a pair; a real call can add a header map as the third item."""
    if isinstance(result, tuple) and len(result) >= 3:
        return result[0], result[1], result[2] or {}
    status, raw = result
    return status, raw, {}


def _header(headers, name):
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return value
    return None


def _retry_after_seconds(headers):
    """OpenAPI Retry-After: a delay in seconds, or an HTTP date. None when the header is absent or unreadable."""
    raw = _header(headers, "retry-after")
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


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


def _photon_http(method, url, headers, body, timeout):
    """Same as _urllib_http, plus response headers, so a 429 Retry-After is visible."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except (urllib.error.URLError, OSError) as e:
        raise ProviderError(f"{url}: {getattr(e, 'reason', e)}")


def _payload(body):
    try:
        return json.loads(body or b"{}")
    except Exception:
        return {"raw": (body or b"")[:200].decode(errors="replace")}


def _problem_text(payload, status):
    msg = (payload.get("message") or payload.get("title") or payload.get("detail") or payload.get("error")
           or payload.get("raw") or f"HTTP {status}")
    return json.dumps(msg) if isinstance(msg, dict) else msg


def _ok(status, payload, what):
    if not 200 <= status < 300:
        raise ProviderError(f"{what}: {_problem_text(payload, status)} (HTTP {status})")


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
    """Photon 2.0 REST (api.photon.codes/v1/projects/{id}).

    platform "sms" (the default): create-or-get the recipient's User, create-or-get the direct Space, then send.
    platform "imessage": a shared line. SendMessageRequest still requires senderId, but that id is receivedBy.id
    on a message the person already sent — POST /users would try to create a user, which a shared line cannot
    use to open a chat. We reuse the direct space (a cache filled by inbound traffic, else GET /messages) and
    never POST /users or /spaces.
    """

    def __init__(self, api_key, project_id, from_number, http=None, base_url=PHOTON_BASE, platform="sms",
                 text_format="plain", timeout=20, sleep=None):
        self.api_key, self.project_id, self.from_number = api_key, project_id, from_number
        self.http, self.base_url, self.platform, self.text_format, self.timeout = http or _photon_http, base_url.rstrip("/"), platform, text_format, timeout
        self._users, self._spaces = {}, {}
        self._imessage_spaces = {}        # separate from _spaces so SMS create-or-get is unchanged
        self._line_sender_id = None       # pho_usr_ of the shared line, learned from receivedBy.id
        self._sleep = sleep or time.sleep

    def _call(self, method, path, body=None, idempotency=None):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        data = json.dumps(body).encode() if body is not None else None
        url = f"{self.base_url}/v1/projects/{self.project_id}{path}"
        for attempt in range(_RATE_LIMIT_TRIES):
            status, raw, resp_headers = _http_parts(self.http(method, url, headers, data, self.timeout))
            if status == 429 and attempt < _RATE_LIMIT_TRIES - 1:
                # Retry-After is seconds or an HTTP date. One second when Photon omits it.
                wait = _retry_after_seconds(resp_headers)
                self._sleep(1.0 if wait is None else wait)
                continue
            payload = _payload(raw)
            if status == 429:
                wait = _retry_after_seconds(resp_headers)
                raise ProviderError(f"photon: {_problem_text(payload, status)} (HTTP 429)", retry_after=wait)
            _ok(status, payload, "photon")
            return payload

    def user_id(self, handle):
        if handle not in self._users:
            self._users[handle] = self._call("POST", "/users", {"handle": handle, "platform": self.platform})["id"]
        return self._users[handle]

    def sender_id(self):
        return self.user_id(self.from_number)

    def space_id(self, handle):
        if handle not in self._spaces:
            key = "space-" + hashlib.sha256(f"{self.platform}|{self.from_number}|{handle}".encode()).hexdigest()[:32]
            body = {"participantIds": [self.sender_id(), self.user_id(handle)], "platform": self.platform}
            self._spaces[handle] = self._call("POST", "/spaces", body, idempotency=key)["id"]
        return self._spaces[handle]

    def remember_space(self, handle, space_id):
        """Keep the direct chat a person already opened, so a later send does not have to list messages."""
        key = _handle_key(handle)
        if key and space_id:
            self._imessage_spaces[key] = space_id

    def remember_line_sender(self, user_id):
        """senderId for the shared line. SendMessageRequest requires it; receivedBy.id on an inbound message is that user."""
        if user_id:
            self._line_sender_id = user_id

    def _absorb(self, msg, overwrite):
        """Cache space.id for sentBy.handle, and the line's user id from receivedBy.id. Direct chats only."""
        received = (msg.get("receivedBy") or {}).get("id")
        if received and not self._line_sender_id:
            self._line_sender_id = received
        space = msg.get("space") or {}
        kind = space.get("kind")
        if kind and kind != "direct":
            return
        sent = msg.get("sentBy") or {}
        if sent.get("kind") == "agent":
            return
        key = _handle_key(sent.get("handle"))
        sid = space.get("id")
        if key and sid and (overwrite or key not in self._imessage_spaces):
            self._imessage_spaces[key] = sid

    def _iter_messages(self, order, created_since=None, stop=None):
        """GET /messages?platform&direction=received&order&orderBy=created_at&pageSize&createdSince&pageToken."""
        token, seen_tokens, out = None, set(), []
        for _page in range(40):
            query = {"platform": self.platform, "direction": "received", "order": order,
                     "orderBy": "created_at", "pageSize": 50}
            if created_since:
                query["createdSince"] = created_since
            if token:
                query["pageToken"] = token
            page = self._call("GET", "/messages?" + urlencode(query))
            for msg in page.get("items") or []:
                if self.platform == "imessage":
                    # desc: the first hit is the newest, so do not let older pages replace it.
                    self._absorb(msg, overwrite=(order == "asc"))
                out.append(msg)
                if stop and stop():
                    return out
            nxt = page.get("nextPageToken")
            if not nxt or nxt in seen_tokens:
                return out
            seen_tokens.add(nxt)
            token = nxt
        return out

    def list_received(self, created_since=None):
        """Received messages, oldest first, so a poller can advance a createdSince cursor."""
        return self._iter_messages("asc", created_since=created_since)

    def imessage_space(self, phone):
        key = _handle_key(phone)
        if key and key in self._imessage_spaces:
            return self._imessage_spaces[key]
        # Newest first: the first direct chat whose sentBy.handle is this phone is the one to reuse.
        self._iter_messages("desc", stop=lambda: bool(key) and key in self._imessage_spaces and bool(self._line_sender_id))
        if not key or key not in self._imessage_spaces:
            raise ProviderError(IMESSAGE_NO_CHAT)
        return self._imessage_spaces[key]

    def _sender_id_for_send(self):
        if self.platform != "imessage":
            return self.sender_id()
        if not self._line_sender_id:
            self._iter_messages("desc", stop=lambda: bool(self._line_sender_id))
        if not self._line_sender_id:
            raise ProviderError("shared iMessage sender id is unknown: no received message included receivedBy.id yet")
        return self._line_sender_id

    def send_to_space(self, space_id, text):
        body = {"spaceId": space_id, "senderId": self._sender_id_for_send(),
                "content": {"type": "text", "format": self.text_format, "text": text}}
        payload = self._call("POST", "/messages", body, idempotency=str(uuid.uuid4()))
        return f"photon {payload.get('id', '?')} ({payload.get('status', 'accepted')})"

    def send(self, phone, text, language):
        if self.platform == "imessage":
            return self.send_to_space(self.imessage_space(phone), text)
        return self.send_to_space(self.space_id(phone), text)


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
    out = {"handle": handle, "text": content["text"], "space_id": space.get("id"), "message_id": msg.get("id"),
           "from_agent": sender.get("kind") == "agent"}
    received_id = (msg.get("receivedBy") or {}).get("id")
    if received_id:
        out["received_by_id"] = received_id       # the shared line's user id, when Photon includes it
    return out


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
        platform = (env.get("PHOTON_PLATFORM") or "sms").strip().lower()
        if platform not in ("sms", "imessage"):
            raise ProviderError(f"PHOTON_PLATFORM must be sms or imessage (got {platform})")
        return PhotonSMS(env["PHOTON_API_KEY"], env["PHOTON_PROJECT_ID"], env["PHOTON_FROM"], http=http,
                         base_url=env.get("PHOTON_BASE_URL") or PHOTON_BASE, platform=platform,
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
