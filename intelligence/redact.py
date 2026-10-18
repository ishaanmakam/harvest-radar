"""Strip phones, emails, API keys and roster-shaped rows before they are stored or posted."""
import re

_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_KEY = re.compile(r"(?i)\b(?:pho_sk_|sk-|xai-|Bearer\s+)[A-Za-z0-9_\-]{8,}")
_ASSIGNED = re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|authorization)\s*[:=]\s*\S+")
_ROSTER = re.compile(r"(?im)^(?:roster_id,.*|[A-Z]\d{3,},[^,\n]*,[^,\n]*,\+?\d{8,}.*)$")


def redact(text):
    if text is None:
        return text
    if not isinstance(text, str):
        text = str(text)
    text = _KEY.sub("[redacted]", text)
    text = _ASSIGNED.sub("[redacted]", text)
    text = _ROSTER.sub("[redacted]", text)
    text = _EMAIL.sub("[redacted]", text)
    return _PHONE.sub("[redacted]", text)
